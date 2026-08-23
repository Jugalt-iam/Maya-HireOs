#!/usr/bin/env python3
"""
jobhunt_outreach.py  --  Skill 5, the outreach planning engine.

Pure Tier 0. Sequencing and timing are a fixed, deterministic shape -- no
judgment involved in "email first, then LinkedIn four days later" -- so
there is no model call in this file at all. The actual message text (Skill
6) is a separate, judgment-shaped step that runs on the existing `copy` mode
lane call in server.py, which already loads job_search_adapter.md for voice.

Never sends anything. This only prepares a plan; sending stays a human
action, per the brief's explicit "the system prepares outreach" rule.
"""

from typing import Any, Dict, List, Optional

__all__ = ["CHANNELS", "PERSON_TYPES", "build_plan"]

CHANNELS = ("EMAIL", "LINKEDIN", "X")
PERSON_TYPES = ("recruiter", "hiring_manager", "marketing_leader", "business_leader",
                "founder", "executive", "employee", "referral")

# Day offsets from first contact, and the objective of each step. Sequencing
# is deliberately short: three touches over roughly two weeks, matching how
# an actual person follows up rather than an automated drip.
_DEFAULT_SEQUENCE = [
    {"step": 1, "channel": "EMAIL", "day_offset": 0,
     "objective": "introduce and flag genuine fit",
     "reason": "the first, most direct channel for a real message"},
    {"step": 2, "channel": "LINKEDIN", "day_offset": 4,
     "objective": "lightweight follow-up, easy to ignore without cost",
     "reason": "a second surface if email went unread, lower pressure"},
    {"step": 3, "channel": "EMAIL", "day_offset": 9,
     "objective": "final follow-up, or a clean close if no response",
     "reason": "one last direct attempt before moving on"},
]

# Referral and inbound conversations skip the cold-outreach cadence entirely
# -- the relationship already exists, so a scripted sequence would read as
# tone-deaf. This returns a single next-step entry instead of a drip.
_REFERRAL_SEQUENCE = [
    {"step": 1, "channel": "EMAIL", "day_offset": 0,
     "objective": "follow up on the referral directly, reference the connection",
     "reason": "a warm intro deserves a direct reply, not a cold sequence"},
]


def build_plan(person_type: str, channels: Optional[List[str]] = None
              ) -> List[Dict[str, Any]]:
    """Deterministic, no model. person_type shapes which sequence applies;
    channels, if given, restricts the sequence to those channels only
    (e.g. no email address known yet, LinkedIn only)."""
    person_type = (person_type or "").strip().lower()
    sequence = _REFERRAL_SEQUENCE if person_type == "referral" else _DEFAULT_SEQUENCE
    allowed = set(c.upper() for c in channels) if channels else set(CHANNELS)
    plan = [dict(step) for step in sequence if step["channel"] in allowed]
    if not plan:
        # Nothing in the requested channel set matched the default sequence;
        # fall back to a single step on whatever channel was actually given,
        # rather than silently returning an empty plan for a real request.
        first_channel = next(iter(allowed), "EMAIL")
        plan = [{"step": 1, "channel": first_channel, "day_offset": 0,
               "objective": "introduce and flag genuine fit",
               "reason": "only channel available for this contact"}]
    return plan
