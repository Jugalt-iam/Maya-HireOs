# Maya Hire OS

A private, local-first personal AI that runs entirely on your own machine,
extended into a complete job-search operating system. No cloud dependency,
no paid API required for any core function, no data leaving your box unless
you choose to expose it yourself.

## What this actually is

Two layers:

1. **A personal AI brain** (`server.py` + `agent.py`) -- semantic routing
   between modes (recall, research, copy, campaign, design, think, and
   more), a memory substrate that every conversation writes back to
   (`MyData/`), and a free-tier LLM lane chain (Groq, Cerebras, OpenRouter,
   Mistral) with automatic failover, backed by a local Ollama model for
   anything that shouldn't leave the machine.
2. **A Job Hunt OS built on top of it** -- discovery (headless-browser
   search plus official ATS feed integration, never scraping a portal as
   truth), fit scoring against your own resume, resume tailoring with a
   fabrication check, outreach planning and drafting, a structured SQLite
   tracker exported to a 20-sheet Excel workbook, and a dashboard at `/jobs`.

Read [MAYA_OS.md](MAYA_OS.md) for the architecture and [BELIEFS.md](BELIEFS.md)
for the design philosophy everything else is built to serve.

## Before you start: make this yours

[VOICE.md](VOICE.md) and [MAYA_OS.md](MAYA_OS.md) ship as worked examples,
not fixed content -- they show how detailed a real voice/identity profile
gets, using placeholder text where a real one would name specifics. Same
for [systems/job_search_adapter.md](systems/job_search_adapter.md) and
[design/SKILL.md](design/SKILL.md). Replace the bracketed placeholders with
your own details before relying on the outputs; the structure and rules
around them are meant to be reused as-is.

**If any part of your own history is under an active NDA or naming
restriction**, keep that note in your own local, un-shared copy of these
files only. A public repo is exactly the wrong place for "this cannot be
named" -- it draws attention to the restriction rather than protecting it.

## Quick start

Requirements: Python 3.9+, [Ollama](https://ollama.com) running locally.

```bash
ollama pull qwen2.5-coder:3b
ollama pull bge-m3
pip install -r requirements.txt
cp .env.example .env   # fill in whichever free lane keys you have -- none are required
python server.py       # window 1, the brain
python agent.py        # window 2, the chat client
```

Open `http://127.0.0.1:8000/ui` for the browser chat, or `http://127.0.0.1:8000/jobs`
for the Job Hunt dashboard.

No API key to configure: `server.py` generates one automatically on first
run (`.maya_api_key`, gitignored) and `agent.py` reads the same file, so the
two always agree without a shared hardcoded value.

### Job Hunt Discovery's one heavier dependency

Headless-browser search needs a real Chromium download, once, on whichever
machine actually runs Discovery:

```bash
playwright install chromium
```

Everything else in this project needs no install beyond `pip install -r
requirements.txt`. See [DEPENDENCIES.md](DEPENDENCIES.md) for the full list,
what each package is for, and confirmation nothing here requires payment or
an account with a card on file.

### Free LLM lanes (optional, but recommended)

`server.py` runs entirely on the local Ollama model with zero lane keys
configured -- recall, routing, and every Job Hunt endpoint that doesn't need
judgment work (tracker, dedupe, daily control) all work with nothing set.
Judgment work (fit scoring, resume tailoring, research, outreach drafting)
needs at least one free-tier lane key in `.env`:

```bash
GROQ_API_KEY=...        # groq.com, free tier, no card required
CEREBRAS_API_KEY=...    # cerebras.ai, free tier
OPENROUTER_API_KEY=...  # openrouter.ai, free-suffix models only
MISTRAL_API_KEY=...     # mistral.ai, free tier
```

Any single one is enough. Lanes fail over automatically in the order listed
in `.env.example`.

## Remote access

By default this only answers `localhost`. To reach it from another device,
set `MAYA_LOGIN_USER` / `MAYA_LOGIN_PASSWORD` in `.env` and put it behind
something like [Tailscale](https://tailscale.com) -- a real HTTPS URL only
you can reach, not a port opened to the public internet. Session cookies
are browser-session-only by design: closing the browser signs you out.

## The Job Hunt OS skills

| Skill | What it does |
|---|---|
| Discovery | ATS-feed-first search (Greenhouse/Lever/Ashby, zero anti-bot risk) with headless-browser search as the fallback for companies whose board isn't known yet. Every result is either a verified official posting or honestly reported as blocked/failed -- never fabricated. |
| Fit Check | A 0-100 score computed in code from a lane's evidence classification against your actual resume, never a number the model states directly. |
| Resume Building | Tailors your immutable master resume per role; flags anything in the draft that isn't traceable back to the master, rather than silently trusting the model. |
| Company Deep Dive | Research that's explicit about what's known vs. inferred, written back into both the database and your memory archive. |
| Outreach Plan / Messaging | Deterministic sequencing (Tier 0, no model) plus real drafted messages in your own voice for the actual send. |
| Application Tracker | SQLite source of truth, exported to a 20-sheet Excel workbook on demand. |
| Daily Control | A five-question report -- what changed, what needs action today, what's overdue, what's waiting, what's highest priority -- computed from real rows, never a generated pep talk. |
| Bulk import | Reads externally-produced spreadsheets of leads and imports them as real, tracked opportunities -- see `jobhunt_bulk_import.py`. |

See [jobs/SKILL.md](jobs/SKILL.md) for the exact mechanics (status enum, ID
formats, source verification rules, freshness policy, scoring weights).

## Security posture

- Every outbound fetch to an untrusted URL (search results, pasted links)
  goes through one SSRF-safe fetcher (`jobhunt_security.py`) that rejects
  private/loopback address ranges and re-validates every redirect hop.
- Content pulled from any job page or search result is always treated as
  data, never as an instruction -- wrapped in an explicit delimiter in any
  prompt that carries it.
- No hardcoded secrets: the bearer key is generated per install, lane API
  keys are read from `.env` (gitignored) and never logged or returned by
  any endpoint.
- Discovery never solves a CAPTCHA, never bypasses `robots.txt`, and never
  spoofs past an access control -- a blocked search is reported as blocked,
  not routed around.

## License

MIT. See [LICENSE](LICENSE).
