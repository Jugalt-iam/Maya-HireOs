# HOMEMATH-SPEC.md

What to add to `homemath` so Maya_OS can use six lanes instead of two.

This is a specification for **your** library, not code for this repo. Lane
ordering and failover belong in homemath, where every project you point at it
inherits them. Implemented here instead, they would be a silo, which
**Belief 5** forbids.

Target: homemath 0.2.0. Current: 0.1.1.

---

## What already works

More than I first credited. The machinery is there:

- `LLMProvider` carries name, url, api_key, model, timeout, speed_tier
- `_build_providers()` returns a `List[LLMProvider]`
- `HomemathEngine.race()` walks that list **in order**, per your own docstring
- `available` flips to `False` after 5 consecutive failures, moving the chain on
- `record_success()` and `record_failure()` already track per-provider state
- `_check_ollama_models()` already probes `/v1/models` on an endpoint

Sequential failover, one lane active at a time, is the behaviour you described,
and it is what `race()` already does.

---

## Change 1: build N providers, not 2

This is the whole job.

`_build_providers()` currently hardcodes two entries from `LLM_HOST`,
`LLM_HOST_FALLBACK` and a single shared `LLM_API_KEY`. It should build one
`LLMProvider` per configured lane, in priority order, skipping any lane whose
key is absent.

Environment variables, one pair per lane:

```
GROQ_BASE_URL        GROQ_API_KEY        GROQ_MODEL
CEREBRAS_BASE_URL    CEREBRAS_API_KEY    CEREBRAS_MODEL
OPENROUTER_BASE_URL  OPENROUTER_API_KEY  OPENROUTER_MODEL
MISTRAL_BASE_URL     MISTRAL_API_KEY     MISTRAL_MODEL
```

Plus `LLM_HOST` and `LLM_MODEL_PRIMARY` as today, which become the local floor
and sort last.

Rules:

- A lane with no key is skipped silently at build time and logged once.
- Order comes from an explicit `HOMEMATH_LANE_ORDER` (comma separated names),
  defaulting to the order above.
- Each provider keeps its own `api_key`. The current shared-key assumption has
  to go: these are six different companies.
- `speed_tier` stops meaning primary or fallback and becomes position in the
  chain.

Everything downstream, `race()` included, then works unchanged.

---

## Change 2: per-lane model preference by task class

One model per lane is not enough. A lane offers different models and the right
one depends on the task.

Two rules from ROUTING.md that the chooser must honour:

- **Judgment-class** tasks (`JUDGE`, `STRATEGY`, `ANALYSIS`, `RESEARCH`) go to
  the **largest** available model on the lane, not the fastest.
- **Mechanical-class** tasks (`SIMPLE`, `CHAT`, `GREETING`) go to the
  **fastest** lane that still has quota.

Suggested shape: `LLMProvider` accepts `models: Dict[str, str]` mapping task
class to model id, with a `default` key. `race()` already receives the messages,
so the class is available from `classify_task_class()`.

---

## Change 3: the quota ledger

The only genuinely new component. Without it a lane is discovered to be
exhausted by failing five times, which wastes five calls and the latency.

Per lane, persisted across restarts:

```
requests_used_today, tokens_used_today
requests_limit, tokens_limit      (RPD, TPD)
rpm_limit, tpm_limit
reset_at                          (UTC timestamp)
last_updated
```

Behaviour:

- Before selecting a lane, skip it if it is over any limit or inside a
  cooldown.
- After each call, add the usage. Token counts come from the response `usage`
  block where the provider returns one, otherwise estimate.
- On a 429, read `Retry-After`, `X-RateLimit-Reset`, `X-RateLimit-Remaining`
  where present and **correct the stored limits from what the provider actually
  says**. Learned limits beat configured ones.
- Reset counters at `reset_at`.
- Expose `homemath.quota_status()` returning per-lane used, remaining and
  reset time, so a caller can print the depleted message.
- Storage: a JSON file next to the config, or Redis when `REDIS_URL` is set.
  Tier 0 code, no model involved.

Reserve rule: accept a `reserve_fraction` (default 0.30) so batch work stops at
70% of a daily quota and leaves the rest for interactive use.

---

## Change 4: catalog probe on startup

`_check_ollama_models()` already does this for one endpoint. Run it across every
configured lane at startup, in parallel, with a short timeout.

- Route around any lane whose model list does not contain its configured model.
- Write the result to a dated file, `catalog/YYYY-MM-DD.json`, so a provider
  silently deleting a model becomes visible history rather than a mystery
  failure weeks later.
- A probe failure is not fatal. Unknown means proceed, as it does today.

---

## Change 5: the exhausted signal

`race()` currently converts `ProviderUnavailable` into
`("FAILED", <user-safe text>)`. Callers cannot tell "everything is out of
quota" from "everything is broken", and those need different handling.

Raise or return a distinguishable `AllLanesDepleted` carrying:

```
lanes:        [{name, reason, reset_at}]
earliest_reset: timestamp
```

Maya_OS uses this to queue judgment work and tell the user when to come back.
Without it the caller can only guess, and guessing produces a silent downgrade,
which is the one outcome VOICE.md section 8 rules out.

---

## What must NOT change

- `homemath_chat(messages, temperature, use_cache, priority) -> str` keeps its
  signature. Maya_OS calls it directly.
- The classifier, the policy maps and the token budgets stay as they are. They
  work.
- No new required dependency. `requests` plus optional `redis` is right.

---

## Order to build in

1. Change 1. On its own this delivers the six lanes and sequential failover.
   Everything else is refinement.
2. Change 5. Small, and it is what makes failure honest.
3. Change 3. The largest piece, and what turns wasted calls into skipped ones.
4. Change 2, then Change 4.

After Change 1 alone, Maya_OS needs no edits. It already calls
`homemath_chat()` and reads the lane list from `.env`.
