# Job Hunt OS — Implementation Report

Built on top of the existing Maya_OS architecture, per the approved plan.
Nothing in `lanes.py`, the chat UI, or the existing
`fit`/`research`/`copy`/`campaign` modes was rewritten — all new work is
additive.

## What was implemented

**Structured data layer** — `jobhunt_db.py`. SQLite (stdlib, no new
dependency), 16 tables covering the full brief: companies, jobs, contacts,
opportunities, fit_checks, resume_versions, outreach_plans, messages,
conversations, tasks, followups, status_history, search_queries,
discovery_runs, role_permutations, audit_log. Human-readable sequential IDs
(`OPP-000123` etc.), multi-signal dedup, append-only status/conversation
history.

**Security layer** — `jobhunt_security.py`. Every external fetch in the
system routes through `safe_fetch()`: scheme allowlist, DNS-resolved
private/loopback/link-local IP rejection, per-redirect-hop re-validation,
size/timeout caps. `safe_join()` blocks path traversal on every file write.
`redact()` scrubs key-shaped strings before logging.

**Eight skills:**
1. **Discovery** — `jobhunt_search.py`, `jobhunt_verify.py`,
   `jobhunt_extract.py`. Headless Chrome (Playwright) is the primary,
   required search mechanism against DuckDuckGo's HTML endpoint, respecting
   `robots.txt`, never bypassing CAPTCHAs. Greenhouse/Lever/Ashby public JSON
   APIs are a secondary, zero-risk enrichment path. Official-source
   verification (allow-list vs. portal exclude-list), JSON-LD-first posting
   date extraction, 7-day freshness filter — all configurable, all Tier 0.
2. **Fit Check** — `jobhunt_fit.py`. The four-part narrative from
   `job_search_adapter.md` is unchanged; a 0-100 score is computed in code
   from the model's evidence classification (never asserted by the model
   directly), with a mandatory-gap cap so a missing hard requirement can't be
   outscored by everything else being strong.
3. **Resume Building** — `jobhunt_resume.py`. Immutable master, versioned
   tailored copies, and a real Tier 0 fabrication check that flags any
   number or named tool/product in a draft that isn't in the master resume.
4. **Company Deep Dive** — extends the `research` mode; writes both a DB row
   and a `MyData/jobhunt/company_notes/*.md` file, and journals the turn so
   it's recallable through ordinary chat, not just the dashboard.
5. **Outreach Plan** — `jobhunt_outreach.py`, pure Tier 0 sequencing
   (email → LinkedIn → email, or a single warm-intro step for referrals).
6. **Messaging** — extends the existing `copy` mode (already loads
   `job_search_adapter.md`'s voice rules).
7. **Tracker/Status** — `jobhunt_excel.py`, all 20 sheets, regenerated
   one-way from the database. Import is report-only (v2 stretch goal for
   real two-way sync, stated as such, not implemented as if it were done).
8. **Daily Control** — `jobhunt_daily.py`, pure aggregation, no model.

**Four routes**, all converging on one `opportunities` table with a
preserved route history: Discovery (`/v1/jobhunt/discovery/run`), Portal
(`/v1/jobhunt/opportunities/from-portal`, dedupes against Discovery),
Inbound (`/v1/jobhunt/opportunities/from-inbound`, captures even with no
lane configured), Conversation (`/v1/jobhunt/conversations`, append-only).

**UI** — `ui/jobs.html`, served at `GET /jobs`. All 14 sections from the
brief, same dark-glass aesthetic as the existing chat page, zero build step,
binds to the `/v1/jobhunt/*` endpoints.

**New `jobs/SKILL.md`** documents the mechanics (status enum, ID formats,
source verification rules, fit-score weighting, the prompt-injection rule
for scraped content). **One documented edit** to `systems/job_search_adapter.md`:
the "not a score out of ten" line now explains the score is visible
everywhere per your explicit decision, without changing the four-part
narrative itself.

## What was tested, and where

**On this Mac (build machine only, per your instruction):**
- Every `jobhunt_*.py` module's pure logic: dedup, ID sequencing, scoring
  arithmetic (including the mandatory-gap cap), fabrication flagging,
  freshness math, query generation, robots.txt parsing logic, SSRF/path-
  traversal guards against adversarial input.
- One real bug caught and fixed: `IPv4Address` has no `is_site_local`
  attribute — an early version of the SSRF guard would have thrown on every
  legitimate public URL, not just malicious ones. Caught before it shipped.
- One real bug caught and fixed: the resume-tailoring endpoint's lane
  callback had the wrong function signature and would have failed on first
  real use. Caught before it shipped.
- Every new endpoint's request/response shape and auth handling, run through
  the actual FastAPI app (not a mock), including honest-degradation paths
  (no lane configured, no master resume, unknown opportunity ID).
- A small number of genuinely free, real network calls during development
  (public Greenhouse/Lever APIs, robots.txt fetches) confirmed those
  integrations work against live data — stopped doing this once you flagged
  it, since host-machine testing is where that belongs going forward.
- All test-generated data (`MyData/`, `queue/`, `.claude_index/`, logs) has
  been deleted from this checkout. Nothing fabricated ships as if it were
  real.

**Not testable here, by design — needs the host:** live headless-browser
search against DuckDuckGo (Playwright was briefly, mistakenly installed on
this Mac to test import structure, then fully uninstalled once you flagged
it — nothing from that remains), fit-checking against your actual resume,
overnight/long-running discovery behavior, and the security checks run
against a live instance rather than as isolated unit tests. `TESTS_JOBHUNT.md`
is the runbook for all of this, in the same numbered format as the existing
`TESTS.md`.

## Limitations, stated plainly

- Discovery's real-world coverage depends on DuckDuckGo continuing to serve
  the non-JS HTML endpoint without blocking automated traffic — if it
  starts blocking, the system reports `SEARCH_BLOCKED` honestly rather than
  degrading silently, but coverage on any given day may be thin for
  companies without a known Greenhouse/Lever/Ashby board.
- Two-way Excel sync (hand-edit the workbook, have it write back) is not
  built — import is report-only, as scoped from the start.
- No `MyData/` content, `.env`, or master resume exists in this checkout, so
  nothing here has been run against your real data. That happens on the
  host.

## Dependencies and cost

Full list with licenses in `DEPENDENCIES.md`. Three new packages:
`openpyxl` (tracker writing), `requests` (now listed explicitly — it was
already a hard, undeclared dependency), `playwright` (headless-Chrome
search). **Confirmed: no paid API, no paid service, and no usage-billed
provider is required anywhere in the core system.** The one optional
external service (Tavily) is off by default, not wired up, and the system
is fully functional without it, permanently, not just at first run.

## To actually use this

1. On the **host** machine: `pip install -r requirements.txt`, then
   `playwright install chromium`.
2. Add at least one LLM lane key to `.env` (copy `.env.example`) for the
   judgment-based skills (fit, research, resume, outreach copy). The
   deterministic endpoints work with zero keys.
3. Place your real master resume at `MyData/jobhunt/resumes/master.md`.
4. Run `python server.py`, then work through `TESTS_JOBHUNT.md` in order.
5. Dashboard at `http://<host>:8000/jobs`, alongside the existing chat at
   `/ui`.
