#!/usr/bin/env python3
"""
jobhunt_daily.py  --  Skill 8, Daily Control.

Tier 0. No model calls, on purpose (ROUTING.md: RAG and storage need no
model at all, and a report is exactly that). This is a report, not
motivation -- it answers five fixed questions with real rows from
jobhunt_db.py, never a generated pep-talk.
"""

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import jobhunt_db as db
import jobhunt_fit

__all__ = ["daily_report", "WARM_INTRO_RELATIONSHIPS", "WARM_INTRO_STALE_DAYS"]

# contacts.relationship values that count as a warm-tie / referral
# relationship rather than a cold recruiter/hiring-manager contact --
# Top-Workflows-to-Land-a-Job-Faster's "weak-tie activation" tactic. Free
# text, matched case-insensitively, not a DB-enforced enum.
WARM_INTRO_RELATIONSHIPS = ("referral", "weak_tie", "warm_intro", "informational")
# A warm-tie contact with no explicit next_followup scheduled and no
# contact in this many days surfaces anyway -- the same "gone quiet"
# reasoning daily_report() already applies to stale opportunities below,
# just for a person instead of a job.
WARM_INTRO_STALE_DAYS = 14


def _rows(conn: sqlite3.Connection, query: str, *params: Any) -> List[Dict[str, Any]]:
    return [dict(r) for r in conn.execute(query, params).fetchall()]


def _warm_intro_followups(conn: sqlite3.Connection, today: str) -> List[Dict[str, Any]]:
    stale_cutoff = (datetime.now(timezone.utc) - timedelta(days=WARM_INTRO_STALE_DAYS)
                    ).date().isoformat()
    placeholders = ",".join("?" * len(WARM_INTRO_RELATIONSHIPS))
    return _rows(
        conn,
        "SELECT * FROM contacts WHERE lower(relationship) IN (%s) AND ("
        "  (next_followup IS NOT NULL AND next_followup != '' AND next_followup <= ?)"
        "  OR ((next_followup IS NULL OR next_followup = '') AND "
        "      last_contact IS NOT NULL AND last_contact != '' AND last_contact < ?)"
        ") ORDER BY COALESCE(NULLIF(next_followup, ''), last_contact)" % placeholders,
        *WARM_INTRO_RELATIONSHIPS, today, stale_cutoff)


def daily_report(conn: sqlite3.Connection) -> Dict[str, Any]:
    today = datetime.now(timezone.utc).date().isoformat()
    now = db.now_iso()

    new_discoveries = _rows(
        conn, "SELECT * FROM jobs WHERE discovered_at >= ? ORDER BY discovered_at DESC",
        today)
    new_qualified = db.list_opportunities(conn, status="QUALIFIED")
    applications_to_make = db.list_opportunities(conn, status="APPLICATION_READY")
    outreach_to_send = db.list_opportunities(conn, status="OUTREACH_PENDING")
    followups_due = db.list_due_followups(conn, as_of=now)
    responses_received = db.list_opportunities(conn, status="REPLIED")
    interviews = db.list_opportunities(conn, status="INTERVIEW")
    pending_research = _rows(
        conn, "SELECT * FROM companies WHERE research_date IS NULL "
             "OR research_date = '' ORDER BY updated_at DESC LIMIT 20")
    overdue_tasks = _rows(
        conn, "SELECT * FROM tasks WHERE status = 'OPEN' AND due_date < ? "
             "ORDER BY due_date", today)
    recently_closed = _rows(
        conn, "SELECT * FROM opportunities WHERE status IN "
             "('CLOSED','REJECTED','WITHDRAWN') AND last_status_change >= ? "
             "ORDER BY last_status_change DESC",
        (datetime.now(timezone.utc).date().isoformat()))
    high_value = db.list_opportunities(conn, min_fit=90)
    high_value = [o for o in high_value
                 if o.get("status") not in ("REJECTED", "WITHDRAWN", "CLOSED")]

    all_open = db.list_opportunities(conn, limit=5000)
    stale_cutoff = datetime.fromtimestamp(
        datetime.now(timezone.utc).timestamp() - 14 * 86400, tz=timezone.utc
    ).isoformat(timespec="seconds")
    stale = [o for o in all_open
            if o.get("status") not in ("REJECTED", "WITHDRAWN", "CLOSED", "NO_ACTION")
            and (o.get("last_activity") or "") < stale_cutoff]

    # WHAT NEEDS ACTION TODAY -- anything with an explicit next_action_date
    # today or earlier, plus followups due, plus anything application-ready.
    action_today = [o for o in all_open
                    if o.get("next_action_date") and o["next_action_date"] <= today]

    warm_intro_followups = _warm_intro_followups(conn, today)

    # HIGH PRIORITY TO APPLY -- Top-Workflows-to-Land-a-Job-Faster's own
    # two thresholds (80%+ match, apply within 48h of posting), computed
    # fresh here via jobhunt_fit.application_urgency() rather than trusted
    # from a stored column (see that function's docstring for why).
    not_yet_applied = _rows(
        conn,
        "SELECT o.*, j.posted_at AS job_posted_at, j.title AS job_title, "
        "c.name AS company_name "
        "FROM opportunities o LEFT JOIN jobs j ON o.job_id = j.job_id "
        "LEFT JOIN companies c ON o.company_id = c.company_id "
        "WHERE o.status NOT IN ('REJECTED','WITHDRAWN','CLOSED','APPLIED') "
        "ORDER BY o.updated_at DESC LIMIT 500")
    high_priority_to_apply = []
    for o in not_yet_applied:
        urgency = jobhunt_fit.application_urgency(o.get("fit_score"), o.get("job_posted_at"))
        if urgency["ready_to_apply"] or urgency["apply_soon"]:
            high_priority_to_apply.append(dict(o, **urgency))

    priority_pool = high_value + applications_to_make + interviews
    highest_priority = None
    if priority_pool:
        # explicit, deterministic ranking: interview > 90+ fit > application-ready,
        # ties broken by fit score descending. No model involved in the ranking.
        def rank(o: Dict[str, Any]) -> tuple:
            stage_rank = {"INTERVIEW": 0, "APPLICATION_READY": 1}.get(o.get("status"), 2)
            return (stage_rank, -(o.get("fit_score") or 0))
        highest_priority = sorted(priority_pool, key=rank)[0]

    return {
        "date": today,
        "sections": {
            "new_discoveries": new_discoveries,
            "new_qualified_jobs": new_qualified,
            "applications_to_make": applications_to_make,
            "outreach_to_send": outreach_to_send,
            "followups_due": followups_due,
            "responses_received": responses_received,
            "interviews": interviews,
            "pending_research": pending_research,
            "stale_opportunities": stale,
            "overdue_tasks": overdue_tasks,
            "recently_closed": recently_closed,
            "high_value_opportunities": high_value,
            "warm_intro_followups": warm_intro_followups,
            "high_priority_to_apply": high_priority_to_apply,
        },
        "answers": {
            "what_changed": {
                "new_discoveries": len(new_discoveries),
                "new_qualified": len(new_qualified),
                "responses_received": len(responses_received),
                "recently_closed": len(recently_closed),
            },
            "what_needs_action_today": action_today,
            "what_is_overdue": overdue_tasks,
            "what_is_waiting": outreach_to_send + followups_due + warm_intro_followups,
            "highest_priority": highest_priority,
        },
    }
