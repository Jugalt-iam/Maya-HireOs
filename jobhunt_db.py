#!/usr/bin/env python3
"""
jobhunt_db.py  --  the structured Job Hunt data layer.

Tier 0. No model calls anywhere in this file, on purpose (ROUTING.md: parsing,
dedupe, score arithmetic, schema validation are code, never a lane).

Standalone by design, same posture as lanes.py: this file knows nothing about
modes, routing or the FastAPI app. server.py imports it and calls it. sqlite3
is the standard library, so this adds no new dependency.

Lives at MyData/jobhunt/jobhunt.db by default -- inside the memory substrate,
so it travels with the rest of MyData, but a .db file matches none of
Memory's text readers in server.py, so the archive scanner never touches it.

IDs are human-readable prefixed sequences (OPP-000123, JOB-000045,
CO-000012, CT-000003), generated from a counters table inside the same
database so they survive restarts and never collide.
"""

import json
import logging
import re
import sqlite3
import threading
import time
import unicodedata
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

__all__ = [
    "connect", "init_schema", "default_db_path",
    "next_id", "now_iso",
    "normalize_text", "dedup_signature",
    "upsert_company", "get_company", "list_companies", "set_company_target",
    "create_job", "get_job", "list_jobs",
    "create_contact", "get_contact", "list_contacts",
    "create_opportunity", "get_opportunity", "list_opportunities",
    "find_opportunity_by_signature", "set_opportunity_status",
    "record_fit_check", "get_latest_fit_check",
    "create_resume_version", "list_resume_versions",
    "create_outreach_plan", "list_outreach_for_opportunity",
    "add_message", "list_messages_for_opportunity",
    "add_conversation", "list_conversations_for_opportunity",
    "add_task", "list_open_tasks", "complete_task",
    "add_followup", "list_due_followups",
    "log_search_query", "log_discovery_run",
    "save_role_permutations", "get_role_permutations",
    "audit", "daily_snapshot",
]

# Shares server.py's logger name on purpose, so a warning logged here still
# flows into logs/server.log once the app has configured handlers on it --
# without this module importing anything from server.py.
_LOG = logging.getLogger("claude-os")

STATUSES = (
    "DISCOVERED", "VERIFIED", "FIT_CHECK", "QUALIFIED", "RESUME_READY",
    "APPLICATION_READY", "APPLIED", "OUTREACH_PENDING", "OUTREACH_SENT",
    "REPLIED", "CONVERSATION", "INTERVIEW", "OFFER", "REJECTED",
    "WITHDRAWN", "CLOSED", "NO_ACTION", "FOLLOW_UP",
)
ROUTES = ("DISCOVERY", "PORTAL", "INBOUND", "CONVERSATION")

