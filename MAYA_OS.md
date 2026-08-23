# MAYA_OS

> **Read [BELIEFS.md](BELIEFS.md) first. It is the constitution of this system.**
> Every file here must be consistent with it. When a design decision has two
> valid answers, BELIEFS.md decides, and the decision names the belief.
>
> **Then read [VOICE.md](VOICE.md). It is the identity and judgment layer.**
> Who you are, how you think, how you sound, how you judge work, and what you
> reject. Any output this OS produces, in any file, is written to VOICE.md.
> Its formatting rules in section 4 are absolute: no em dashes, no en dashes,
> no emoji. Section 7 is the acceptance test for any piece of work.
>
> BELIEFS.md decides what gets built. VOICE.md decides how it reads and whether
> it is good enough to hand over.

---

## What this is

Maya. A private brain that runs entirely on your own machine. No cloud, no API
bill, no data leaving the box.

- **Brain**: `server.py`. Routing, recall, write-back, Ollama. Runs on
  whatever machine you choose -- reachable remotely over Tailscale or
  similar if you want that, localhost-only if you don't.
- **Chat**: `agent.py` in the terminal, or `ui/index.html` in a browser at
  `/ui`. Two faces, one brain, one memory.
- **Substrate**: `MyData/`. What everything is recalled from *and* written back to.
- **Mind**: `systems/*.md`, `marketing/SKILL.md`, `smb/SKILL.md`. Behaviour lives
  in markdown, not code.

## Run it

Two windows. Window 1 stays open.

```
Window 1:  python server.py
Window 2:  python agent.py
```

Needs `ollama serve` running, with `qwen2.5-coder:3b` and `bge-m3` pulled.

Browser face instead of window 2, from any device on your tailnet:

```
http://<your-tailscale-ip>:8000/ui
```

## The three constants

```
API_KEY     = generated once per install, in .maya_api_key next to server.py
MODEL       = "qwen2.5-coder:3b"   local, text only, design as code
EMBED_MODEL = "bge-m3"             routing and search, by meaning
BASE_URL    = "http://127.0.0.1:8000/v1"
```

No hardcoded shared key, on purpose: every install gets its own, and
`agent.py` reads the same file automatically so the two always agree.

One model, not two. A model load costs 17.5 seconds on this hardware, so a
separate vision model would pay that on every switch. The tag is also the speed
dial: on a 16.7 GB/s bus, tokens per second is bandwidth divided by file size.

## Which belief each file serves

| File | Belief |
|---|---|
| `BELIEFS.md` | the constitution itself |
| `server.py` | **4** one substrate every answer returns to; **6** route before answering |
| `agent.py` | **6** loads one mode's instructions, not all of them; **3** shows the route so it can be corrected |
| `systems/opus_five_system.md` | **2** process that sharpens intelligence |
| `systems/fable_five_system.md` | **2** the same, for creative work |
| `marketing/SKILL.md` | **6** a destination the router enters |
| `smb/SKILL.md` | **6** a destination the router enters |
| `design/SKILL.md` | **6** the local destination, design as code |
| `ui/index.html` | **1** no build step; **3** shows the route and the memories used |
| `MyData/journal/` | **4** where generated knowledge arrives back |

A file that serves no belief should not exist. Question it before writing it.

## The two hard parts

### Arrival: Belief 4

Retrieve → generate → print → gone is a system that ends every day identical to
how it started. So every completed turn is appended to `MyData/journal/*.jsonl`,
which is the **same store the retriever indexes**. Today's answers are tomorrow's
memory, and they are recallable in the same session, not after a restart.

This is why there is no `/save` command. Writing to a folder the retriever cannot
read is generation without arrival, the exact failure Belief 4 names.

The archive is cached and the journal is read fresh, so a 61 MB export is never
re-parsed just because you had a conversation.

### Routing: Belief 6

Before answering, the brain decides which mode of understanding the problem
belongs to, then enters only that one:

`recall` · `design` · `teardown` · `copy` · `campaign` · `smb` · `think`

Each mode selects its own instruction files, temperature and recall weighting.
The old behaviour, concatenating Analyst + Copywriter + marketing + SMB into
one wall of text, was siloing by concatenation, not routing.

The route is **printed before every answer** and overridable with `/mode`
(**Belief 3**, a routing decision the human cannot see or correct is a decision
made on their behalf). Every route is written to the journal, so routing quality
becomes observable instead of assumed.

Routing is **semantic**. Each mode is described by a handful of natural
phrasings in `MODE_EXEMPLARS`, those are embedded once into a centroid, your
question is embedded, and the nearest one wins. Nothing has to be spelled the
way a keyword table expects: "find the vedic charts conversation" and "where did
we talk about kundli" land in the same place without either being listed.

The keyword table survives as a weak prior and as the fallback when `bge-m3` is
missing. When it falls back it says so, in the log and in the route line.

**To correct a wrong route, add a phrasing to `MODE_EXEMPLARS`, not a keyword.**
You are teaching it how a kind of question sounds, not spelling out words.

## Modes are weights, not walls

A mode changes how memory is *weighted*, never what is *visible*. A memory found
while writing copy stays reachable while planning a campaign.

Decided by **Belief 5**. Storage organised only by where something came from is
a silo with a filename, and siloed retrieval bakes the transfer problem into the
architecture.

## Changing how it thinks

Edit the markdown, not the code. `systems/*.md` and the `SKILL.md` files are
loaded fresh into the system prompt every turn. **Belief 1**, the code exists to
serve the thinking, not the reverse.
