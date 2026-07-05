# Yin v2 (built from Ra)

**Read this first, every session.** This repo started as Ra's codebase and is
being transformed into Yin v2 — a locally-running Discord bot with layered
memory, autonomous rhythm, and reflective cognition. The transformation is
governed by two documents at this repo root:

- **DESIGN.md** — the full v2 spec (memory lanes, pipeline, tools, consult,
  build order). When in doubt, the design doc wins.
- **LESSONS.md** — the v1 post-mortem. Every principle below was paid for
  with a real failure. Do not relearn these the hard way.

Yin v1 lives at `~/Projects/yin` — **retired, reference only, never run it**.
Its CLAUDE.md opens with a retirement notice. A full archive sits at
`~/Projects/yin/yin-v1-archive.tar.gz`.

## Guiding principles (from LESSONS.md — non-negotiable)

- **Fuses and gates in code, affordances in language.** Every constraint is a
  code-level fuse, gate, or retrieval boundary — never a negative prompt
  instruction Yin can read. Prompt language enforces nothing and plants concepts.
- **Thinking and speaking are different channels.** Nothing produced mid-loop
  reaches Discord. The reply comes from one dedicated final model call with
  tools disabled. Architectural, not instructed.
- **Feed back tool calls and results only.** The model's own prose is never
  appended to loop history.
- **Evidence must be live.** Lesson/preference saves require a verbatim quote
  from the current conversation; recalled memory is not evidence.
- **Retrieval boundaries before schemas.** Separate stores per origin lane;
  each context reads only its own lane; cross-lane reads are explicit.
- **Saves earn their place.** Dedup + reinforcement over accumulation;
  per-theme rate fuses in code the model never reads.
- **Reflection is advisory.** It never blocks, rewrites, or touches state
  outside its own lane.
- **Ambient time points outward** (research, making, curating); prior
  self-reflection is capped in recall by code.
- **Paths derive from package location** (`Path(__file__)`), never process cwd.
- **Never return an empty dead end.** Failed lookups say what does exist.

## Build order (track progress here — update as steps complete)

1. ~~Get Ra onto GitHub (clean, no secrets)~~ ✅
2. ~~Clone to Mac~~ ✅ (this checkout)
3. ~~Strip sky, canvas, bridge, resident chat, sitecustomize, reindex_embeddings~~ ✅ (2026-07-05; image gen, web_search, plugins, heartbeat, day/night off, world_clock, library kept — IDLE-to-20-min change lands with the Ollama work)
4. ~~Swap Postgres → SQLite~~ ✅ (2026-07-05: bot_postgres.py and schema.sql deleted; yin/bridge.py YinStore is the drop-in — SQLite logs, JSON lanes/habitat/creations, honest answers for dropped machinery. cognition/bot now call self.store / cognition.store)
5. ~~Write Ollama model adapter~~ ✅ (2026-07-05: native /api/chat client in model_adapters.py — thinking → MIND channel, structured tool calls, keep_alive 30m, think-budget headroom so reasoning can't eat the reply. Verified live against qwen3.6:27b: chat + thinking + tool round trip. IDLE cut to 20 min. `.env` in place with v1's bot identity)
6. Verify clean Ra runs on Mac against local model, end to end — NEXT: `pip install -r requirements.txt`, then `python main.py`
7. ~~Add Yin's JSON memory stores~~ ✅ (2026-07-05: yin/memory/ — all lanes, evidence gate, dedup+rate fuse, retrieval boundaries, 19 tests; wiring into cognition comes with steps 4–6)
8. ~~Add ChromaDB layer on top of JSON stores~~ ✅ (2026-07-05: mirror.py — Chroma with pure-Python fallback, nomic-embed-text via Ollama, verified end-to-end on the Mac)
9. Add Neo4j store and KG consolidator
10. Add mentor.py (reflection pass)
11. Add dream cycle
12. Add scheduler
13. Add the dedicated final reply call to cognition
14. Add consult (cloud adapter, fuses, consult log)
15. Tests for each module before the next is built
16. Threshold Atlas is already tested — keep its suite running

## Environment

- Runs on this Mac against local Ollama: `qwen3.6:27b` (pulled), embeddings
  via `nomic-embed-text`.
- Discord bot token: v1's `.env` at `~/Projects/yin/.env` (gitignored) holds a
  working token — reuse that bot identity or mint a fresh one. Never commit
  secrets; use `.env.example` as the template.
- Immie is the human in the loop. She is still learning the Mac/terminal —
  explain shell steps plainly when asking her to run things.

## Working rules

- Each module gets tests before the next module is built; unit tests run
  without Discord or a live model (see DESIGN.md "Testing Principles").
- Update DESIGN.md when a design decision changes, and tick off build-order
  steps above as they land.
- When a v1 behaviour is worth consulting, read the code at `~/Projects/yin`
  — but port ideas through the DESIGN.md filter, never copy scaffold wholesale.
