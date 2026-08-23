#!/usr/bin/env python3
"""
jobhunt_search.py  --  Skill 1, Discovery: the search layer.

Headless Chrome (Playwright) is the primary and only required search
mechanism, by design: free forever, no key, no quota ceiling, not a
fallback behind any paid-adjacent API. First-party ATS JSON
feeds (Greenhouse/Lever/Ashby) are a separate, secondary path used only to
enrich/verify postings for companies already known -- they do not search for
new companies, and they are not what "primary" refers to here.

Real Playwright automation only runs where Playwright is actually installed
and a browser is available, which on this project is the host machine, never
this Mac (see TESTS_JOBHUNT.md). Importing this module never requires
Playwright to be installed -- the import is deferred into PlaywrightDriver's
own methods, the same optional-dependency pattern this project already uses
for numpy and pypdf. Everything else here (robots.txt checking, ATS feed
parsing, freshness math, query generation from stored role permutations) is
plain Tier 0 code, fully testable with a stub driver and no browser at all.

Respects robots.txt on every host it touches. Never solves or bypasses a
CAPTCHA. Never retries aggressively against a host that is signalling a
block. Reports SEARCH_SUCCESS / SEARCH_PARTIAL / SEARCH_BLOCKED /
SEARCH_FAILED honestly -- never a fabricated result.
"""

import json
import re
import time
import urllib.robotparser
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus, urlparse

import jobhunt_security as security
import jobhunt_verify as verify

__all__ = [
    "SEARCH_SUCCESS", "SEARCH_PARTIAL", "SEARCH_BLOCKED", "SEARCH_FAILED",
    "MAX_JOB_AGE_DAYS", "SearchOutcome", "PlaywrightDriver",
    "robots_allow", "search_web", "find_engagement_posts",
    "discover_ats_board_for_company",
    "fetch_greenhouse_postings",
    "fetch_lever_postings", "fetch_ashby_postings",
    "compute_age_days", "is_fresh", "build_queries_from_permutations",
]

SEARCH_SUCCESS = "SEARCH_SUCCESS"
SEARCH_PARTIAL = "SEARCH_PARTIAL"
SEARCH_BLOCKED = "SEARCH_BLOCKED"
SEARCH_FAILED = "SEARCH_FAILED"

MAX_JOB_AGE_DAYS = 7   # configurable, per the brief -- not hardcoded elsewhere

DUCKDUCKGO_HTML = "https://html.duckduckgo.com/html/"
USER_AGENT = security.USER_AGENT

_ROBOTS_CACHE: Dict[str, urllib.robotparser.RobotFileParser] = {}


def robots_allow(url: str, user_agent: str = USER_AGENT) -> bool:
    """True unless robots.txt explicitly disallows this path for us. Fails
    open only on a genuine fetch error (no robots.txt found is not a
    disallow); fails closed (not allowed) the moment robots.txt says so."""
    parsed = urlparse(url)
    origin = "%s://%s" % (parsed.scheme, parsed.netloc)
    rp = _ROBOTS_CACHE.get(origin)
    if rp is None:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(origin + "/robots.txt")
        try:
            fetched = security.safe_fetch(origin + "/robots.txt", timeout=8)
            if fetched["ok"]:
                rp.parse(fetched["text"].splitlines())
            else:
                rp.parse([])   # no robots.txt reachable -- treat as no rules
        except security.SecurityError:
            rp.parse([])
        _ROBOTS_CACHE[origin] = rp
    try:
        return rp.can_fetch(user_agent, url)
    except Exception:
        return True


class SearchOutcome:
    def __init__(self, state: str, results: Optional[List[Dict[str, str]]] = None,
                detail: str = ""):
        self.state = state
        self.results = results or []
        self.detail = detail

    def to_dict(self) -> Dict[str, Any]:
        return {"state": self.state, "results": self.results, "detail": self.detail}


