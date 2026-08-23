# ROUTING.md

> Read [BELIEFS.md](BELIEFS.md) and [VOICE.md](VOICE.md) first. This file is
> subordinate to both.

This is an addition to Maya_OS, not a replacement. Memory, recall, the mode
router, the journal and both faces are unchanged. Only where a request is
executed changes.

---

## The division of responsibility

Two systems route, and they route different things. Keeping this line clean is
what stops either one from becoming a substitute for the other.

| Decides | Owner |
|---|---|
| Which mode of understanding (recall, copy, campaign, teardown, smb, think) | **Maya_OS** `server.py` |
| Which tier the work runs on (local or lane) | **Maya_OS** `server.py` |
| Which provider serves it, in what order, with what failover | **homemath** |
| Token budget and thinking mode per task class | **homemath** |

Maya_OS never picks a provider. homemath never picks a mode. Nothing in this
repo may implement lane ordering, lane failover or a provider chain. That logic
belongs in homemath, and putting it here would make it a silo, which
**Belief 5** forbids.

---

## Tiers

**Tier 0, deterministic code, no model.** File operations, parsing, dedupe,
schema validation, content hashing, the quota ledger. Everything that can be
code must be code.

**Tier 1, local.** Two small models, unlimited and offline. `bge-m3` embeds,
which routes every question by meaning and searches the archive semantically.
`qwen2.5-coder:3b` writes design as code. Neither writes prose about the user.

Routing is semantic, not keyword. Modes are described by natural phrasings,
embedded into centroids, and the nearest one wins. Correct a wrong route by
adding a phrasing to `MODE_EXEMPLARS`, never by adding a keyword.

**Tier 2, free lanes through homemath.** The intelligence layer at zero cost.
Thinking, logic, judgment, creation, tool use. Google AI Studio is excluded
from this system: not added, not suggested, not used as a fallback.

**Tier 3, frontier, existing Claude usage.** Final artifacts only. Letters,
tailored resume prose, LinkedIn. This OS stages the work and stops at the
handoff. It never calls Tier 3 itself.

---

## What runs where

The agreed rule:

- **RAG and storage** goes to retrieval. No model.
- **Thinking** goes to an API lane.
- **Doc creation** goes to an API lane, only.
- **Designing** stays local.

| Work | Tier | Model called |
|---|---|---|
| Recall: what did I do, what did I decide, find X | retrieval | **none** |
| Lookups: CRM, clients, past threads | retrieval | **none** |
| Designing: svg, canvas, generative art, layout | 1, local | local coding model |
| Thinking, logic, scoring, judgment | 2, lane | the lane |
| Doc creation: copy, campaigns, drafts | 2, lane | the lane |
| Reading an image | 2, lane | a vision-capable lane |

**Retrieval is the important row.** The archive is the answer. Asking a model
to write prose about retrieved threads adds nothing, costs a full inference
call, and is the only part of the recall path that can fail. It was removed.
Recall now returns in milliseconds and works with the local model stopped,
missing, or broken.

The local model is a text-only **coding** model, and it is used for exactly one
thing: making designs as code. SVG, HTML, CSS, Canvas.

There is no local vision model. Writing correct SVG is a coding task, and a 3B
coding model is good at coding in a way a 3B vision model is not good at seeing.
Dropping the vision encoder also removed the component that kept crashing this
CPU. Reading an image now needs a vision-capable lane, and photographic work
goes to Magnific or OpenArt.

---

## What may leave the machine

Default: **nothing from the archive**. A lane request carries the question and
the mode instructions. It does not carry the retrieved `<MEMORY>` block.

This is set by `LANE_SENDS_MEMORY` in `server.py`, default `False`. Turning it
on trades privacy for voice fidelity, and that trade is the user's to make
deliberately, not a default to inherit (**Belief 3**).

Never on a free lane under any setting: repository code, credentials, keys, and
personal files unrelated to work. When in doubt, it is sensitive and stays
local.

---

## Failure and degradation

Failover between lanes is homemath's job. What this OS does when homemath
reports every lane exhausted:

- **Mechanical-class work** falls to Tier 1. It is work the 4B can do.
- **Judgment-class work queues.** It is written to `queue/pending.jsonl` and
  reported in plain language, including which lanes are dead and when the first
  one returns.

Judgment work never silently downgrades to the 4B. A weaker answer presented as
the answer is the failure mode **VOICE.md section 8** names as the strongest
rejection trigger, and it is worse than no answer, because no answer is honest.

One account per provider, inside its own limits. No multiple accounts on a
single provider, no limit-dodging.

---

## Scheduling

- Quota ledger per provider, requests and tokens used today, in Tier 0 code.
- Each provider's reset time is stored, corrected from rate-limit response
  headers where the provider sends them.
- The overnight batch runs after each provider's reset, inside the 10pm to 5am
  IST idle window where possible.
- Interactive daytime work keeps headroom: batch never spends more than 70% of
  any lane's daily quota.
- Cache everything. A document processed once is never processed twice.
  Content-hash before any call.

The ledger lives in homemath, alongside the lane list it belongs to. See
[HOMEMATH-SPEC.md](HOMEMATH-SPEC.md).

---

## Status

Maya_OS side: built. `server.py` selects the tier and calls homemath for
lane work.

Lane side: **built**. `lanes.py` builds one provider per key pair in `.env`,
in the order set by `HOMEMATH_LANE_ORDER`, with a per-lane quota ledger, 429
handling that learns real limits from response headers, and an
`AllLanesDepleted` signal carrying the earliest reset time.

It is a standalone module with no imports from this project, so it lifts into
homemath 0.2 as a file, unchanged. That is the intended path: this is the
prototype, homemath is where it lives afterwards. Belief 5 is satisfied by
extraction, not by refusing to build it.
