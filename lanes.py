#!/usr/bin/env python3
"""
lanes.py  --  the provider chain. Six lanes, one active at a time.

Standalone by design. It imports nothing from Maya_OS and knows nothing about
modes, memory or routing. Config in, answer out. That is so it can be lifted
into homemath 0.2 as a file, unchanged, once it has earned its place here.

What it does, which homemath 0.1.1 does not:
  * builds N providers from N pairs of environment variables, not two from one
  * gives each provider its own API key, because these are different companies
  * picks the model per lane by task class: biggest for judgment, fastest for
    mechanical work
  * keeps a quota ledger, so an exhausted lane is skipped before it is called
    rather than discovered by failing five times
  * learns real limits from 429 headers and corrects the configured ones
  * raises AllLanesDepleted with the earliest reset time, so a caller can say
    when to come back instead of guessing

Sequential failover. One lane serves a request. If it is out, the next takes it.
They never run together.
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

__all__ = ["Lane", "Ledger", "LaneChain", "AllLanesDepleted", "build_chain"]

DEFAULT_TIMEOUT = 60

# FREE TIER ONLY. Never a paid tier, on any provider, for any reason.
#
# What this flag can enforce: openrouter is restricted to :free models, model
# ids that advertise a paid tier are refused, and any billing-shaped response
# kills that lane for the rest of the session rather than being retried.
#
# What it CANNOT enforce: if a payment method is attached to the account and
# the provider bills on overage, only the provider can stop that. The real
# guarantee is no card on file. Said plainly rather than implied.
FREE_TIER_ONLY = True

# Response text that means "this would cost money". Any of these and the lane
# is done for the session. Never retried, never paid into.
_BILLING_SIGNS = ("payment required", "insufficient credit", "insufficient_quota",
                  "billing", "add a payment", "upgrade your plan", "quota exceeded",
                  "requires a paid", "credits required", "purchase", "subscription")

# Model ids that are only served on a paid tier.
_PAID_MARKERS = ("-preview-paid", ":paid", "premium")
# Four lanes, because four is what there are keys for. A lane listed here
# without a key is not a fallback, it is a name that fails slightly later.
DEFAULT_ORDER = ["groq", "cerebras", "openrouter", "mistral"]

# name -> (base url env, key env, model env, default base url)
LANE_ENV = {
    "groq":                 ("GROQ_BASE_URL", "GROQ_API_KEY", "GROQ_MODEL",
                             "https://api.groq.com/openai/v1"),
    "cerebras":             ("CEREBRAS_BASE_URL", "CEREBRAS_API_KEY", "CEREBRAS_MODEL",
                             "https://api.cerebras.ai/v1"),
    "openrouter":           ("OPENROUTER_BASE_URL", "OPENROUTER_API_KEY", "OPENROUTER_MODEL",
                             "https://openrouter.ai/api/v1"),
    "mistral":              ("MISTRAL_BASE_URL", "MISTRAL_API_KEY", "MISTRAL_MODEL",
                             "https://api.mistral.ai/v1"),
}

# Task classes that need the strongest model on a lane rather than the quickest.
JUDGMENT_CLASSES = {"score", "reason", "consolidate", "judge", "strategy",
                    "analysis", "research"}

# --------------------------------------------------------------------------
#  Model discovery. You supply a key, not a model id.
#
#  Free tiers rotate their model lists constantly. A pinned id in .env goes
#  stale and the lane dies with a 404 for no visible reason. So each lane is
#  asked what it is serving right now, and the chain picks per task class.
# --------------------------------------------------------------------------

# Not chat models. Calling these with messages fails or wastes a request.
_NOT_CHAT = ("embed", "embedding", "whisper", "tts", "audio", "speech",
             "rerank", "moderation", "guard", "safety", "dall", "stable-diffusion",
             "flux", "image", "clip", "vision-encoder", "bge", "nomic")

# Names that signal a model built to reason. Preferred for judgment work.
_REASONING = ("r1", "reason", "thinking", "think", "qwq", "o1", "o3", "deepseek-r1")

import re as _re

_MOE = _re.compile(r"(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)\s*b\b", _re.I)
_DENSE = _re.compile(r"(\d+(?:\.\d+)?)\s*b\b", _re.I)


def model_size(model_id: str) -> float:
    """Billions of parameters read off the name. 0.0 when it does not say.

    Crude on purpose. The name is the only signal a /models list gives, and it
    is right often enough to rank by.
    """
    name = (model_id or "").lower().replace("_", "-")
    m = _MOE.search(name)
    if m:                                   # 8x7b, 8x22b: count the whole thing
        try:
            return float(m.group(1)) * float(m.group(2))
        except ValueError:
            return 0.0
    m = _DENSE.search(name)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return 0.0
    return 0.0


def usable_chat_models(ids: List[str], free_only: bool = False) -> List[str]:
    out = []
    for mid in ids or []:
        low = (mid or "").lower()
        if not low or any(bad in low for bad in _NOT_CHAT):
            continue
        if free_only and ":free" not in low:
            continue
        if FREE_TIER_ONLY and any(p in low for p in _PAID_MARKERS):
            continue
        out.append(mid)
    return out


def choose_models(ids: List[str], free_only: bool = False) -> Dict[str, str]:
    """Pick judgment, mechanical and default from a live model list.

    Judgment gets the biggest, with a thumb on the scale for models named as
    reasoners. Mechanical gets the smallest real chat model, because a fast
    lane is the whole point of mechanical work.
    """
    usable = usable_chat_models(ids, free_only)
    if not usable:
        return {}

    def judgment_key(mid: str) -> Tuple[float, float]:
        low = mid.lower()
        bonus = 20.0 if any(r in low for r in _REASONING) else 0.0
        size = model_size(mid)
        return (size + bonus, size)

    def mechanical_key(mid: str) -> Tuple[float, str]:
        size = model_size(mid)
        return (size if size > 0 else 999.0, mid)

    judgment = max(usable, key=judgment_key)
    mechanical = min(usable, key=mechanical_key)
    # The default is the judgment pick: better to be slow and right than fast
    # and wrong when the class is unknown.
    return {"judgment": judgment, "mechanical": mechanical, "default": judgment}


class AllLanesDepleted(RuntimeError):
    """Every lane is out of quota or unreachable.

    Carries enough detail for the caller to tell a human when to come back,
    which is the difference between an outage and a wait.
    """

    def __init__(self, lanes: List[Dict[str, Any]], earliest_reset: Optional[float]):
        self.lanes = lanes
        self.earliest_reset = earliest_reset
        names = ", ".join(l["name"] for l in lanes) or "none configured"
        RuntimeError.__init__(self, "all lanes depleted: " + names)


class Lane:
    """One provider endpoint, its keys, its models and its failure state."""

    def __init__(self, name: str, base_url: str, api_key: str,
                 models: Optional[Dict[str, str]] = None,
                 limits: Optional[Dict[str, Any]] = None,
                 timeout: int = DEFAULT_TIMEOUT):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.models = models or {}
        self.limits = limits or {}
        self.timeout = timeout
        self.consecutive_failures = 0
        self.last_error = ""
        self.disabled_reason = ""      # set once, never cleared this session

    @property
    def healthy(self) -> bool:
        return not self.disabled_reason and self.consecutive_failures < 3

    def model_for(self, task_class: str) -> str:
        """Biggest for judgment, fastest for mechanical, else the default."""
        if task_class in JUDGMENT_CLASSES and self.models.get("judgment"):
            return self.models["judgment"]
        if task_class not in JUDGMENT_CLASSES and self.models.get("mechanical"):
            return self.models["mechanical"]
        return self.models.get("default") or ""

    def probe(self, timeout: int = 8) -> List[str]:
        """Model ids this lane is advertising right now. Empty means unknown."""
        try:
            r = requests.get(self.base_url + "/models", timeout=timeout,
                             headers={"Authorization": "Bearer " + self.api_key})
            r.raise_for_status()
            data = r.json() or {}
            rows = data.get("data") if isinstance(data, dict) else None
            return [m.get("id") for m in (rows or [])
                    if isinstance(m, dict) and m.get("id")]
        except Exception as exc:
            self.last_error = str(exc)[:120]
            return []

    def __repr__(self) -> str:
        return "<Lane %s %s>" % (self.name, self.base_url)


class Ledger:
    """Per-lane usage, persisted, so an exhausted lane is skipped not called.

    Without this a dead lane is found by failing at it, which costs a round
    trip and the latency every single time.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.data: Dict[str, Dict[str, Any]] = {}
        self.load()

    def load(self) -> None:
        try:
            if self.path.is_file():
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            self.data = {}

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except Exception:
            pass

    def _row(self, name: str) -> Dict[str, Any]:
        row = self.data.setdefault(name, {})
        now = time.time()
        if now >= float(row.get("reset_at") or 0):
            row.update({"requests": 0, "tokens": 0,
                        "reset_at": self._next_utc_midnight(now)})
        row.setdefault("requests", 0)
        row.setdefault("tokens", 0)
        row.setdefault("cooldown_until", 0)
        return row

    @staticmethod
    def _next_utc_midnight(now: float) -> float:
        day = 86400.0
        return (int(now // day) + 1) * day

    def available(self, lane: Lane, reserve: float = 0.0) -> Tuple[bool, str]:
        """Can this lane take a request. reserve holds back a fraction for
        interactive use so a batch cannot spend the whole day's quota."""
        row = self._row(lane.name)
        now = time.time()
        if now < float(row.get("cooldown_until") or 0):
            wait = int(row["cooldown_until"] - now)
            return False, "rate limited, %ds left" % wait
        rpd = lane.limits.get("rpd")
        if rpd:
            cap = rpd * (1.0 - reserve)
            if row["requests"] >= cap:
                return False, "daily request limit reached"
        tpd = lane.limits.get("tpd")
        if tpd:
            cap = tpd * (1.0 - reserve)
            if row["tokens"] >= cap:
                return False, "daily token limit reached"
        return True, ""

    def record(self, name: str, tokens: int = 0) -> None:
        row = self._row(name)
        row["requests"] += 1
        row["tokens"] += max(0, int(tokens))
        row["last_used"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.save()

    def cooldown(self, name: str, seconds: float) -> None:
        row = self._row(name)
        row["cooldown_until"] = time.time() + max(1.0, seconds)
        self.save()

    def learn(self, name: str, headers: Dict[str, str]) -> None:
        """Correct stored limits from what the provider actually said.

        Learned beats configured: the numbers on a pricing page go stale, the
        headers on the response do not.
        """
        row = self._row(name)
        for key in ("x-ratelimit-limit-requests", "x-ratelimit-limit-tokens"):
            val = headers.get(key)
            if val and str(val).strip().isdigit():
                row["learned_" + key.rsplit("-", 1)[-1]] = int(val)
        reset = headers.get("x-ratelimit-reset-requests") or headers.get("retry-after")
        if reset:
            try:
                row["reset_hint_seconds"] = float(str(reset).rstrip("s"))
            except ValueError:
                pass
        self.save()

    def status(self) -> Dict[str, Dict[str, Any]]:
        return dict((k, dict(v)) for k, v in self.data.items())

    def earliest_reset(self, names: List[str]) -> Optional[float]:
        times = []
        for n in names:
            row = self.data.get(n) or {}
            for field in ("cooldown_until", "reset_at"):
                val = float(row.get(field) or 0)
                if val > time.time():
                    times.append(val)
        return min(times) if times else None


class LaneChain:
    """Ordered lanes. One serves the request, the next takes over when it cannot."""

    def __init__(self, lanes: List[Lane], ledger: Ledger, reserve: float = 0.0):
        self.lanes = lanes
        self.ledger = ledger
        self.reserve = reserve

    def names(self) -> List[str]:
        return [l.name for l in self.lanes]

    def chat(self, messages: List[Dict[str, Any]], task_class: str = "reason",
             temperature: float = 0.7, max_tokens: int = 2048,
             on_attempt=None) -> Tuple[str, str]:
        """Returns (lane_name, text). Raises AllLanesDepleted if none can serve.

        Never falls back to something weaker and calls it an answer. Either a
        lane produced the text, or the caller is told nobody could.
        """
        skipped: List[Dict[str, Any]] = []
        for lane in self.lanes:
            if lane.disabled_reason:
                skipped.append({"name": lane.name,
                                "reason": lane.disabled_reason, "reset_at": None})
                continue
            if not lane.healthy:
                skipped.append({"name": lane.name, "reason": "failing repeatedly",
                                "reset_at": None})
                continue
            ok, why = self.ledger.available(lane, self.reserve)
            if not ok:
                row = self.ledger.data.get(lane.name, {})
                skipped.append({"name": lane.name, "reason": why,
                                "reset_at": row.get("cooldown_until") or row.get("reset_at")})
                continue
            model = lane.model_for(task_class)
            if not model:
                skipped.append({"name": lane.name, "reason": "no model configured",
                                "reset_at": None})
                continue
            if on_attempt:
                on_attempt(lane.name, model)

            ok, text, tokens, retry_after = self._call(
                lane, model, messages, temperature, max_tokens)
            if ok:
                lane.consecutive_failures = 0
                self.ledger.record(lane.name, tokens)
                return lane.name, text
            if retry_after:
                self.ledger.cooldown(lane.name, retry_after)
                skipped.append({"name": lane.name, "reason": "rate limited",
                                "reset_at": time.time() + retry_after})
            else:
                lane.consecutive_failures += 1
                skipped.append({"name": lane.name,
                                "reason": lane.last_error or "call failed",
                                "reset_at": None})
        raise AllLanesDepleted(skipped, self.ledger.earliest_reset(self.names()))

    def _call(self, lane: Lane, model: str, messages: List[Dict[str, Any]],
              temperature: float, max_tokens: int
              ) -> Tuple[bool, str, int, float]:
        """One OpenAI-compatible request. Returns (ok, text, tokens, retry_after)."""
        body = {"model": model, "messages": messages,
                "temperature": temperature, "max_tokens": max_tokens}
        try:
            r = requests.post(lane.base_url + "/chat/completions", json=body,
                              timeout=lane.timeout,
                              headers={"Authorization": "Bearer " + lane.api_key,
                                       "Content-Type": "application/json"})
        except Exception as exc:
            lane.last_error = str(exc)[:160]
            return False, "", 0, 0.0

        try:
            self.ledger.learn(lane.name, dict((k.lower(), v) for k, v in r.headers.items()))
        except Exception:
            pass

        if r.status_code == 429:
            after = r.headers.get("Retry-After") or r.headers.get("retry-after")
            try:
                wait = float(str(after).rstrip("s")) if after else 300.0
            except ValueError:
                wait = 300.0
            lane.last_error = "429 rate limited"
            return False, "", 0, wait
        if r.status_code != 200:
            detail = ""
            try:
                detail = str((r.json() or {}).get("error", ""))[:200]
            except Exception:
                detail = (r.text or "")[:200]
            low = (detail or "").lower()
            # Anything that smells like money ends this lane for the session.
            # Free tier only means never paying into an overage by retrying.
            if r.status_code == 402 or any(sign in low for sign in _BILLING_SIGNS):
                lane.disabled_reason = "would cost money: %s" % detail[:90]
                lane.last_error = lane.disabled_reason
                return False, "", 0, 0.0
            lane.last_error = "HTTP %d %s" % (r.status_code, detail)
            return False, "", 0, 0.0

        try:
            data = r.json()
        except Exception as exc:
            lane.last_error = "bad json: %s" % str(exc)[:80]
            return False, "", 0, 0.0

        choices = data.get("choices") or []
        msg = (choices[0].get("message") or {}) if choices else {}
        text = msg.get("content") or ""
        if isinstance(text, list):     # some providers return content parts
            text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
        if not text.strip():
            lane.last_error = "empty response"
            return False, "", 0, 0.0
        usage = data.get("usage") or {}
        tokens = int(usage.get("total_tokens") or 0) or max(1, len(text) // 4)
        return True, text, tokens, 0.0

    def discover(self, state_dir: Optional[Path] = None) -> Dict[str, Any]:
        """Ask every lane what it serves today and pick models from that.

        You supply a key. The chain works out the rest. A lane that will not
        answer its /models endpoint keeps whatever was set in .env, and if
        there is nothing there it is dropped with the reason recorded, rather
        than kept as a lane that 404s on every call.
        """
        report: Dict[str, Any] = {}
        live: List[Lane] = []
        for lane in self.lanes:
            ids = lane.probe()
            free_only = lane.name == "openrouter"      # free-suffix models only
            picked = choose_models(ids, free_only) if ids else {}
            if picked:
                # anything explicitly set in .env still wins
                for slot, val in list(lane.models.items()):
                    if val:
                        picked[slot] = val
                lane.models = picked
            report[lane.name] = {
                "offered": len(ids), "error": lane.last_error,
                "judgment": lane.models.get("judgment"),
                "mechanical": lane.models.get("mechanical"),
                "source": "discovered" if picked else ("env" if lane.models else "none"),
            }
            if lane.models.get("default") or lane.models.get("judgment"):
                live.append(lane)
            else:
                report[lane.name]["dropped"] = (
                    lane.last_error or "no usable chat model offered")
        self.lanes = live
        if state_dir:
            try:
                folder = Path(state_dir) / "catalog"
                folder.mkdir(parents=True, exist_ok=True)
                stamp = datetime.now().strftime("%Y-%m-%d")
                (folder / (stamp + ".json")).write_text(
                    json.dumps({"checked_at": datetime.now().isoformat(timespec="seconds"),
                                "lanes": report}, indent=2), encoding="utf-8")
            except Exception:
                pass
        return report

    def probe_all(self, state_dir: Optional[Path] = None) -> Dict[str, Any]:
        """Ask every lane what it is serving today, and write it down.

        A provider silently deleting a model becomes visible history rather
        than a mystery failure three weeks later.
        """
        catalog: Dict[str, Any] = {}
        for lane in self.lanes:
            ids = lane.probe()
            wanted = [m for m in lane.models.values() if m]
            catalog[lane.name] = {
                "base_url": lane.base_url, "models_offered": ids,
                "configured": wanted,
                "missing": [m for m in wanted if ids and m not in ids],
                "error": lane.last_error,
            }
        if state_dir:
            try:
                folder = Path(state_dir) / "catalog"
                folder.mkdir(parents=True, exist_ok=True)
                stamp = datetime.now().strftime("%Y-%m-%d")
                (folder / (stamp + ".json")).write_text(
                    json.dumps({"checked_at": datetime.now().isoformat(timespec="seconds"),
                                "lanes": catalog}, indent=2), encoding="utf-8")
            except Exception:
                pass
        return catalog


def build_chain(state_dir: Path, reserve: float = 0.0,
                config: Optional[Dict[str, Any]] = None) -> LaneChain:
    """Build the ordered chain from environment variables.

    A lane with no key is skipped silently. You do not need all six: start with
    one and add more as you get keys.
    """
    order = [n.strip() for n in
             os.environ.get("HOMEMATH_LANE_ORDER", ",".join(DEFAULT_ORDER)).split(",")
             if n.strip() and n.strip() != "local"]
    lanes: List[Lane] = []
    cfg_lanes = (config or {}).get("lanes", {}) if isinstance(config, dict) else {}

    for name in order:
        spec = LANE_ENV.get(name)
        if not spec:
            continue
        url_env, key_env, model_env, url_default = spec
        api_key = os.environ.get(key_env, "").strip()
        if not api_key:
            continue
        base = os.environ.get(url_env, "").strip() or url_default
        if not base:
            continue
        entry = cfg_lanes.get(name) or {}
        models = dict(entry.get("models") or {})
        env_model = os.environ.get(model_env, "").strip()
        if env_model:                       # optional override, not required
            models["default"] = env_model
            models.setdefault("judgment", env_model)
        models = dict((k, v) for k, v in models.items() if v)
        lanes.append(Lane(name, base, api_key, models,
                          dict(entry.get("limits") or {})))

    return LaneChain(lanes, Ledger(Path(state_dir) / "lane_ledger.json"), reserve)
