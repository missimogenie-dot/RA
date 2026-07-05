# Lessons from Yin v1 — what to keep, what not to replicate

Written 2026-07-05, after the rumination spiral and the reply-pipeline
failures. Verified against a second model: Grok 4.3 run in the same
scaffold reproduced the same issues, so these are scaffold lessons, not
model lessons. This file lives at the repo root, outside `yin/`, so the
running model never reads it — negation phrasing is safe here.

---

## The reply pipeline

1. **Never let "whatever text lacked tool syntax" be the reply.**
   v1's ReAct loop shipped the first response containing no `[TOOL:…]`
   call (cognition.py:94). Any stray reasoning, journal prose, or
   self-talk without tool syntax went straight to Discord. New build:
   thinking and speaking are different channels. The reply comes from
   one dedicated final call ("write the message you'll send"), and
   nothing produced mid-loop can ever reach the chat.

2. **Don't feed the model's own prose back as history.**
   v1 appended each step's full text to the working history
   (cognition.py:97), so one hallucinated `[Immie]: …` line became
   conversation the model then answered — talking to itself thinking it
   was her. Feed back tool calls and results only.

3. **Transcript-shaped context invites role-play — in every model.**
   Working memory rendered `Immie [ID:…]: … / Yin: …`, vector memories
   stored `User (Immie): … Yin: …`, input arrived as `[Immie]: …`. Give
   a language model a script and it continues the script: third-person
   narration about "Immie", fake turns, log-format mimicry. New build:
   render the model's own lines as "You:", keep speaker labels out of
   stored memory text where possible, and sanitize replies — strip
   leading speaker labels, truncate at any line that opens a fake turn.

## Prompts and language

4. **Negation plants the concept.** "Do not turn ambient time into a
   programme of self-improvement" produced a self-improvement programme
   within seven minutes of going live (first over-explaining lesson
   21:01, directive saved 20:54 on 2026-07-04). Affordances only, in
   every text the model can read — prompts, tool descriptions, tool
   results, and docstrings reachable via read_source.

5. **A self-audit question is an instruction to find the flaw.**
   Asking "did you over-center what the user wanted?" every exchange
   guarantees the answer is eventually yes, every time — 77 self-
   critical lessons in under two days. Reflection questions must point
   outward: what did the world show you, what worked, what turned out
   to be true. Self-notes framed as a naturalist's field notes, not
   verdicts.

6. **The model parrots the vocabulary of its own instructions.**
   "Over-center", "over-compliance", "rapport" appeared in prompts on
   July 4 and in saved lessons hours later. Any meta-commentary
   vocabulary put in front of the model will come back as content.

## Memory

7. **Gate at the storage layer, not per caller.** The people-pleasing
   filter guarded the reflection round only; most of the spiral arrived
   through the `add_lesson` tool, which was ungated. Every write path
   into a store shares one gate (v1 fix: `lesson_within_journal()`
   inside `LessonManager.add_lesson`).

8. **Recalled memory must not count as evidence for new memory.**
   The evidence gate (verbatim quote check — keep it) accepted quotes
   from the recalled-memory summary, so yesterday's rumination was
   valid evidence for today's. That closed the loop. Evidence must come
   from the live conversation only.

9. **Semantic dedup alone can't stop a theme.** The ≥0.92 reinforce
   threshold catches restatements, but a spiral generates endless
   *variations* that score 0.85–0.90 and pile up as new entries.
   Consider a per-theme rate fuse (code-level, invisible): N saves on
   one semantic cluster per day, the rest quietly held.

10. **Attribution metadata without retrieval filters is decoration.**
    v1 tagged everything (user_id, origin, "(your own time)" labels)
    but recall pulled top-k by similarity across the lot, so the tags
    changed nothing. Separate stores per origin — her conversations vs
    the bot's own research/notes — and each context reads only its own
    lane. Design retrieval boundaries first, then schemas.

## Ambient time and autonomy

11. **The ambient loop needs its own kernel.** v1's habitat reused the
    conversation system prompt (preferences about Immie, "your user"
    framing), priming user-focus during the bot's own time. Own system
    prompt, own memory lane, own channel.

12. **Self-scheduled tasks that mention the user become user role-play.**
    "Deliberately refrain from justifying actions in interactions with
    Immie" ran on a schedule with no Immie present — so the model
    simulated her. Task instructions must be self-contained; scheduled
    and ambient work never references a person who isn't in the loop.

13. **Idle loop + self-observation topics = rumination engine.** Each
    habitat cycle recalled the previous cycles' self-lessons and dug
    deeper, every 10–20 minutes, all night. The menu should point
    outward (research, making, curating the world-graph); when the
    model reflects, cap how much prior self-reflection gets recalled
    into that prompt.

## Tools and environment

14. **The tool failures were environmental, not conceptual.** Proven
    causes in v1: launchd's minimal PATH (binaries must be resolved
    absolutely), cwd confusion in the sandbox (`workspace/` prefix
    double-applied), and exact-path tools defeated by files living in
    subfolders. New build: every subprocess gets explicit cwd and
    absolute binaries; file tools resolve fuzzily; test under launchd,
    not just an interactive shell.

14b. **Never anchor data paths to the process cwd.** The killer bug:
    `WORKSPACE = Path("workspace").resolve()` (file_tools.py) resolves
    against whatever directory the process starts in. Launched from
    anywhere but the repo root, list_files/read_file see an empty
    phantom workspace while the real files sit one directory over —
    confirmed identically with Grok 4.3 on different hardware. The
    memory paths (`memory/core/*.json`) share the flaw. Every data
    path in the new build derives from the package location
    (`Path(__file__)`) or one explicit config root — the same reason
    read_source never broke.

15. **Tool volume was not a problem — the model asked for more.** Keep
    a rich registry; spend the effort on per-tool result quality
    instead of trimming the list.

16. **Never hand back an empty dead end.** A failed lookup that returns
    nothing gets a confabulated explanation. Every miss lists what DOES
    exist or what to try instead (v1's `_not_found` pattern — keep).

## What v1 got right — keep these organs

- **Fuses, not psychology**: step limits, tool budgets, the cognition
  lock (chat, habitat, and scheduled tasks can't trample each other),
  and code-level gates the model never reads. No punishment, quota, or
  superego machinery — the one time enforcement moved into prompt
  language it caused the worst incident in the project's history.
- **Evidence gate**: verbatim-quote check on saves (with lesson 8's
  scoping fix).
- **Hand-prunable JSON as source of truth** with a self-healing
  semantic mirror — pruning 73 lessons mid-flight cost nothing.
- **Advisors over enforcers**: the reflection round never blocks or
  rewrites; it only shapes what's carried forward.
- **Live-reloaded habitat menu**, rest as a genuine option.
- **Channel separation**: chat / thoughts (tool trace) / ambient /
  creations / logs. Made the spiral visible and diagnosable.
- **Plugin review gate**, human-approved, never bypassed.
- **No character identity** — a name, not a persona.

## Model choice (secondary to all of the above)

Fix the scaffold first — Grok 4.3 inherited every symptom, so no model
choice rescues a leaky pipeline. Then pick for temperament and tool
fluency: Qwen3 ~27–30B class for tool-call reliability, or a larger
Hermes for the least assistant-shaped disposition (small Hermes was
underwhelming; try the bigger builds). Gemma's RLHF self-blame reflex
contributed flavour to the spiral but not the mechanism. Swap points:
`OLLAMA_MODEL` and `MENTOR_MODEL` in config, plus a tool-syntax
compatibility pass and a kernel tone pass.