class PlaywrightDriver:
    """The real, host-only search mechanism. Playwright is imported lazily
    inside search(), never at module import time, so this file loads fine
    on a machine (like the one this was written on) that has never run
    `pip install playwright`."""

    def __init__(self, headless: bool = True, timeout_ms: int = 20000):
        self.headless = headless
        self.timeout_ms = timeout_ms

    def search(self, query: str, max_results: int = 10) -> SearchOutcome:
        url = "%s?q=%s" % (DUCKDUCKGO_HTML, quote_plus(query))
        if not robots_allow(url):
            return SearchOutcome(SEARCH_BLOCKED, detail="disallowed by robots.txt: %s" % url)

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return SearchOutcome(
                SEARCH_FAILED,
                detail="playwright is not installed on this machine. Run "
                      "`pip install playwright && playwright install chromium` "
                      "on the host that runs Discovery -- never on the "
                      "build machine.")

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=self.headless)
                try:
                    page = browser.new_page(user_agent=USER_AGENT)
                    page.goto(url, timeout=self.timeout_ms, wait_until="domcontentloaded")
                    # A CAPTCHA/verification interstitial is a block, not a
                    # zero-result search. Detected by absence of the normal
                    # results container rather than parsed as "no jobs found".
                    if page.query_selector("#anomaly-modal, .anomaly-modal__title"):
                        return SearchOutcome(SEARCH_BLOCKED,
                                            detail="anti-bot interstitial shown")
                    links = page.query_selector_all("a.result__a")
                    results = []
                    for link in links[:max_results]:
                        href = link.get_attribute("href") or ""
                        title = (link.inner_text() or "").strip()
                        if href and title:
                            results.append({"title": title, "url": href})
                    if not results:
                        return SearchOutcome(SEARCH_BLOCKED,
                                            detail="no result markup found -- "
                                                  "likely a block or layout change")
                    return SearchOutcome(SEARCH_SUCCESS, results)
                finally:
                    browser.close()
        except Exception as exc:
            return SearchOutcome(SEARCH_FAILED, detail=str(exc)[:200])


def search_web(query: str, driver=None, max_results: int = 10) -> SearchOutcome:
    """The one entry point callers use. driver defaults to PlaywrightDriver
    but accepts any object with a matching .search(query, max_results)
    method, which is how this stays unit-testable without a browser."""
    driver = driver or PlaywrightDriver()
    return driver.search(query, max_results)


def find_engagement_posts(company_names: List[str], max_results: int = 5,
                          driver=None) -> Dict[str, Any]:
    """Read-only discovery of public LinkedIn posts worth a comment, one
    search per company name given. Finds candidates only -- nothing here
    fetches or scrapes a LinkedIn page itself, and nothing here or in any
    caller posts, comments, or follows anything. That stays a manual
    action for whoever reviews the returned list.

    Reuses search_web()/robots_allow() exactly as Discovery's own job
    search already does, including the same honest SEARCH_SUCCESS/PARTIAL/
    BLOCKED/FAILED states. LinkedIn's robots.txt is one of the strictest of
    any major site, so SEARCH_BLOCKED here is a real, expected, honestly-
    reported outcome for some or all companies, not a bug to route around.

    Only title and url are returned per candidate -- the DuckDuckGo HTML
    scraper in PlaywrightDriver.search() does not currently extract a
    result snippet, and extending its DOM parsing is not something that can
    be verified from a machine with no way to run Playwright against the
    live page; left for a follow-up that can actually be tested against
    the real DOM on the host.
    """
    candidates: List[Dict[str, str]] = []
    states: Dict[str, str] = {}
    for name in company_names:
        name = (name or "").strip()
        if not name:
            continue
        query = 'site:linkedin.com/posts "%s"' % name
        outcome = search_web(query, driver=driver, max_results=max_results)
        states[name] = outcome.state
        if outcome.state not in (SEARCH_SUCCESS, SEARCH_PARTIAL):
            continue
        for r in outcome.results:
            url = r.get("url", "")
            if "linkedin.com" not in url:
                continue   # the query's own site: restriction wasn't honored -- skip it
            candidates.append({"title": r.get("title", ""), "url": url, "company": name})
    return {"candidates": candidates, "states": states}


def discover_ats_board_for_company(domain: str) -> Optional[Dict[str, str]]:
    """Checks whether a company's own domain redirects straight to a known
    ATS board -- most companies on Greenhouse/Lever/Ashby point their own
    /careers page directly at it. No search engine, no browser, no anti-bot
    surface at all: this is one plain HTTP fetch of the company's own
    domain through the same SSRF-safe fetcher every other module uses,
    following redirects honestly (safe_fetch re-validates every hop) and
    checking only where they actually land.

    Returns {"ats": ..., "token": ...} on a genuine redirect match, never a
    guess -- the token always comes from a URL the company's own site
    actually sent the request to, the same way a human clicking their
    careers link would land there. None if nothing matched.
    """
    if not domain:
        return None
    for path in ("/careers", "/jobs", ""):
        url = "https://%s%s" % (domain, path)
        try:
            resp = security.safe_fetch(url, timeout=10)
        except security.SecurityError:
            continue
        if not resp.get("ok"):
            continue
        final_url = resp.get("url") or url
        classification = verify.classify_url(final_url)
        if classification.get("source_type") == "OFFICIAL_ATS":
            token = verify.company_token_from_url(final_url)
            if token:
                return {"ats": classification["ats"], "token": token}
    return None


