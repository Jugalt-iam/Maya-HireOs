# TESTS.md

Run these in order. Each one isolates a single component, so a failure tells you
what broke rather than that something did.

Stop at the first failure and send me that block. Later tests assume the earlier
ones passed.

---

## 0. Before you start

```
ollama list
```

Expect exactly two: `qwen2.5-coder:3b` and `bge-m3`. Nothing else is needed and
`qwen3.5` and `qwen2.5vl` can go if they are still there.

Start the brain and **wait for the banner** before opening window 2. Half the
failures so far have been the agent starting first.

```
python server.py
```

---

## 1. Preflight

**Proves:** models present, archive parsed, lanes discovered, embeddings live.

Read the block it prints. All of these should be `[ok]`:

| Line | What it means if it is not ok |
|---|---|
| `ollama up` | service is down and could not be started |
| `brain qwen2.5-coder:3b` | model not pulled |
| `embed bge-m3` | model not pulled, routing falls back to keywords |
| `routing: semantic, bge-m3 (1024 dims)` | embeddings could not be built |
| `memory: 2549 chunks / 42 threads` | archive did not parse |
| `write-back on` | journal folder could not be created |
| `lanes: groq -> cerebras -> ...` | no lane key, or none reachable |

**Send me this whole block regardless of outcome.** It is the baseline for
everything else.

---

## 2. Retrieval, no model in the path

```
find the conversation related to vedic charts
```

**Proves:** archive search, routing to recall, retrieval answering with no
inference call at all.

**Expect:** an answer in under two seconds. Window 1 logs `route -> recall
[semantic]` then `tier -> retrieval` then `retrieval -> N hit(s), no model
called`.

**If it fails:** it is my code. Nothing else is involved. Send the traceback.

Then try it phrased differently, which is the actual test of semantic routing:

```
where did we talk about kundli
```

Same threads should come back. Neither "kundli" nor that phrasing appears
anywhere in the code. If this returns nothing, embeddings are not being used.

---

## 3. Journal write-back

```
/health
```

**Proves:** Belief 4. Look for `journal.turns`. After test 2 it should be `2`,
not `0`. If it is still `0`, lookups are not arriving in memory and the system
ends every day the way it started.

---

## 4. Design, the local coding model

```
design a quote card for linkedin, 1080x1080, with the line "intelligence over workflows"
```

**Proves:** the local coder model generates without the stack overrun that
killed both vision models.

**Expect:** one complete SVG file, cream background, amber accent, in your brand
tokens. Slow, 30 to 60 seconds.

**If it crashes with `0xc0000409`:** the vision encoder was never the cause and
I was wrong four times. Say so and we drop local generation entirely, routing
design to a lane too. Retrieval keeps working either way.

**If it produces an SVG:** save it and open it. The test is whether it renders,
not whether it is beautiful.

---

## 5. Lanes, a real provider

```
should i raise my prices this quarter
```

**Proves:** the chain reaches a real provider, picks the judgment model, and
returns text.

**Expect:** window 1 logs `tier -> lane` then `-> lane groq (openai/gpt-oss-120b)
for reason`, then a real answer within seconds.

**If it says all lanes depleted:** read which reason it gives per lane. Rate
limited, no model, or a billing message all mean different things and the
message names which.

---

## 6. Lane failover

Only if test 5 passed. In `.env`, break the first lane deliberately:

```
GROQ_API_KEY=broken
```

Restart, ask the same question. **Expect:** groq fails once, cerebras answers,
and the log shows the handover. This is the whole point of the chain and it has
never been observed.

Put the real key back afterwards.

---

## 7. The browser, from the Mac

```
http://<windows-tailscale-ip>:8000/ui
```

**Proves:** the UI, the glassbox, and access from the machine you actually work
on.

Ask the same recall question. Check the route chip appears above the answer and
the "N memories used" button opens and shows the threads.

---

## 8. Quota ledger

```
type .lanes\lane_ledger.json
```

**Proves:** usage is being counted, which is what stops a lane being called
after it is spent. Should show requests and tokens per lane after test 5.

---

## What "good enough to demo" means

Tests 1, 2, 3 and 5 passing is a working system worth showing: it knows your
archive, routes by meaning, and reasons through a free frontier model.

Test 4 failing is survivable. Design moves to a lane and the local model is
dropped.

Tests 2 or 5 failing is not survivable for a demo, because those are the two
things that make it interesting rather than a chat box.
