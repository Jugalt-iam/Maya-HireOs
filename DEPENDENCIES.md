# Dependencies

What this project actually imports, and why each one earned its place
instead of being hand-rolled the way `.docx`/`.pptx`/`.xlsx` reading already
is. Nothing here requires payment, an account with a card on file, or a key
for core operation. Job Hunt Discovery's one required new capability
(headless browser search) is free, open-source software with no usage
ceiling of any kind.

## Already required before this extension

| Package | Purpose | License | Cost |
|---|---|---|---|
| `fastapi` | the HTTP API / server.py's app | MIT | free |
| `uvicorn` | ASGI server that runs FastAPI | BSD-3 | free |
| `requests` | outbound HTTP (Ollama, lane providers) -- was a hard, undeclared dependency before this pass, now listed explicitly | Apache 2.0 | free |
| `openai` (agent.py only, not server.py) | OpenAI-compatible client SDK for the CLI chat window | Apache 2.0 | free (talks to the local brain, not OpenAI's API) |

## Added for the Job Hunt OS extension

| Package | Purpose | License | Why not hand-rolled |
|---|---|---|---|
| `openpyxl` | writes the 20-sheet `JobHunt_Tracker.xlsx` (frozen headers, conditional formatting, hyperlinks, auto-filter) | MIT | The project already hand-rolls `.xlsx` *reading* (zipped-XML parsing, no dependency) because reading flat text is simple. Writing a real, styled, formula-capable workbook is a different problem; there is no reasonable stdlib path to it. |
| `playwright` (+ one-time `playwright install chromium`) | the primary, required Discovery search mechanism: headless Chrome driving DuckDuckGo's HTML search and reading JS-rendered career/ATS pages | Apache 2.0 | Real browser automation (JS execution, anti-bot-aware navigation) cannot be hand-rolled with `requests` + `html.parser`; a plain HTTP client cannot render or interact with a modern search results page. This is the one genuinely heavy install in the whole project (a real Chromium download), and it is the direct tradeoff for a search mechanism with no API key, no account, and no quota ceiling, ever. |

**No search-API SDK.** Any future optional Tavily integration would call its
REST API directly with `requests`, the same pattern already used for every
lane provider -- no new dependency for that either.

## Explicitly not added, and why

- `beautifulsoup4` -- `jobhunt_extract.py` hand-rolls HTML text and JSON-LD
  extraction with stdlib `html.parser`, matching this project's existing
  posture (`server.py`'s own `.docx`/`.pptx`/`.xlsx` readers are hand-rolled
  for the same reason: one fewer dependency for a problem stdlib already
  solves adequately).
- `APScheduler` / any scheduler library -- the brief's "daily discovery run"
  is served by a lightweight in-process loop using stdlib `threading`,
  already a hard dependency of `server.py`.
- Any paid or usage-billed API client of any kind (SerpAPI, Google Custom
  Search, Bing Search API, OpenAI/Anthropic API keys for judgment work) --
  the LLM lanes are free-tier-only by `lanes.py`'s own `FREE_TIER_ONLY`
  design, and Discovery's search layer is architected the same way.
- `sqlite3` (stdlib, not a dependency) -- the structured Job Hunt data layer
  uses Python's built-in `sqlite3` module, so the entire structured store
  (opportunities, jobs, companies, contacts, fit checks, etc.) adds zero new
  packages.

## Installation

On the **host machine only** (never the Mac this was developed on, per the
explicit build-machine/host-machine separation):

```bash
pip install -r requirements.txt
playwright install chromium
```

`requirements.txt` lists `openpyxl` and `playwright` alongside the existing
`fastapi`, `uvicorn`, `requests`. Everything else Job Hunt OS needs
(`jobhunt_*.py`, `jobs/SKILL.md`) is source in this repository, not a
package.
