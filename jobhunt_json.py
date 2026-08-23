#!/usr/bin/env python3
"""
jobhunt_json.py  --  the one place free-text JSON extraction lives.

Tier 0. No model. Used by any jobhunt module that has to pull a JSON value
out of a lane's free-text reply -- a fenced code block, prose before or
after it, whatever a smaller free-tier model actually produced instead of
the exact format asked for.

This used to be two separately-written, already-diverged copies (one in
server.py, one in jobhunt_fit.py). Consolidated here so both import the same
function instead of maintaining near-identical logic in two places that a
future fix would only reach one of.
"""

import json
from typing import Any, Dict, List, Optional, Tuple

__all__ = ["find_json_value", "extract_json_value", "extract_row_list",
          "find_all_json_objects"]


def find_json_value(text: str, chars: str = "[{"
                    ) -> Tuple[Optional[Any], Optional[int]]:
    """Finds the first genuinely valid JSON value embedded anywhere in free
    text, and where it starts.

    Tries an honest decode (json.JSONDecoder.raw_decode) starting at every
    character in `chars` found in the text, until one parses cleanly, rather
    than a regex that grabs from the first bracket to the last one in the
    whole response -- which breaks the instant any other bracket character
    appears anywhere in the surrounding prose, or the JSON has nested
    structure a non-greedy regex stops at too early.

    `chars` restricts what a match may start with: "[{" for either an array
    or an object (the general case), "{" alone when only an object is
    meaningful (e.g. splitting narrative prose from a trailing
    classification block, where an array match would be a false positive).

    Returns (value, start_index), or (None, None) if nothing decodes.
    """
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text or ""):
        if ch not in chars:
            continue
        try:
            value, _ = decoder.raw_decode(text, i)
            return value, i
        except json.JSONDecodeError:
            continue
    return None, None


def extract_json_value(text: str) -> Any:
    """find_json_value(), value only, either an array or an object."""
    value, _ = find_json_value(text, "[{")
    return value


def extract_row_list(value: Any, required_key: str = "") -> List[Dict[str, Any]]:
    """A parsed JSON value may be the list itself, or an object wrapping it
    under some key the model chose on its own (roles/rows/data/permutations
    are all common). Checks the obvious shapes rather than requiring one
    exact format."""
    if isinstance(value, list):
        candidates = value
    elif isinstance(value, dict):
        candidates = None
        for key in ("roles", "rows", "data", "permutations", "results", "items"):
            if isinstance(value.get(key), list):
                candidates = value[key]
                break
        if candidates is None:
            # a dict of sheet -> [rows] rather than a flat list
            flattened = []
            for v in value.values():
                if isinstance(v, list):
                    flattened.extend(v)
            candidates = flattened
        if not candidates and required_key and value.get(required_key):
            # find_json_value() can land on a single row object instead of
            # the whole array when the array itself failed to parse (a
            # truncated reply is a common cause). A single well-formed row
            # is real data, not nothing -- but if more than one row was
            # actually complete before the cutoff, the caller should use
            # find_all_json_objects() on the original text instead of this,
            # which only ever sees the one value it was handed.
            candidates = [value]
    else:
        candidates = []
    rows = [r for r in candidates if isinstance(r, dict)]
    if required_key:
        rows = [r for r in rows if r.get(required_key)]
    return rows


def find_all_json_objects(text: str, required_key: str = "") -> List[Dict[str, Any]]:
    """Recovers every complete top-level JSON object in free text, in the
    order they appear -- including ones that were meant to sit inside an
    array that never closed, which a truncated reply (cut off by a
    max_tokens limit mid-response) produces routinely. find_json_value()
    stops at the first successful parse; a truncated array fails to parse
    as a whole and that first success ends up being just the array's first
    row, discarding every other row that was actually complete before the
    cutoff. This scans the rest of the text too, so a reply that finished
    12 of 20 requested rows before running out of budget still yields 12
    real rows instead of 1.

    Scans forward from wherever the previous match ended (not from the next
    character), so a nested object inside an already-recovered row is never
    re-matched as if it were a separate top-level row.
    """
    decoder = json.JSONDecoder()
    found: List[Dict[str, Any]] = []
    i, n = 0, len(text or "")
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        try:
            value, end = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            i += 1
            continue
        if isinstance(value, dict) and (not required_key or value.get(required_key)):
            found.append(value)
        i = end
    return found