SCHEMA = """
CREATE TABLE IF NOT EXISTS id_counters (
    prefix TEXT PRIMARY KEY,
    next_value INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS companies (
    company_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    domain TEXT,
    careers_url TEXT,
    industry TEXT,
    location TEXT,
    description TEXT,
    leadership TEXT,           -- JSON
    research TEXT,             -- JSON: structured deep-dive fields
    research_sources TEXT,     -- JSON list of {url, retrieved_at}
    research_date TEXT,
    relevant_contacts TEXT,    -- JSON list of contact_id
    ats_boards TEXT,           -- JSON: {greenhouse: token, lever: token, ashby: token}
    target_priority TEXT,      -- P0-P3 when this is one of the deliberate target-bucket
                               -- companies (Top-Workflows-to-Land-a-Job-Faster's "20
                               -- companies" tactic); NULL for every other company
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_companies_name ON companies(name);

CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL,
    title TEXT NOT NULL,
    normalized_title TEXT NOT NULL,
    location TEXT,
    remote_type TEXT,
    employment_type TEXT,
    description TEXT,
    requirements TEXT,
    preferred_requirements TEXT,
    official_url TEXT,
    source_url TEXT,
    source_type TEXT,          -- OFFICIAL_ATS | COMPANY_SITE | PORTAL | INBOUND
    ats TEXT,                  -- greenhouse | lever | ashby | workday | bamboohr | other
    ats_job_id TEXT,
    posted_at TEXT,
    updated_at_source TEXT,
    discovered_at TEXT NOT NULL,
    age_days INTEGER,
    date_confidence TEXT,      -- HIGH | LOW | UNKNOWN
    status TEXT NOT NULL DEFAULT 'DISCOVERED',
    dedup_signature TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(company_id) REFERENCES companies(company_id)
);
CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company_id);
CREATE INDEX IF NOT EXISTS idx_jobs_signature ON jobs(dedup_signature);
CREATE INDEX IF NOT EXISTS idx_jobs_posted ON jobs(posted_at);

CREATE TABLE IF NOT EXISTS contacts (
    contact_id TEXT PRIMARY KEY,
    company_id TEXT,
    name TEXT NOT NULL,
    title TEXT,
    email TEXT,
    linkedin TEXT,
    x_handle TEXT,
    relationship TEXT,
    source TEXT,
    last_contact TEXT,
    next_followup TEXT,
    status TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(company_id) REFERENCES companies(company_id)
);
CREATE INDEX IF NOT EXISTS idx_contacts_company ON contacts(company_id);

CREATE TABLE IF NOT EXISTS opportunities (
    opportunity_id TEXT PRIMARY KEY,
    job_id TEXT,
    company_id TEXT NOT NULL,
    route TEXT NOT NULL,
    route_history TEXT,        -- JSON list of {route, at, note}
    status TEXT NOT NULL DEFAULT 'DISCOVERED',
    fit_score INTEGER,
    fit_status TEXT,
    resume_version_id TEXT,
    application_status TEXT,
    application_date TEXT,
    outreach_status TEXT,
    conversation_status TEXT,
    interview_stage TEXT,
    priority TEXT,              -- P0 | P1 | P2 | P3
    next_action TEXT,
    next_action_date TEXT,
    last_activity TEXT,
    last_status_change TEXT,
    deadline TEXT,
    outcome TEXT,
    notes TEXT,
    dedup_signature TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(job_id) REFERENCES jobs(job_id),
    FOREIGN KEY(company_id) REFERENCES companies(company_id)
);
CREATE INDEX IF NOT EXISTS idx_opp_signature ON opportunities(dedup_signature);
CREATE INDEX IF NOT EXISTS idx_opp_status ON opportunities(status);
CREATE INDEX IF NOT EXISTS idx_opp_company ON opportunities(company_id);

CREATE TABLE IF NOT EXISTS fit_checks (
    fit_check_id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL,
    score INTEGER NOT NULL,
    score_components TEXT NOT NULL,   -- JSON: {component: {max, awarded, evidence}}
    category TEXT NOT NULL,           -- Excellent | Strong | Possible | Reject
    strengths TEXT,                   -- JSON list
    gaps TEXT,                        -- JSON list
    mandatory_gaps TEXT,              -- JSON list
    preferred_gaps TEXT,              -- JSON list
    seniority_assessment TEXT,
    recommendation TEXT,              -- apply_hard | apply_light | skip
    confidence TEXT,
    narrative TEXT,                   -- the unchanged four-part text
    created_at TEXT NOT NULL,
    FOREIGN KEY(opportunity_id) REFERENCES opportunities(opportunity_id)
);
CREATE INDEX IF NOT EXISTS idx_fit_opp ON fit_checks(opportunity_id);

CREATE TABLE IF NOT EXISTS resume_versions (
    version_id TEXT PRIMARY KEY,
    job_id TEXT,
    company_id TEXT,
    base_version_id TEXT,      -- immutable master has NULL here
    headline TEXT,
    summary TEXT,
    content_path TEXT,         -- where the rendered file lives
    diff_notes TEXT,           -- JSON: what changed vs base, in plain terms
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_resume_job ON resume_versions(job_id);

CREATE TABLE IF NOT EXISTS outreach_plans (
    plan_id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL,
    contact_id TEXT,
    channel TEXT,               -- EMAIL | LINKEDIN | X
    reason TEXT,
    sequence_step INTEGER,
    timing TEXT,
    followup_timing TEXT,
    objective TEXT,
    status TEXT,
    response TEXT,
    next_action TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(opportunity_id) REFERENCES opportunities(opportunity_id)
);
CREATE INDEX IF NOT EXISTS idx_outreach_opp ON outreach_plans(opportunity_id);

CREATE TABLE IF NOT EXISTS messages (
    message_id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL,
    plan_id TEXT,
    channel TEXT,
    body TEXT NOT NULL,
    sent INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY(opportunity_id) REFERENCES opportunities(opportunity_id)
);
CREATE INDEX IF NOT EXISTS idx_messages_opp ON messages(opportunity_id);

CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL,
    person TEXT,
    company_id TEXT,
    context TEXT,
    discussed TEXT,
    commitments TEXT,
    questions TEXT,
    next_action TEXT,
    followup_date TEXT,
    potential_opening TEXT,
    referral INTEGER DEFAULT 0,
    conversation_date TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(opportunity_id) REFERENCES opportunities(opportunity_id)
);
CREATE INDEX IF NOT EXISTS idx_conv_opp ON conversations(opportunity_id);

CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    opportunity_id TEXT,
    title TEXT NOT NULL,
    due_date TEXT,
    status TEXT NOT NULL DEFAULT 'OPEN',
    created_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, due_date);

CREATE TABLE IF NOT EXISTS followups (
    followup_id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL,
    due_date TEXT NOT NULL,
    reason TEXT,
    status TEXT NOT NULL DEFAULT 'PENDING',
    created_at TEXT NOT NULL,
    FOREIGN KEY(opportunity_id) REFERENCES opportunities(opportunity_id)
);
CREATE INDEX IF NOT EXISTS idx_followups_due ON followups(due_date, status);

CREATE TABLE IF NOT EXISTS status_history (
    history_id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT NOT NULL,
    note TEXT,
    at TEXT NOT NULL,
    FOREIGN KEY(opportunity_id) REFERENCES opportunities(opportunity_id)
);
CREATE INDEX IF NOT EXISTS idx_history_opp ON status_history(opportunity_id);

CREATE TABLE IF NOT EXISTS search_queries (
    query_id TEXT PRIMARY KEY,
    run_id TEXT,
    query_text TEXT NOT NULL,
    provider TEXT NOT NULL,
    result_state TEXT NOT NULL,   -- SEARCH_SUCCESS | SEARCH_PARTIAL | SEARCH_BLOCKED | SEARCH_FAILED
    result_count INTEGER DEFAULT 0,
    detail TEXT,
    at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_queries_run ON search_queries(run_id);

CREATE TABLE IF NOT EXISTS discovery_runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    queries_run INTEGER DEFAULT 0,
    jobs_found INTEGER DEFAULT 0,
    jobs_verified INTEGER DEFAULT 0,
    jobs_qualified INTEGER DEFAULT 0,
    summary TEXT                 -- JSON
);

CREATE TABLE IF NOT EXISTS role_permutations (
    perm_id TEXT PRIMARY KEY,
    sheet TEXT NOT NULL,          -- which of the 10 sheets this row belongs to
    canonical_role TEXT,
    designation TEXT,
    alternative_designation TEXT,
    seniority TEXT,
    function TEXT,
    role_family TEXT,
    adjacent_role TEXT,
    include_exclude TEXT,
    search_priority TEXT,
    notes TEXT,
    generated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_perm_sheet ON role_permutations(sheet);

CREATE TABLE IF NOT EXISTS audit_log (
    audit_id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id TEXT,
    action TEXT NOT NULL,
    detail TEXT,
    at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_log(entity_type, entity_id);
"""

