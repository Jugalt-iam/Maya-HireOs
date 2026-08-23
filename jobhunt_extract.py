#!/usr/bin/env python3
"""
jobhunt_extract.py  --  Skill 1, Discovery: extraction and date verification.

Tier 0, no model. Fetches a verified job page through the shared SSRF-safe
fetcher (jobhunt_security.safe_fetch, never a raw requests/Playwright call
against untrusted input) and pulls the job description and posting date out
of it using stdlib html.parser -- the same hand-rolled-over-BeautifulSoup
posture server.py already uses for .docx/.pptx/.xlsx.

Never trusts a search snippet's date. JSON-LD JobPosting structured data is
HIGH confidence; a visible date string on the page is LOW; neither present
is UNKNOWN, and UNKNOWN is never quietly treated as recent.
"""

import json
import re
from html import unescape as unescape_html_entities
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional

import jobhunt_security as security

__all__ = ["extract_job_page", "extract_jsonld_jobposting", "strip_html_to_text"]

_SKIP_TAGS = {"script", "style", "noscript", "svg", "head"}


class _TextExtractor(HTMLParser):
    """Pulls visible text and every JSON-LD script block out of an HTML
    document in one pass, without a parser dependency."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._in_jsonld = False
        self._jsonld_buf: List[str] = []
        self.jsonld_blocks: List[str] = []
        self.text_parts: List[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        if tag == "script":
            attr_dict = dict(attrs)
            if (attr_dict.get("type") or "").lower() == "application/ld+json":
                self._in_jsonld = True
                self._jsonld_buf = []

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag == "script" and self._in_jsonld:
            self.jsonld_blocks.append("".join(self._jsonld_buf))
            self._in_jsonld = False

    def handle_data(self, data):
        if self._in_jsonld:
            self._jsonld_buf.append(data)
            return
        if self._skip_depth == 0 and data.strip():
            self.text_parts.append(data.strip())


def strip_html_to_text(html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:
        pass
    text = " ".join(parser.text_parts)
    return re.sub(r"\s+", " ", text).strip()


def _jsonld_blocks(html: str) -> List[str]:
    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:
        pass
    return parser.jsonld_blocks


def _escape_control_chars_in_strings(raw: str) -> str:
    """Some ATS platforms build their JSON-LD by dropping a rich-text
    description straight into a JSON string template without escaping it --
    a real posting seen in production (Trakstar Hire) has a literal newline
    sitting inside a "description" string value, which is invalid JSON and
    makes json.loads() reject an otherwise well-formed JobPosting block
    outright, even though every field in it is fine. This walks the text
    once, tracking whether each character is inside a quoted string (respecting
    backslash escapes), and only escapes a raw newline/carriage-return/tab
    when it is actually inside a string -- pretty-printing whitespace between
    tokens, which is normal and common, is left untouched."""
    out = []
    in_string = False
    escape_next = False
    for ch in raw:
        if in_string:
            if escape_next:
                out.append(ch)
                escape_next = False
            elif ch == "\\":
                out.append(ch)
                escape_next = True
            elif ch == '"':
                in_string = False
                out.append(ch)
            elif ch == "\n":
                out.append("\\n")
            elif ch == "\r":
                out.append("\\r")
            elif ch == "\t":
                out.append("\\t")
            else:
                out.append(ch)
        else:
            if ch == '"':
                in_string = True
            out.append(ch)
    return "".join(out)


def extract_jsonld_jobposting(html: str) -> Optional[Dict[str, Any]]:
    """Returns the first JobPosting object found in the page's JSON-LD, or
    None. Handles both a single object and an @graph/array wrapper, which
    real ATS pages use inconsistently. Falls back to a control-character-
    escaped reparse before giving up on a block that fails to parse --
    see _escape_control_chars_in_strings()."""
    for block in _jsonld_blocks(html):
        try:
            data = json.loads(block)
        except (json.JSONDecodeError, ValueError):
            try:
                data = json.loads(_escape_control_chars_in_strings(block))
            except (json.JSONDecodeError, ValueError):
                continue
        candidates = data if isinstance(data, list) else [data]
        for item in candidates:
            if not isinstance(item, dict):
                continue
            graph = item.get("@graph")
            pool = graph if isinstance(graph, list) else [item]
            for node in pool:
                if isinstance(node, dict) and node.get("@type") == "JobPosting":
                    return node
    return None


_VISIBLE_DATE_RE = re.compile(
    r"\b(posted|updated)\s*(on)?\s*[:\-]?\s*"
    r"(\d{4}-\d{2}-\d{2}|[A-Za-z]+\s+\d{1,2},?\s+\d{4})", re.IGNORECASE)


def extract_job_page(url: str, company_domain: Optional[str] = None
                     ) -> Dict[str, Any]:
    """One network call, through the shared SSRF guard. Returns:
      {ok, title, description, posted_at, date_confidence, company_name,
       raw_html_len, error}
    Never raises on a normal fetch failure -- that is EXTRACTION_FAILED, an
    honesty gate the caller reports, not an exception it has to catch.
    """
    try:
        resp = security.safe_fetch(url)
    except security.SecurityError as exc:
        return {"ok": False, "error": "blocked: %s" % exc}
    if not resp["ok"]:
        return {"ok": False, "error": resp["error"]}

    html = resp["text"]
    posting = extract_jsonld_jobposting(html)
    posted_at = None
    date_confidence = "UNKNOWN"
    title = None
    description = None
    company_name = None

    if posting:
        posted_at = posting.get("datePosted")
        raw_title = posting.get("title")
        if isinstance(raw_title, str) and raw_title.strip():
            # Always unescape, never conditionally on "does this look like it
            # has entities" -- a title can carry a bare &amp; with no other
            # markup around it, and an unconditional unescape is a safe no-op
            # on plain text that has none.
            title = unescape_html_entities(raw_title).strip()
        desc = posting.get("description")
        if isinstance(desc, str) and desc.strip():
            # Some ATS platforms (Trakstar Hire, seen in production)
            # HTML-entity-escape their entire JSON-LD description, so the
            # string holds "&lt;p&gt;" rather than "<p>" -- a plain "<" in
            # desc check never fires, and the real markup and its own
            # entities (e.g. an actual &nbsp; inside the source HTML) end up
            # double-escaped ("&amp;nbsp;"), which a single decode pass does
            # not fully resolve: the outer unescape recovers the real "<p>"
            # tags and turns "&amp;nbsp;" into "&nbsp;", but that inner
            # entity only becomes a "&...;"-shaped string *after* that first
            # decode, so it needs strip_html_to_text()'s own parse-time
            # decode (a second, HTML-aware pass) to resolve fully. Both
            # calls are safe no-ops on description text that was never
            # escaped at all.
            description = strip_html_to_text(unescape_html_entities(desc))
        if posted_at:
            date_confidence = "HIGH"
        # schema.org allows hiringOrganization as either an Organization
        # object ({"name": "..."}) or a bare string -- a shared ATS hostname
        # (every Greenhouse customer shares boards.greenhouse.io) is never a
        # real company name, this is the actual source of truth when present.
        org = posting.get("hiringOrganization")
        if isinstance(org, dict):
            name = org.get("name")
            if isinstance(name, str) and name.strip():
                company_name = unescape_html_entities(name).strip()
        elif isinstance(org, str) and org.strip():
            company_name = unescape_html_entities(org).strip()

    text = strip_html_to_text(html)
    if not description:
        description = text[:8000]
    if not posted_at:
        m = _VISIBLE_DATE_RE.search(text)
        if m:
            posted_at = m.group(3)
            date_confidence = "LOW"

    return {"ok": True, "title": title, "description": description,
           "posted_at": posted_at, "date_confidence": date_confidence,
           "company_name": company_name,
           "raw_html_len": len(html), "error": ""}
