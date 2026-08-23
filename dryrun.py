#!/usr/bin/env python3
"""
dryrun.py  --  check every lane before trusting any of them.

    python dryrun.py

Prints which lanes are live, which models each one is offering right now, and
the routing table by task class. Sends one tiny request per lane, nothing else.

SERVES (see BELIEFS.md and ROUTING.md):
  Belief 3  you see the routing decision and the live catalog, so you can
            correct them. A route you cannot inspect is a decision made on
            your behalf.
  Belief 4  the catalog is written to catalog/YYYY-MM-DD.json, so a provider
            silently deleting a model becomes visible history rather than a
            mystery failure weeks later.

It does NOT pick providers or order lanes. That is homemath's job. This only
reports what is there.
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import requests
except ImportError:
    print("FATAL: 'requests' is missing.  Run:  pip install requests")
    sys.exit(1)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
PROBE_TIMEOUT = 8
CALL_TIMEOUT = 25

# Name, base URL env var, default base URL, API key env var.
# Order matches config/providers.yaml. Google AI Studio is excluded.
LANES = [
    ("groq", "GROQ_BASE_URL", "https://api.groq.com/openai/v1", "GROQ_API_KEY", "GROQ_MODEL"),
    ("cerebras", "CEREBRAS_BASE_URL", "https://api.cerebras.ai/v1", "CEREBRAS_API_KEY", "CEREBRAS_MODEL"),
    ("openrouter", "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "OPENROUTER_MODEL"),
    ("mistral", "MISTRAL_BASE_URL", "https://api.mistral.ai/v1", "MISTRAL_API_KEY", "MISTRAL_MODEL"),
]

ROUTING_TABLE = [
    ("recall",         "what did I do, what did I decide", "1 local"),
    ("classify",       "sorting and labelling",            "1 local"),
    ("extract",        "parsing, OCR reading",             "1 local"),
    ("score",          "judgment, rubrics, fit",           "2 lane"),
    ("reason",         "logic, analysis, thinking",        "2 lane"),
    ("draft_internal", "copy, drafts, creation",           "2 lane"),
    ("consolidate",    "campaigns, planning",              "2 lane"),
]

MODE_MAP = [("recall", "recall", "local"), ("smb", "recall", "local"),
            ("teardown", "extract", "local"), ("copy", "draft_internal", "lane"),
            ("campaign", "consolidate", "lane"), ("think", "reason", "lane")]


def say(msg=""):
    try:
        print(msg, flush=True)
    except Exception:
        print(str(msg).encode("ascii", "replace").decode("ascii"), flush=True)


def load_dotenv():
    """Same ten lines as server.py. Existing environment always wins."""
    count = 0
    path = ROOT / ".env"
    if not path.is_file():
        return 0
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            count += 1
    return count


def probe_models(base, key):
    """GET /v1/models. Returns (ok, [ids], note)."""
    url = base.rstrip("/") + "/models"
    try:
        r = requests.get(url, timeout=PROBE_TIMEOUT,
                         headers={"Authorization": "Bearer " + key})
        if r.status_code == 401:
            return False, [], "401 key rejected"
        if r.status_code == 404:
            return False, [], "404 no /models endpoint"
        r.raise_for_status()
        data = r.json() or {}
        rows = data.get("data") if isinstance(data, dict) else None
        ids = [m.get("id") for m in (rows or []) if isinstance(m, dict) and m.get("id")]
        return True, ids, ""
    except requests.exceptions.Timeout:
        return False, [], "timeout after %ds" % PROBE_TIMEOUT
    except Exception as exc:
        return False, [], str(exc)[:70]


def ping_chat(base, key, model):
    """One tiny completion. Returns (ok, note, latency_ms)."""
    if not model:
        return False, "no model set in .env", 0
    url = base.rstrip("/") + "/chat/completions"
    body = {"model": model, "max_tokens": 5, "temperature": 0,
            "messages": [{"role": "user", "content": "Reply with the word: ok"}]}
    t0 = time.time()
    try:
        r = requests.post(url, json=body, timeout=CALL_TIMEOUT,
                          headers={"Authorization": "Bearer " + key,
                                   "Content-Type": "application/json"})
        ms = int((time.time() - t0) * 1000)
        if r.status_code == 429:
            return False, "429 rate limited or quota spent", ms
        if r.status_code != 200:
            detail = ""
            try:
                detail = str((r.json() or {}).get("error", ""))[:60]
            except Exception:
                detail = (r.text or "")[:60]
            return False, "HTTP %d %s" % (r.status_code, detail), ms
        data = r.json()
        msg = ((data.get("choices") or [{}])[0].get("message") or {})
        text = (msg.get("content") or "").strip()
        return True, (text[:20] or "empty"), ms
    except requests.exceptions.Timeout:
        return False, "timeout after %ds" % CALL_TIMEOUT, int((time.time() - t0) * 1000)
    except Exception as exc:
        return False, str(exc)[:60], int((time.time() - t0) * 1000)


def check_local():
    base = os.environ.get("LLM_HOST", "http://127.0.0.1:11434").rstrip("/")
    model = os.environ.get("LLM_MODEL_PRIMARY", "")
    try:
        r = requests.get(base + "/api/tags", timeout=6)
        r.raise_for_status()
        names = [m.get("name", "") for m in (r.json() or {}).get("models", [])
                 if isinstance(m, dict)]
        return True, names, model
    except Exception as exc:
        return False, [], str(exc)[:60]


def main():
    loaded = load_dotenv()

    say("")
    say("=" * 76)
    say("  MAYA_OS  --  lane dry run")
    say("=" * 76)
    say("  .env values loaded: %d" % loaded)
    if loaded == 0:
        say("  [WARN] no .env found. Copy .env.example to .env and add your keys.")

    try:
        import homemath
        say("  homemath: %s installed" % getattr(homemath, "__version__", "?"))
    except ImportError as exc:
        say("  homemath: NOT installed (%s)" % exc)
        say("            fix:  pip install homemath")

    # Tier 1 first. It is the floor and nothing else matters if it is down.
    say("")
    say("-" * 76)
    say("  TIER 1, local floor")
    say("-" * 76)
    ok, names, note = check_local()
    if ok:
        say("  [ok]   ollama up, %d model(s): %s" % (len(names), ", ".join(names) or "none"))
        want = os.environ.get("LLM_MODEL_PRIMARY", "")
        if want and want not in names:
            say("  [WARN] LLM_MODEL_PRIMARY=%s is not installed" % want)
            say("         fix:   ollama pull %s" % want)
    else:
        say("  [FAIL] ollama unreachable: %s" % note)
        say("         Recall stops working without this. Start Ollama first.")

    # Tier 2 lanes.
    say("")
    say("-" * 76)
    say("  TIER 2, free lanes  (probe + one 5-token call each)")
    say("-" * 76)
    say("  %-22s %-8s %-7s %-9s %s" % ("LANE", "KEY", "MODELS", "CALL", "NOTE"))
    say("  " + "-" * 72)

    catalog, live = {}, 0
    for name, url_env, url_default, key_env, model_env in LANES:
        key = os.environ.get(key_env, "").strip()
        base = os.environ.get(url_env, "").strip() or url_default
        model = os.environ.get(model_env, "").strip()

        if not key:
            say("  %-22s %-8s %-7s %-9s %s" % (name, "absent", "-", "-", "no key in .env, skipped"))
            catalog[name] = {"configured": False}
            continue
        if not base:
            say("  %-22s %-8s %-7s %-9s %s" % (name, "set", "-", "-", "no base URL, set " + url_env))
            catalog[name] = {"configured": False, "reason": "no base url"}
            continue

        p_ok, ids, p_note = probe_models(base, key)
        c_ok, c_note, ms = ping_chat(base, key, model)
        if c_ok:
            live += 1
        say("  %-22s %-8s %-7s %-9s %s" % (
            name, "set", (len(ids) if p_ok else "?"),
            ("%dms" % ms) if c_ok else "FAIL",
            c_note if c_ok else (c_note or p_note)))
        catalog[name] = {"configured": True, "base_url": base, "model": model,
                         "models_offered": ids, "probe_ok": p_ok,
                         "probe_note": p_note, "call_ok": c_ok,
                         "call_note": c_note, "latency_ms": ms}

    say("")
    say("  %d of %d lane(s) answered." % (live, len(LANES)))
    if live == 0:
        say("  Judgment work will report unavailable and queue. It will NOT")
        say("  silently fall back to the local model. That is by design.")

    # Where work runs.
    say("")
    say("-" * 76)
    say("  ROUTING TABLE")
    say("-" * 76)
    say("  %-16s %-36s %s" % ("TASK CLASS", "WHAT IT IS", "RUNS ON"))
    for cls, what, where in ROUTING_TABLE:
        say("  %-16s %-36s %s" % (cls, what, where))
    say("")
    say("  %-16s %-36s %s" % ("CHAT MODE", "TASK CLASS", "RUNS ON"))
    for mode, cls, where in MODE_MAP:
        say("  %-16s %-36s %s" % (mode, cls, where))
    say("")
    say("  Images always run local. Free lanes do not reliably accept them.")
    say("  Lane requests carry no <MEMORY> block unless LANE_SENDS_MEMORY is on.")

    # Belief 4: the catalog arrives somewhere it can be compared against later.
    try:
        folder = ROOT / "catalog"
        folder.mkdir(exist_ok=True)
        path = folder / ("%s.json" % datetime.now().strftime("%Y-%m-%d"))
        with open(str(path), "w", encoding="utf-8") as fh:
            json.dump({"checked_at": datetime.now().isoformat(timespec="seconds"),
                       "lanes": catalog}, fh, indent=2)
        say("")
        say("  Catalog written: %s" % path)
        say("  Compare against older files to see what a provider quietly removed.")
    except Exception as exc:
        say("  [WARN] could not write catalog: %s" % exc)

    say("=" * 76)
    say("")


if __name__ == "__main__":
    main()