# ---------------------------------------------------------------- plumbing --

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def default_db_path(root: Optional[Path] = None) -> Path:
    base = Path(root) if root else Path(__file__).resolve().parent
    return base / "MyData" / "jobhunt" / "jobhunt.db"


class _LockingConnection(sqlite3.Connection):
    """A sqlite3.Connection serialized behind one reentrant lock.

    server.py hands one JOBHUNT_CONN to every /v1/jobhunt/* request, and
    those run on FastAPI's threadpool -- genuinely concurrent OS threads.
    check_same_thread=False only turns off Python's same-thread guard; it
    does not make the connection safe for concurrent use, that's on the
    caller. Overriding execute()/executescript() here means every existing
    call site in this file gets that safety for free, reads included,
    without editing the ~30 functions that call conn.execute(...) directly.

    _tx_depth also makes nested `with _tx(conn):` blocks defer to the
    outermost commit. next_id() opens its own _tx and used to commit even
    when called from inside a caller's already-open _tx (e.g.
    create_opportunity's status_history insert) -- that split one logical,
    multi-statement write into several partial commits, so a crash between
    them could leave an opportunity row with no matching history entry.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._db_lock = threading.RLock()
        self._tx_depth = 0

    def execute(self, *args: Any, **kwargs: Any) -> sqlite3.Cursor:
        with self._db_lock:
            return super().execute(*args, **kwargs)

    def executemany(self, *args: Any, **kwargs: Any) -> sqlite3.Cursor:
        with self._db_lock:
            return super().executemany(*args, **kwargs)

    def executescript(self, *args: Any, **kwargs: Any) -> sqlite3.Cursor:
        with self._db_lock:
            return super().executescript(*args, **kwargs)


def connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30, check_same_thread=False,
                           factory=_LockingConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, coltype: str) -> None:
    """Adds a column to a table that already exists if that column is not
    already there. CREATE TABLE IF NOT EXISTS (used throughout SCHEMA above)
    only covers a table that doesn't exist at all yet -- it does nothing for
    a real, already-populated table created by an earlier version of this
    schema, and ALTER TABLE ADD COLUMN has no "IF NOT EXISTS" form of its
    own to guard a repeat run with. table/column/coltype are always
    hardcoded call-site literals here, never request-derived, so building
    the ALTER statement by string formatting is safe."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(%s)" % table)}
    if column not in cols:
        conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, column, coltype))
        conn.commit()


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
    _ensure_column(conn, "companies", "target_priority", "TEXT")


@contextmanager
def _tx(conn: sqlite3.Connection):
    lock = getattr(conn, "_db_lock", None)
    if lock is None:
        # A plain sqlite3.Connection not built via connect() above (a raw
        # connection handed in directly, e.g. in a quick script) -- still
        # correct, just without the shared-connection locking.
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return
    lock.acquire()
    conn._tx_depth += 1
    try:
        yield conn
        if conn._tx_depth == 1:
            conn.commit()
    except Exception:
        if conn._tx_depth == 1:
            conn.rollback()
        raise
    finally:
        conn._tx_depth -= 1
        lock.release()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def next_id(conn: sqlite3.Connection, prefix: str, width: int = 6) -> str:
    """Atomic, restart-safe sequence per prefix. OPP-000123 style."""
    with _tx(conn):
        conn.execute(
            "INSERT INTO id_counters(prefix, next_value) VALUES (?, 1) "
            "ON CONFLICT(prefix) DO UPDATE SET next_value = next_value + 1",
            (prefix,))
        row = conn.execute(
            "SELECT next_value FROM id_counters WHERE prefix = ?", (prefix,)
        ).fetchone()
        n = row["next_value"]
    return "%s-%0*d" % (prefix, width, n)


