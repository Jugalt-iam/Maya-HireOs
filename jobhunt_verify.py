#!/usr/bin/env python3
"""
jobhunt_verify.py  --  official-source verification.

Tier 0. No model. Deterministic domain/path matching against the brief's own
allow list and exclude list. This is a hard rule, not judgment: a portal URL
never becomes the source of truth for a job, no matter how confident a model
might sound about it. Shared by both Route 1 (Discovery) and Route 2
(Portal), so there is exactly one place this rule lives.

Standalone: no import from server.py.
"""

import re
from typing import Dict, Optional
from urllib.parse import urlparse

__all__ = ["OFFICIAL_ATS_DOMAINS", "PORTAL_DOMAINS", "classify_url", "is_official",
          "company_token_from_url"]

# domain suffix -> ats name. Matched against the URL's hostname, suffix-wise,
# so "boards.greenhouse.io" and "job-boards.greenhouse.io" both match.
OFFICIAL_ATS_DOMAINS: Dict[str, str] = {
    "greenhouse.io": "greenhouse",
    "lever.co": "lever",
    "ashbyhq.com": "ashby",
    "myworkdayjobs.com": "workday",
    "bamboohr.com": "bamboohr",
    "smartrecruiters.com": "smartrecruiters",
    "icims.com": "icims",
    "jobvite.com": "jobvite",
    "workable.com": "workable",
    "trakstar.com": "trakstar",
}

# Never the final source, per the brief -- discovery input only. Listed so
# the classifier can say explicitly "this is a portal", not just "unknown".
PORTAL_DOMAINS = {
    "linkedin.com", "naukri.com", "indeed.com", "foundit.in", "foundit.com",
    "glassdoor.com", "monster.com", "ziprecruiter.com", "simplyhired.com",
    "timesjobs.com", "shine.com", "instahyre.com", "cutshort.io",
}

_CAREERS_PATH_RE = re.compile(r"/(careers|jobs|join-us|work-with-us)(/|$)", re.IGNORECASE)


def _hostname(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _matches_suffix(hostname: str, domains) -> Optional[str]:
    for suffix in domains:
        if hostname == suffix or hostname.endswith("." + suffix):
            return suffix
    return None


def classify_url(url: str, company_domain: Optional[str] = None) -> Dict[str, object]:
    """Returns {source_type, ats, confidence, hostname}.

    source_type is one of OFFICIAL_ATS, COMPANY_SITE, PORTAL, UNKNOWN.
    UNKNOWN is the conservative default: a career site this classifier does
    not recognize is not treated as verified just because it looks plausible.
    """
    hostname = _hostname(url)
    path = urlparse(url).path or ""

    ats_suffix = _matches_suffix(hostname, OFFICIAL_ATS_DOMAINS)
    if ats_suffix:
        return {"source_type": "OFFICIAL_ATS", "ats": OFFICIAL_ATS_DOMAINS[ats_suffix],
               "confidence": "HIGH", "hostname": hostname}

    if company_domain:
        cd = company_domain.lower()
        if cd.startswith("www."):   # a prefix strip, not str.lstrip's character-set
            cd = cd[4:]              # strip (which mangled e.g. "webflow.com" -> "ebflow.com")
        if hostname == cd or hostname.endswith("." + cd) or hostname == "www." + cd:
            if _CAREERS_PATH_RE.search(path) or path in ("", "/"):
                return {"source_type": "COMPANY_SITE", "ats": None,
                       "confidence": "HIGH", "hostname": hostname}
            return {"source_type": "COMPANY_SITE", "ats": None,
                   "confidence": "LOW", "hostname": hostname}

    portal_suffix = _matches_suffix(hostname, PORTAL_DOMAINS)
    if portal_suffix:
        return {"source_type": "PORTAL", "ats": None, "confidence": "HIGH",
               "hostname": hostname}

    return {"source_type": "UNKNOWN", "ats": None, "confidence": "LOW",
           "hostname": hostname}


def is_official(classification: Dict[str, object]) -> bool:
    return classification.get("source_type") in ("OFFICIAL_ATS", "COMPANY_SITE")


def company_token_from_url(url: str) -> Optional[str]:
    """The company token embedded in a known ATS URL's own path, e.g.
    "stripe" from boards.greenhouse.io/stripe/jobs/123. None if the host
    isn't a recognized ATS or the path has no token segment.

    A shared ATS hostname is never a company name on its own -- every
    employer on Greenhouse shares boards.greenhouse.io. This is a second,
    much better source than the bare hostname for the (still imperfect,
    still a fallback) case where a job page's JSON-LD doesn't carry a real
    hiringOrganization name.
    """
    hostname = _hostname(url)
    if not _matches_suffix(hostname, OFFICIAL_ATS_DOMAINS):
        return None
    try:
        parts = [p for p in urlparse(url).path.split("/") if p]
    except ValueError:
        return None
    return parts[0] if parts else None
