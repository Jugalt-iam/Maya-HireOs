# Ingestion and gating

Serves the belief that the system says what it actually did, refuses rather
than guesses, and never presents a weaker result as the result.

This is a build spec for Maya OS. Everything here is to be written from
scratch. No code is carried in from anywhere.

---

## 1. Long jobs checkpoint

Indexing an archive is minutes of work that can die at any point. Today
`build_vectors()` is all or nothing: a failure at chunk 2000 of 2549 loses the
first 2000 and the next run starts at zero.

A checkpoint file records completed units as they complete. On start, the run
reads it and skips what is done. The default is to resume, because resuming is
almost always what is wanted, and `--rebuild` forces a clean pass.

The checkpoint records what finished, not what was attempted. A unit is
complete only after its vectors are on disk.

## 2. Embed batch and write batch are different numbers

The batch that goes to the embedder and the batch that goes to storage are
tuned by different constraints. The embedder is bound by request size and
timeout. Storage is bound by write cost.

We currently have one number, `EMBED_BATCH = 16`, doing both jobs, which means
tuning either one damages the other. Split them.

## 3. Identity comes from content

Every chunk's id is a hash of its content plus its source position. This buys
three things at once:

- a re-run upserts in place instead of duplicating,
- an interrupted run is safe to repeat,
- the same document processed twice is detected without a second pass.

ROUTING.md already requires that a document processed once is never processed
twice. This is the mechanism, and it is a field, not a subsystem.

## 4. Verify is a mode, not a side effect

`--verify` reads and reports and changes nothing: counts per collection, which
are empty, which are stale, total on disk. It must be safe to run at any time,
including mid-build.

Anything that reports should be runnable without permission to act.

## 5. Refuse rather than default

When configuration is ambiguous, stop and say which input was ambiguous and
what would have made it unambiguous. Do not pick the more likely option and
proceed.

A default chosen on the system's behalf is a decision made without the user,
and it will be discovered later, in output, when it is expensive.

## 6. Report by default, act on a flag

Any command that can change state reports by default and acts only when told
to. The report and the action share one code path, so what is reported is
exactly what would happen.

The exit code carries the verdict so it composes. Human-readable output by
default, machine-readable on request.

Preflight should work this way: it currently mixes checking with building.

## 7. Chunk on structure first, length second

Split on the document's own boundaries, headings before paragraphs before
sentences, and only fall back to length when a section exceeds the budget.
Carry one sentence of overlap across a forced split so a fact spanning the
boundary survives in one piece.

Our chunker is length-driven and cuts through headings. A chunk that begins
mid-argument retrieves badly no matter how good the embedding is.

## 8. Metadata splits universal from specific

Every chunk carries the same small universal set: source, position, content
hash, ingested-at, confidence. Everything beyond that varies by what the
document is.

The universal set is what search filters and dedup run against, so it must be
present on every chunk without exception. If a field is only sometimes there,
it cannot be part of the index.

## 9. A failed embed is a failed insert

Never write a placeholder vector. A zero vector has no direction, so it matches
nothing, forever, while the row count says the chunk was stored. That is
silent, permanent data loss that reports as success.

If the embedder fails, the insert fails, the failure is counted, and the
counter is printed at the end. A run that stored 2400 of 2549 says so.

## 10. A gate that always opens is a log line

Two rules, and they are the same rule.

**A failing verdict is never rewritten.** If something can be repaired
automatically, repair it and score the repaired version honestly as a new
attempt. Do not score, fail, patch, and then relabel the original verdict as a
pass. The record must show what actually happened.

**Presence is not quality.** Checking that a required word appears measures
whether a word appears. It is a useful precondition and a worthless score. Keep
those two things in different fields and never add them together.

This matters most where it is least convenient: the moment a gate blocks work
we want, it is doing its job.

## 11. One service, one client

One place that knows a service's base URL, API version, auth, timeout and
retry policy. Every caller goes through it.

Written inline at each call site, the copies drift, and the drift is invisible
until the day one of them is talking to a version that no longer exists.

---

## Order of work

| # | Item | Why first |
|---|---|---|
| 9 | Failed embed fails the insert | Silent data loss, cheapest fix |
| 3 | Content-hash ids | Unblocks 1, and dedup is already required |
| 1 | Checkpoint and resume | Makes the local build survivable |
| 2 | Split the batch sizes | One line, unblocks tuning |
| 7 | Structure-aware chunking | Retrieval quality, the whole point |
| 10 | Honest gates | Before any gate exists, so it is never retrofitted |
| 4, 6 | Verify mode, report by default | Preflight cleanup |
| 5, 8, 11 | Refuse, metadata, one client | Consolidation |