# ------------------------------------------------------------ normalization --

def normalize_text(s: str) -> str:
    """Lowercase, strip accents, collapse whitespace/punctuation to single
    spaces. Used for dedup signatures, never shown to a user."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = _NON_ALNUM.sub(" ", s.lower()).strip()
    return re.sub(r"\s+", " ", s)


def dedup_signature(company: str, title: str, location: str = "",
                    canonical_url: str = "", ats_id: str = "") -> str:
    """Company + role + location + canonical URL + ATS id, all folded into
    one normalized signature when supplied. Two records only collapse into
    the same opportunity when every field actually given agrees -- this
    used to silently ignore canonical_url/ats_id entirely (they were
    accepted parameters nothing read), which merged genuinely different
    postings that happened to share a company/title/location but had a
    different URL or req id. Existing stored signatures are untouched; this
    only changes how new signatures are computed."""
    parts = [normalize_text(company), normalize_text(title), normalize_text(location)]
    if canonical_url:
        parts.append(normalize_text(canonical_url))
    if ats_id:
        parts.append(normalize_text(str(ats_id)))
    return "|".join(parts)


# --------------------------------------------------------------- companies --

def upsert_company(conn: sqlite3.Connection, name: str, **fields: Any) -> str:
    existing = conn.execute(
        "SELECT company_id FROM companies WHERE lower(name) = ?",
        (name.strip().lower(),)).fetchone()
    ts = now_iso()
    if existing:
        cid = existing["company_id"]
        cols = ", ".join("%s = ?" % k for k in fields) + ", updated_at = ?"
        if fields:
            conn.execute("UPDATE companies SET %s WHERE company_id = ?" % cols,
                        (*fields.values(), ts, cid))
            conn.commit()
        return cid
    cid = next_id(conn, "CO")
    cols = ["company_id", "name", "created_at", "updated_at"] + list(fields.keys())
    vals = [cid, name, ts, ts] + list(fields.values())
    placeholders = ",".join("?" * len(vals))
    with _tx(conn):
        conn.execute("INSERT INTO companies(%s) VALUES (%s)"
                    % (",".join(cols), placeholders), vals)
    audit(conn, "company", cid, "created", name)
    return cid


def get_company(conn: sqlite3.Connection, company_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute("SELECT * FROM companies WHERE company_id = ?",
                       (company_id,)).fetchone()
    return dict(row) if row else None


def list_companies(conn: sqlite3.Connection, limit: int = 200,
                   target_only: bool = False) -> List[Dict[str, Any]]:
    if target_only:
        rows = conn.execute(
            "SELECT * FROM companies WHERE target_priority IS NOT NULL AND "
            "target_priority != '' ORDER BY target_priority ASC, updated_at DESC LIMIT ?",
            (limit,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM companies ORDER BY updated_at DESC LIMIT ?",
                            (limit,)).fetchall()
    return [dict(r) for r in rows]


def set_company_target(conn: sqlite3.Connection, company_id: str,
                       target_priority: Optional[str]) -> None:
    """target_priority is P0-P3 to add/keep a company in the deliberate
    target bucket, or None/"" to remove it -- the same convention
    opportunities.priority already uses, just at the company level."""
    with _tx(conn):
        conn.execute(
            "UPDATE companies SET target_priority = ?, updated_at = ? WHERE company_id = ?",
            (target_priority or None, now_iso(), company_id))


# --------------------------------------------------------------------- jobs --

def create_job(conn: sqlite3.Connection, company_id: str, title: str,
               **fields: Any) -> str:
    jid = next_id(conn, "JOB")
    ts = now_iso()
    normalized_title = normalize_text(title)
    signature = dedup_signature(
        (get_company(conn, company_id) or {}).get("name", ""),
        title, fields.get("location", ""), fields.get("official_url", ""))
    cols = ["job_id", "company_id", "title", "normalized_title",
           "dedup_signature", "discovered_at", "created_at", "updated_at"]
    vals = [jid, company_id, title, normalized_title, signature, ts, ts, ts]
    for k, v in fields.items():
        cols.append(k)
        vals.append(v)
    placeholders = ",".join("?" * len(vals))
    with _tx(conn):
        conn.execute("INSERT INTO jobs(%s) VALUES (%s)"
                    % (",".join(cols), placeholders), vals)
    audit(conn, "job", jid, "created", title)
    return jid


def get_job(conn: sqlite3.Connection, job_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def list_jobs(conn: sqlite3.Connection, company_id: Optional[str] = None,
             limit: int = 200) -> List[Dict[str, Any]]:
    if company_id:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE company_id = ? ORDER BY discovered_at DESC LIMIT ?",
            (company_id, limit)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY discovered_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- contacts --

def create_contact(conn: sqlite3.Connection, name: str, **fields: Any) -> str:
    ctid = next_id(conn, "CT")
    ts = now_iso()
    cols = ["contact_id", "name", "created_at", "updated_at"] + list(fields.keys())
    vals = [ctid, name, ts, ts] + list(fields.values())
    placeholders = ",".join("?" * len(vals))
    with _tx(conn):
        conn.execute("INSERT INTO contacts(%s) VALUES (%s)"
                    % (",".join(cols), placeholders), vals)
    audit(conn, "contact", ctid, "created", name)
    return ctid


def get_contact(conn: sqlite3.Connection, contact_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute("SELECT * FROM contacts WHERE contact_id = ?",
                       (contact_id,)).fetchone()
    return dict(row) if row else None


def list_contacts(conn: sqlite3.Connection, company_id: Optional[str] = None
                  ) -> List[Dict[str, Any]]:
    if company_id:
        rows = conn.execute(
            "SELECT * FROM contacts WHERE company_id = ? ORDER BY updated_at DESC",
            (company_id,)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM contacts ORDER BY updated_at DESC LIMIT 200").fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- opportunities --

def find_opportunity_by_signature(conn: sqlite3.Connection, signature: str
                                  ) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM opportunities WHERE dedup_signature = ? "
        "ORDER BY created_at ASC LIMIT 1", (signature,)).fetchone()
    return dict(row) if row else None


def create_opportunity(conn: sqlite3.Connection, company_id: str, route: str,
                       job_id: Optional[str] = None, **fields: Any) -> str:
    if route not in ROUTES:
        raise ValueError("unknown route: %s" % route)
    oid = next_id(conn, "OPP")
    ts = now_iso()
    signature = fields.pop("dedup_signature", None)
    if not signature and job_id:
        job = get_job(conn, job_id)
        if job:
            signature = job.get("dedup_signature")
    route_history = json.dumps([{"route": route, "at": ts, "note": "created"}])
    cols = ["opportunity_id", "company_id", "job_id", "route", "route_history",
           "status", "dedup_signature", "last_status_change", "last_activity",
           "created_at", "updated_at"]
    vals = [oid, company_id, job_id, route, route_history,
           fields.pop("status", "DISCOVERED"), signature, ts, ts, ts, ts]
    for k, v in fields.items():
        cols.append(k)
        vals.append(v)
    placeholders = ",".join("?" * len(vals))
    with _tx(conn):
        conn.execute("INSERT INTO opportunities(%s) VALUES (%s)"
                    % (",".join(cols), placeholders), vals)
        conn.execute(
            "INSERT INTO status_history(history_id, opportunity_id, from_status, "
            "to_status, note, at) VALUES (?,?,?,?,?,?)",
            (next_id(conn, "HIST"), oid, None, "DISCOVERED", "opportunity created", ts))
    audit(conn, "opportunity", oid, "created", route)
    return oid


def get_opportunity(conn: sqlite3.Connection, opportunity_id: str
                    ) -> Optional[Dict[str, Any]]:
    row = conn.execute("SELECT * FROM opportunities WHERE opportunity_id = ?",
                       (opportunity_id,)).fetchone()
    return dict(row) if row else None


def list_opportunities(conn: sqlite3.Connection, status: Optional[str] = None,
                       min_fit: Optional[int] = None, limit: int = 500
                       ) -> List[Dict[str, Any]]:
    # Joins in the job's title/URLs and the company's name, aliased so none
    # of them collide with an opportunities column of the same name (both
    # tables have their own status/updated_at -- a bare "SELECT o.*, j.*"
    # would let one silently clobber the other in the resulting dict). This
    # is what actually lets every opportunity card in the dashboard show a
    # real title, company, and link instead of just an ID -- before this,
    # list_opportunities() never carried those fields at all, since
    # opportunities and jobs are separate tables and nothing joined them.
    q = ("SELECT o.*, j.title AS job_title, j.official_url AS job_official_url, "
        "j.source_url AS job_source_url, c.name AS company_name "
        "FROM opportunities o "
        "LEFT JOIN jobs j ON o.job_id = j.job_id "
        "LEFT JOIN companies c ON o.company_id = c.company_id "
        "WHERE 1=1")
    params: List[Any] = []
    if status:
        q += " AND o.status = ?"
        params.append(status)
    if min_fit is not None:
        q += " AND o.fit_score >= ?"
        params.append(min_fit)
    q += " ORDER BY o.updated_at DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(q, params).fetchall()
    return [dict(r) for r in rows]


def set_opportunity_status(conn: sqlite3.Connection, opportunity_id: str,
                           new_status: str, note: str = "") -> None:
    if new_status not in STATUSES:
        raise ValueError("unknown status: %s" % new_status)
    row = get_opportunity(conn, opportunity_id)
    if not row:
        raise KeyError(opportunity_id)
    ts = now_iso()
    with _tx(conn):
        conn.execute(
            "UPDATE opportunities SET status = ?, last_status_change = ?, "
            "last_activity = ?, updated_at = ? WHERE opportunity_id = ?",
            (new_status, ts, ts, ts, opportunity_id))
        conn.execute(
            "INSERT INTO status_history(history_id, opportunity_id, from_status, "
            "to_status, note, at) VALUES (?,?,?,?,?,?)",
            (next_id(conn, "HIST"), opportunity_id, row["status"], new_status, note, ts))
    audit(conn, "opportunity", opportunity_id, "status_change",
         "%s -> %s" % (row["status"], new_status))


# ---------------------------------------------------------------- fit checks --

def record_fit_check(conn: sqlite3.Connection, opportunity_id: str, score: int,
                     score_components: Dict[str, Any], category: str,
                     narrative: str, **fields: Any) -> str:
    fcid = next_id(conn, "FIT")
    ts = now_iso()
    cols = ["fit_check_id", "opportunity_id", "score", "score_components",
           "category", "narrative", "created_at"]
    vals = [fcid, opportunity_id, int(score), json.dumps(score_components),
           category, narrative, ts]
    for k, v in fields.items():
        cols.append(k)
        vals.append(json.dumps(v) if isinstance(v, (list, dict)) else v)
    placeholders = ",".join("?" * len(vals))
    with _tx(conn):
        conn.execute("INSERT INTO fit_checks(%s) VALUES (%s)"
                    % (",".join(cols), placeholders), vals)
        conn.execute(
            "UPDATE opportunities SET fit_score = ?, fit_status = ?, "
            "updated_at = ? WHERE opportunity_id = ?",
            (int(score), category, ts, opportunity_id))
    audit(conn, "opportunity", opportunity_id, "fit_check", "%s (%d)" % (category, score))
    return fcid


# Columns record_fit_check() json.dumps()'d going in (score_components
# always; the rest only when the caller passed a list/dict). Read back the
# same way, so GET /v1/jobhunt/opportunities/{id}'s embedded fit_check
# matches the real arrays/objects POST /v1/jobhunt/fit/check returns,
# instead of double-encoded JSON strings.
_FIT_CHECK_JSON_FIELDS = ("score_components", "strengths", "gaps",
                         "mandatory_gaps", "preferred_gaps")


def get_latest_fit_check(conn: sqlite3.Connection, opportunity_id: str
                         ) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM fit_checks WHERE opportunity_id = ? "
        "ORDER BY created_at DESC LIMIT 1", (opportunity_id,)).fetchone()
    if row is None:
        return None
    result = dict(row)
    for field in _FIT_CHECK_JSON_FIELDS:
        val = result.get(field)
        if isinstance(val, str) and val:
            try:
                result[field] = json.loads(val)
            except (json.JSONDecodeError, ValueError):
                pass   # was never JSON after all -- leave the raw string
    return result


# --------------------------------------------------------------- resumes --

def create_resume_version(conn: sqlite3.Connection, content_path: str,
                          job_id: Optional[str] = None,
                          company_id: Optional[str] = None,
                          base_version_id: Optional[str] = None,
                          **fields: Any) -> str:
    vid = next_id(conn, "RES")
    ts = now_iso()
    cols = ["version_id", "content_path", "job_id", "company_id",
           "base_version_id", "created_at"]
    vals = [vid, content_path, job_id, company_id, base_version_id, ts]
    for k, v in fields.items():
        cols.append(k)
        vals.append(json.dumps(v) if isinstance(v, (list, dict)) else v)
    placeholders = ",".join("?" * len(vals))
    with _tx(conn):
        conn.execute("INSERT INTO resume_versions(%s) VALUES (%s)"
                    % (",".join(cols), placeholders), vals)
    audit(conn, "resume_version", vid, "created", content_path)
    return vid


def list_resume_versions(conn: sqlite3.Connection, job_id: Optional[str] = None
                         ) -> List[Dict[str, Any]]:
    if job_id:
        rows = conn.execute(
            "SELECT * FROM resume_versions WHERE job_id = ? ORDER BY created_at DESC",
            (job_id,)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM resume_versions ORDER BY created_at DESC LIMIT 200").fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------- outreach ----

def create_outreach_plan(conn: sqlite3.Connection, opportunity_id: str,
                         channel: str, **fields: Any) -> str:
    pid = next_id(conn, "OUT")
    ts = now_iso()
    cols = ["plan_id", "opportunity_id", "channel", "created_at", "updated_at"]
    vals = [pid, opportunity_id, channel, ts, ts]
    for k, v in fields.items():
        cols.append(k)
        vals.append(v)
    placeholders = ",".join("?" * len(vals))
    with _tx(conn):
        conn.execute("INSERT INTO outreach_plans(%s) VALUES (%s)"
                    % (",".join(cols), placeholders), vals)
    audit(conn, "opportunity", opportunity_id, "outreach_planned", channel)
    return pid


def list_outreach_for_opportunity(conn: sqlite3.Connection, opportunity_id: str
                                  ) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM outreach_plans WHERE opportunity_id = ? ORDER BY created_at",
        (opportunity_id,)).fetchall()
    return [dict(r) for r in rows]


def add_message(conn: sqlite3.Connection, opportunity_id: str, channel: str,
                body: str, plan_id: Optional[str] = None, sent: bool = False) -> str:
    mid = next_id(conn, "MSG")
    ts = now_iso()
    with _tx(conn):
        conn.execute(
            "INSERT INTO messages(message_id, opportunity_id, plan_id, channel, "
            "body, sent, created_at) VALUES (?,?,?,?,?,?,?)",
            (mid, opportunity_id, plan_id, channel, body, int(sent), ts))
    audit(conn, "opportunity", opportunity_id, "message_drafted", channel)
    return mid


def list_messages_for_opportunity(conn: sqlite3.Connection, opportunity_id: str
                                  ) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM messages WHERE opportunity_id = ? ORDER BY created_at",
        (opportunity_id,)).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------- conversations --

def add_conversation(conn: sqlite3.Connection, opportunity_id: str,
                     conversation_date: str, **fields: Any) -> str:
    cid = next_id(conn, "CONV")
    ts = now_iso()
    cols = ["conversation_id", "opportunity_id", "conversation_date", "created_at"]
    vals = [cid, opportunity_id, conversation_date, ts]
    for k, v in fields.items():
        cols.append(k)
        vals.append(v)
    placeholders = ",".join("?" * len(vals))
    with _tx(conn):
        conn.execute("INSERT INTO conversations(%s) VALUES (%s)"
                    % (",".join(cols), placeholders), vals)
        conn.execute(
            "UPDATE opportunities SET conversation_status = 'ACTIVE', "
            "last_activity = ?, updated_at = ? WHERE opportunity_id = ?",
            (ts, ts, opportunity_id))
    audit(conn, "opportunity", opportunity_id, "conversation_logged", conversation_date)
    return cid


def list_conversations_for_opportunity(conn: sqlite3.Connection, opportunity_id: str
                                       ) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM conversations WHERE opportunity_id = ? ORDER BY conversation_date",
        (opportunity_id,)).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------- tasks --

def add_task(conn: sqlite3.Connection, title: str, opportunity_id: Optional[str] = None,
            due_date: Optional[str] = None) -> str:
    tid = next_id(conn, "TASK")
    ts = now_iso()
    with _tx(conn):
        conn.execute(
            "INSERT INTO tasks(task_id, opportunity_id, title, due_date, "
            "status, created_at) VALUES (?,?,?,?,?,?)",
            (tid, opportunity_id, title, due_date, "OPEN", ts))
    return tid


def list_open_tasks(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM tasks WHERE status = 'OPEN' ORDER BY due_date").fetchall()
    return [dict(r) for r in rows]


def complete_task(conn: sqlite3.Connection, task_id: str) -> None:
    ts = now_iso()
    with _tx(conn):
        conn.execute(
            "UPDATE tasks SET status = 'DONE', completed_at = ? WHERE task_id = ?",
            (ts, task_id))


def add_followup(conn: sqlite3.Connection, opportunity_id: str, due_date: str,
                 reason: str = "") -> str:
    fid = next_id(conn, "FUP")
    ts = now_iso()
    with _tx(conn):
        conn.execute(
            "INSERT INTO followups(followup_id, opportunity_id, due_date, reason, "
            "status, created_at) VALUES (?,?,?,?,?,?)",
            (fid, opportunity_id, due_date, reason, "PENDING", ts))
    return fid


def list_due_followups(conn: sqlite3.Connection, as_of: Optional[str] = None
                       ) -> List[Dict[str, Any]]:
    as_of = as_of or now_iso()
    rows = conn.execute(
        "SELECT * FROM followups WHERE status = 'PENDING' AND due_date <= ? "
        "ORDER BY due_date", (as_of,)).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------- discovery ---

def log_search_query(conn: sqlite3.Connection, query_text: str, provider: str,
                     result_state: str, run_id: Optional[str] = None,
                     result_count: int = 0, detail: str = "") -> str:
    qid = next_id(conn, "Q")
    ts = now_iso()
    with _tx(conn):
        conn.execute(
            "INSERT INTO search_queries(query_id, run_id, query_text, provider, "
            "result_state, result_count, detail, at) VALUES (?,?,?,?,?,?,?,?)",
            (qid, run_id, query_text, provider, result_state, result_count, detail, ts))
    return qid


def log_discovery_run(conn: sqlite3.Connection, run_id: str, **fields: Any) -> None:
    existing = conn.execute(
        "SELECT run_id FROM discovery_runs WHERE run_id = ?", (run_id,)).fetchone()
    ts = now_iso()
    if existing:
        cols = ", ".join("%s = ?" % k for k in fields)
        if fields:
            with _tx(conn):
                conn.execute("UPDATE discovery_runs SET %s WHERE run_id = ?"
                            % cols, (*fields.values(), run_id))
        return
    cols = ["run_id", "started_at"] + list(fields.keys())
    vals = [run_id, ts] + list(fields.values())
    placeholders = ",".join("?" * len(vals))
    with _tx(conn):
        conn.execute("INSERT INTO discovery_runs(%s) VALUES (%s)"
                    % (",".join(cols), placeholders), vals)


# -------------------------------------------------------- role permutations --

# The real, fixed schema. Rows come from a free-tier lane reply -- server.py's
# own comment elsewhere calls those models "inconsistent about following the
# exact format asked for" -- so an extra or renamed key (e.g. "id",
# "location") is a real, expected case, not a hypothetical one.
_ROLE_PERMUTATION_COLUMNS = {
    "canonical_role", "designation", "alternative_designation", "seniority",
    "function", "role_family", "adjacent_role", "include_exclude",
    "search_priority", "notes",
}


def save_role_permutations(conn: sqlite3.Connection, sheet: str,
                           rows: List[Dict[str, Any]]) -> int:
    """Filters each row to known columns and saves row-by-row so one
    malformed row (an unrecognized key that used to raise
    sqlite3.OperationalError and roll back the whole sheet) costs only that
    row, not every other valid one in the same lane reply."""
    ts = now_iso()
    saved = 0
    for row in rows:
        filtered = {k: v for k, v in row.items() if k in _ROLE_PERMUTATION_COLUMNS}
        pid = next_id(conn, "PERM")
        cols = ["perm_id", "sheet", "generated_at"] + list(filtered.keys())
        vals = [pid, sheet, ts] + list(filtered.values())
        placeholders = ",".join("?" * len(vals))
        try:
            with _tx(conn):
                conn.execute("INSERT INTO role_permutations(%s) VALUES (%s)"
                            % (",".join(cols), placeholders), vals)
            saved += 1
        except sqlite3.Error as exc:
            _LOG.warning("save_role_permutations: dropped one malformed row (%s): %r",
                        exc, row)
    return saved


def get_role_permutations(conn: sqlite3.Connection, sheet: Optional[str] = None
                          ) -> List[Dict[str, Any]]:
    if sheet:
        rows = conn.execute(
            "SELECT * FROM role_permutations WHERE sheet = ? ORDER BY perm_id",
            (sheet,)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM role_permutations ORDER BY sheet, perm_id").fetchall()
    return [dict(r) for r in rows]


# ----------------------------------------------------------------- audit ---

def audit(conn: sqlite3.Connection, entity_type: str, entity_id: Optional[str],
         action: str, detail: str = "") -> None:
    aid = next_id(conn, "AUD")
    ts = now_iso()
    with _tx(conn):
        conn.execute(
            "INSERT INTO audit_log(audit_id, entity_type, entity_id, action, "
            "detail, at) VALUES (?,?,?,?,?,?)",
            (aid, entity_type, entity_id, action, detail[:500], ts))


# ------------------------------------------------------------ daily control --

def daily_snapshot(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Pure aggregation, no model. Feeds the Daily Control skill and the
    Overview dashboard."""
    today = datetime.now(timezone.utc).date().isoformat()

    def scalar(q: str, *params: Any) -> int:
        row = conn.execute(q, params).fetchone()
        return int(row[0] or 0) if row else 0

    return {
        "date": today,
        "new_discoveries_today": scalar(
            "SELECT COUNT(*) FROM jobs WHERE discovered_at >= ?", today),
        "qualified_jobs": scalar(
            "SELECT COUNT(*) FROM opportunities WHERE status = 'QUALIFIED'"),
        "applications_ready": scalar(
            "SELECT COUNT(*) FROM opportunities WHERE status = 'APPLICATION_READY'"),
        "outreach_pending": scalar(
            "SELECT COUNT(*) FROM opportunities WHERE status = 'OUTREACH_PENDING'"),
        "followups_due": scalar(
            "SELECT COUNT(*) FROM followups WHERE status = 'PENDING' AND due_date <= ?",
            now_iso()),
        "conversations_active": scalar(
            "SELECT COUNT(*) FROM opportunities WHERE conversation_status = 'ACTIVE'"),
        "interviews": scalar(
            "SELECT COUNT(*) FROM opportunities WHERE status = 'INTERVIEW'"),
        "offers": scalar(
            "SELECT COUNT(*) FROM opportunities WHERE status = 'OFFER'"),
        "high_value_90plus": scalar(
            "SELECT COUNT(*) FROM opportunities WHERE fit_score >= 90 "
            "AND status NOT IN ('REJECTED','WITHDRAWN','CLOSED')"),
        "overdue_tasks": scalar(
            "SELECT COUNT(*) FROM tasks WHERE status = 'OPEN' AND due_date < ?", today),
        "stale_opportunities": scalar(
            "SELECT COUNT(*) FROM opportunities WHERE status NOT IN "
            "('REJECTED','WITHDRAWN','CLOSED','NO_ACTION') AND last_activity < ?",
            datetime.fromtimestamp(
                time.time() - 14 * 86400, tz=timezone.utc
            ).isoformat(timespec="seconds")),
    }
