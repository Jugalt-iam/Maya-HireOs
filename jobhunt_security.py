#!/usr/bin/env python3
"""
jobhunt_security.py  --  the one place new code is allowed to touch the
network with a URL that came from search results, a pasted link, or any
other untrusted input, and the one place new code resolves a filesystem path
that came from a request.

Tier 0. No model calls. Standalone, like lanes.py and jobhunt_db.py: no
imports from server.py, so it is easy to unit test on its own and easy to
reason about in isolation.

Three things live here:
  * safe_fetch()   -- an SSRF-guarded HTTP GET/HEAD.
  * safe_join()     -- a path-traversal-guarded filesystem join.
  * redact()         -- scrubs key-shaped strings before they reach a log line.

Honest limit, stated rather than implied: safe_fetch() resolves and validates
the hostname before connecting and re-validates on every redirect hop, which
stops the realistic threat here (a scraped job page linking to, or redirecting
to, an internal address). It does not pin the connection to the resolved IP at
the socket level, so it is not a defense against a timed DNS-rebinding attack
that changes what a hostname resolves to between the check and the connect.
That is a real, narrower gap than "fully SSRF-proof," and it is written down
here rather than left implicit.
"""

import ipaddress
import re
import socket
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import requests

__all__ = ["SecurityError", "SSRFBlocked", "PathTraversal",
           "safe_fetch", "safe_join", "redact", "is_public_host"]

ALLOWED_SCHEMES = {"http", "https"}
MAX_REDIRECTS = 5
DEFAULT_TIMEOUT = 15
MAX_BYTES = 8_000_000        # a job/company page over 8MB is not a job page
USER_AGENT = "MayaJobHuntOS/1.0 (+local personal use; respects robots.txt)"


class SecurityError(Exception):
    pass


class SSRFBlocked(SecurityError):
    pass


class PathTraversal(SecurityError):
    pass


# ------------------------------------------------------------- SSRF guard --

def _is_disallowed_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True     # unparsable is untrusted
    # is_private already covers RFC1918 (IPv4) and unique-local (IPv6 fc00::/7).
    # is_site_local exists only on IPv6Address (and is long deprecated there),
    # so it is checked separately rather than unconditionally, which would
    # raise AttributeError on every ordinary IPv4Address, public or not.
    disallowed = (ip.is_private or ip.is_loopback or ip.is_link_local
                 or ip.is_reserved or ip.is_multicast or ip.is_unspecified)
    if isinstance(ip, ipaddress.IPv6Address):
        disallowed = disallowed or ip.is_site_local
    return disallowed


def is_public_host(hostname: str) -> bool:
    """True only if every address this hostname resolves to is public.
    One private address anywhere in the answer set fails the whole host,
    since an attacker only needs one usable route in."""
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return False
    if not infos:
        return False
    for info in infos:
        addr = info[4][0]
        if _is_disallowed_ip(addr):
            return False
    return True


def _validate_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise SSRFBlocked("scheme not allowed: %s" % (parsed.scheme or "<none>"))
    if not parsed.hostname:
        raise SSRFBlocked("no hostname in url")
    if not is_public_host(parsed.hostname):
        raise SSRFBlocked("hostname resolves to a non-public address: %s"
                          % parsed.hostname)
    return url


def safe_fetch(url: str, method: str = "GET",
               timeout: int = DEFAULT_TIMEOUT,
               max_bytes: int = MAX_BYTES,
               headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """GET (or HEAD) a URL, manually walking redirects so every hop is
    re-validated before it is followed. Never raises for a normal HTTP
    failure (404, timeout, connection refused) -- those come back as
    ok=False with a reason, same honesty-gate shape as the rest of this
    project. Only raises SSRFBlocked/SecurityError for a blocked target,
    since that is a security decision, not a normal failure to degrade past.
    """
    current = url
    hdrs = {"User-Agent": USER_AGENT}
    hdrs.update(headers or {})

    for _ in range(MAX_REDIRECTS + 1):
        _validate_url(current)
        try:
            resp = requests.request(
                method, current, headers=hdrs, timeout=timeout,
                allow_redirects=False, stream=True)
        except requests.exceptions.RequestException as exc:
            return {"ok": False, "status": None, "url": current,
                    "text": "", "error": str(exc)[:200]}

        if resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("Location")
            resp.close()
            if not location:
                return {"ok": False, "status": resp.status_code, "url": current,
                        "text": "", "error": "redirect with no Location header"}
            current = requests.compat.urljoin(current, location)
            continue

        if resp.status_code != 200:
            resp.close()
            return {"ok": False, "status": resp.status_code, "url": current,
                    "text": "", "error": "HTTP %d" % resp.status_code}

        chunks = []
        total = 0
        try:
            for chunk in resp.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    resp.close()
                    return {"ok": False, "status": 200, "url": current, "text": "",
                            "error": "response exceeded %d bytes, stopped reading"
                                    % max_bytes}
                chunks.append(chunk)
        finally:
            resp.close()

        body = b"".join(chunks)
        encoding = resp.encoding or "utf-8"
        try:
            text = body.decode(encoding, errors="replace")
        except (LookupError, UnicodeDecodeError):
            text = body.decode("utf-8", errors="replace")
        return {"ok": True, "status": 200, "url": current, "text": text,
                "error": "", "content_type": resp.headers.get("Content-Type", "")}

    return {"ok": False, "status": None, "url": current, "text": "",
            "error": "too many redirects (%d)" % MAX_REDIRECTS}


# ---------------------------------------------------------- path safety ---

def safe_join(root: Path, *parts: str) -> Path:
    """Resolve parts against root and reject anything that escapes it.

    Blocks '..' traversal, absolute-path injection (an absolute part still
    gets joined under root, never replaces it, matching Path's own footgun),
    and symlink escapes by resolving before comparing.
    """
    root = Path(root).resolve()
    candidate = root
    for part in parts:
        # Path(root, "/etc/passwd") would silently discard root in stdlib
        # Path joining; strip any leading path separators from each part so
        # every part is genuinely relative.
        cleaned = str(part).replace("\\", "/").lstrip("/")
        if not cleaned:
            continue
        candidate = candidate / cleaned
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise PathTraversal("path escapes root: %s" % candidate)
    return resolved


# -------------------------------------------------------------- redaction --

_KEY_PATTERNS = [
    re.compile(r"(sk-[A-Za-z0-9_\-]{10,})"),
    re.compile(r"(Bearer\s+)([A-Za-z0-9_\-\.]{10,})", re.IGNORECASE),
    re.compile(r"((?:api[_-]?key|token|secret|password)\s*[:=]\s*)([^\s\"',]{6,})",
              re.IGNORECASE),
]


def redact(text: str) -> str:
    """Scrub anything key-shaped before it reaches a log line or a report.
    Second layer of defense -- the first is simply never logging a raw
    credential in the first place."""
    out = text or ""
    for pat in _KEY_PATTERNS:
        if pat.groups == 2:
            out = pat.sub(lambda m: m.group(1) + "[redacted]", out)
        else:
            out = pat.sub("[redacted]", out)
    return out