# --------------------------------------------------- ATS feeds (secondary) --

def _ats_fetch_json(url: str) -> Dict[str, Any]:
    try:
        resp = security.safe_fetch(url, timeout=15)
    except security.SecurityError as exc:
        return {"ok": False, "error": str(exc)}
    if not resp["ok"]:
        return {"ok": False, "error": resp["error"]}
    try:
        return {"ok": True, "data": json.loads(resp["text"])}
    except (json.JSONDecodeError, ValueError) as exc:
        return {"ok": False, "error": "bad json: %s" % exc}


def fetch_greenhouse_postings(company_token: str) -> Dict[str, Any]:
    url = "https://boards-api.greenhouse.io/v1/boards/%s/jobs?content=true" % company_token
    result = _ats_fetch_json(url)
    if not result["ok"]:
        return result
    jobs = result["data"].get("jobs", []) if isinstance(result["data"], dict) else []
    postings = [{"title": j.get("title"), "official_url": j.get("absolute_url"),
                "posted_at": j.get("updated_at"), "ats_job_id": str(j.get("id", "")),
                "location": (j.get("location") or {}).get("name"),
                "description": j.get("content")} for j in jobs]
    return {"ok": True, "postings": postings, "ats": "greenhouse"}


def fetch_lever_postings(company_token: str) -> Dict[str, Any]:
    url = "https://api.lever.co/v1/postings/%s?mode=json" % company_token
    result = _ats_fetch_json(url)
    if not result["ok"]:
        return result
    jobs = result["data"] if isinstance(result["data"], list) else []
    postings = [{"title": j.get("text"), "official_url": j.get("hostedUrl"),
                "posted_at": j.get("createdAt"), "ats_job_id": str(j.get("id", "")),
                "location": (j.get("categories") or {}).get("location"),
                "description": j.get("descriptionPlain") or j.get("description")}
               for j in jobs]
    return {"ok": True, "postings": postings, "ats": "lever"}


def fetch_ashby_postings(company_token: str) -> Dict[str, Any]:
    url = "https://api.ashbyhq.com/posting-api/job-board/%s" % company_token
    result = _ats_fetch_json(url)
    if not result["ok"]:
        return result
    jobs = result["data"].get("jobs", []) if isinstance(result["data"], dict) else []
    postings = [{"title": j.get("title"), "official_url": j.get("jobUrl"),
                "posted_at": j.get("publishedAt"), "ats_job_id": str(j.get("id", "")),
                "location": j.get("location"), "description": j.get("descriptionPlain")}
               for j in jobs]
    return {"ok": True, "postings": postings, "ats": "ashby"}


# ------------------------------------------------------------- freshness ---

def compute_age_days(posted_at_iso: Optional[Any]) -> Optional[int]:
    """Accepts either an ISO-8601 string (Greenhouse's updated_at, Ashby's
    publishedAt) or a Unix epoch timestamp in seconds or milliseconds
    (Lever's createdAt is epoch milliseconds, an int, not a string) --
    treating a Lever timestamp as an unparseable ISO string used to make
    every Lever posting silently look UNKNOWN-age and get filtered out as
    not fresh, which would have made wiring Lever's feed in do nothing.
    """
    if posted_at_iso is None or posted_at_iso == "":
        return None
    if isinstance(posted_at_iso, (int, float)):
        try:
            seconds = posted_at_iso / 1000.0 if posted_at_iso > 10_000_000_000 else posted_at_iso
            dt = datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    else:
        s = str(posted_at_iso).strip()
        if s.isdigit():
            return compute_age_days(int(s))
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    delta = datetime.now(timezone.utc) - dt
    return max(0, delta.days)


def is_fresh(age_days: Optional[int], max_age_days: int = MAX_JOB_AGE_DAYS) -> bool:
    return age_days is not None and age_days <= max_age_days


# -------------------------------------------------- query generation -------

def build_queries_from_permutations(rows: List[Dict[str, Any]],
                                    locations: Optional[List[str]] = None
                                    ) -> List[str]:
    """Deterministic combination, Tier 0. The judgment step (deciding which
    role permutations exist at all) already happened when the permutation
    workbook was generated; combining title x location here needs no model."""
    locations = locations or [""]
    queries: List[str] = []
    seen = set()
    for row in rows:
        designation = (row.get("designation") or row.get("canonical_role") or "").strip()
        if not designation:
            continue
        if (row.get("include_exclude") or "").strip().lower() == "exclude":
            continue
        for loc in locations:
            q = ("%s %s careers" % (designation, loc)).strip()
            q = re.sub(r"\s+", " ", q)
            if q.lower() not in seen:
                seen.add(q.lower())
                queries.append(q)
    return queries
