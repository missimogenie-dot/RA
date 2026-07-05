# Yin v2 — Design Document

*Written 2026-07-05. Reference during build. Update as decisions are made.*
*Merged 2026-07-05: the consult section (formerly a separate file) is now the "Consult — Asking for Help" chapter below.*

## What Yin Is

A locally-running Discord bot with layered memory, autonomous rhythm, and reflective cognition. Yin is not a persona — it has a name, not a character installed by prompt. Its identity, if any, forms slowly through its own work and reflection, not through human instruction.

Yin runs on the Mac against a local Ollama model (Qwen3 27B class or similar). The Discord interface is the same as Ra's. The human who talks to Yin is Immie.

## Guiding Principles

These come directly from the v1 lesson document and govern every design decision. When in doubt, return to these.

**Fuses and gates in code, affordances in language.** Enforcement belongs in the scaffold. Prompt language enforces nothing and plants concepts. Every constraint is a code-level fuse, gate, or retrieval boundary — never a negative instruction Yin can read.

**Thinking and speaking are different channels.** Nothing produced mid-loop reaches Discord. The reply comes from one dedicated final model call with tools disabled. This is architectural, not a prompt instruction.

**Feed back tool calls and results only.** The working history between loop rounds contains tool calls and tool results. The model's own prose is never appended to history mid-loop.

**Evidence must be live.** The reflection evidence gate (verbatim quote check) accepts only text from the current conversation. Recalled memory cannot serve as evidence for new memory saves.

**Retrieval boundaries before schemas.** Separate stores per origin. Each context reads only its own lane. Tags and metadata are decoration unless retrieval is designed around them.

**Saves earn their place.** Deduplication and reinforcement over accumulation. Per-theme rate fuses in code. The model never reads the fuse logic.

**Reflection is advisory and evidenced.** The reflection round shapes what is carried forward. It never blocks, rewrites, or directly modifies state outside its own lane.

**Ambient time points outward.** The habitat menu orients toward the world: research, making, curating. When Yin reflects during ambient time, prior self-reflection is capped in the recall window for that prompt.

**Paths derive from package location.** Every data path is anchored to `Path(__file__)` or one explicit config root. Nothing resolves against process cwd.

**Never return an empty dead end.** Every failed lookup states what does exist or what to try instead.

## What Crosses from Ra (Keep)

These Ra modules are trusted, tested, and cross over as-is or with minor adaptation:

| **Module** | **Notes** |
|:---|:---|
| bot.py | Discord handling, DMs, commands, reactions — keep whole |
| model_adapters.py | Add Ollama adapter; keep existing structure |
| influence_router.py | Identity/role pressure routing — keep whole |
| cognition.py | Keep the `_run_loop` architecture exactly; strip sky, canvas, bridge, resident chat tool handlers |
| prompt_builder.py | Keep structure; rewrite content blocks for Yin |
| config.py | Adapt env vars for Yin; remove Ra-specific keys |
| main.py | Keep entry point structure |
| runtime.py | Adapt wiring; remove Postgres, sky, canvas, bridge |
| threshold_atlas/ | Keep whole — standalone, tested, no dependencies |
| library/ | Keep — reading corpus works fine |
| codebase_rw.py | Keep — Yin reading its own source is a good feature |
| Image generation (create_image), websearch, voice gen where possible | Keep — its part of creative cycles |

## What Gets Stripped from Ra

Remove entirely — do not port:

- sky.py and all sky references — too influential on output
- canvas.py and all canvas references — not needed for v1 Yin
- bridge_mailbox.py — inter-bot messaging, not relevant here
- bot_postgres.py and schema.sql — replaced by SQLite + JSON
- Resident chat tools (resident_chat_read, resident_chat_send)
- Day/night cycle (day_night.py) — dream cycle handles time differently

## What Gets Added (Yin's Organs)

These are new modules that do not exist in Ra:

| **Module** | **Purpose** |
|:---|:---|
| memory/chroma_store.py | ChromaDB vector store — semantic search across all memory lanes |
| memory/neo4j_store.py | Neo4j knowledge graph — fact nodes and relationships |
| memory/lessons.py | Lesson store with evidence gate and per-theme rate fuse |
| memory/goals.py | Goal store, semantically indexed, dedup-reinforcing |
| memory/preferences.py | Preference store, semantically indexed |
| memory/autobiography.py | Autobiography / journal — append-only narrative log |
| memory/timeline.py | Timeline of significant events, ordered |
| memory/working.py | Working memory — current session context, salience-scored |
| kg_consolidator.py | Extracts facts into Neo4j every N conversation turns |
| dream_cycle.py | 3am consolidation — salience scores working memory, condenses to autobiography, prunes orphan graph nodes |
| scheduler.py | Yin-created recurring tasks via schedule_task tool |
| mentor.py | Post-exchange reflection — advisory only, evidence-gated, never blocks |

## Memory Architecture

### Stores and Lanes

Every store is a separate origin lane. Retrieval is designed around lane boundaries first. Cross-lane reads are explicit, never implicit.

```
yin/memory/
├── human/
│   └── {discord_user_id}.json   # human facts, keyed by user
├── lessons.json                 # Yin-originated, evidence-gated
├── goals.json                   # Yin-originated, semantically indexed
├── preferences.json             # Yin-originated, semantically indexed
├── autobiography.json           # append-only narrative log
├── timeline.json                # significant events, ordered
├── working.json                 # current session, salience-scored
└── kg/
    └── (Neo4j connection config)
```

ChromaDB collections mirror the lanes:

- human_memory — per-user facts
- lessons — Yin's lessons
- goals — Yin's goals
- preferences — Yin's preferences
- world_knowledge — facts extracted by KG consolidator

### Deduplication and Reinforcement

On every save attempt:

1. Semantic search against the target collection for near-matches
2. Score ≥ 0.92 → reinforce existing entry (increment weight, update timestamp) rather than creating a new one
3. Score 0.85–0.91 → per-theme rate fuse check. If N saves on this semantic cluster have occurred today, hold quietly. Fuse logic is code-only, never visible to the model.
4. Score < 0.85 → save as new entry

### Evidence Gate (Lessons and Preferences)

Saves to lessons and preferences must include a verbatim quote from the **current live conversation only**. The gate rejects:

- Saves with no verbatim evidence
- Saves whose evidence text matches recalled memory rather than live text
- Saves during ambient/dream cycles (no live conversation present)

This gate lives inside `LessonManager.add_lesson()` and `PreferenceManager.add_preference()`. It is not a prompt instruction.

### Retrieval Boundaries

| **Context** | **Can read** | **Cannot read** |
|:---|:---|:---|
| Chat response | human lane (this user), lessons, goals, preferences, working | autobiography (private), other users' human lane |
| Reflection | live conversation + above | recalled memory as evidence |
| Ambient cycle | lessons, goals, preferences, autobiography, world_knowledge | human lane (no user present) |
| Dream cycle | working, autobiography, world_knowledge | human lane |
| Scheduler tasks | goals, lessons | human lane, autobiography |

## Pipeline

message → recall → `_run_loop` (tools) → final reply call → observe → mentor (async)

### Step by step

**1. Recall** (before the loop starts) Semantic search across relevant lanes for the current message. Results are injected into the system prompt context block. Working memory is also injected. This is read-only — nothing is written at recall time.

**2. `_run_loop`** (Ra's loop, kept exactly)

- Rounds up to MAX_TOOL_ROUNDS
- Each round: model call → extract tool calls → dispatch → append tool calls and results only to history (never the model's prose)
- If no tool calls: loop ends, text is held as candidate reply (not sent)
- If round limit reached: one final tool-free round for closing text

**3. Final reply call** (separate, dedicated) After the loop completes, one fresh model call with tools disabled receives the loop's tool log summary and produces the Discord reply. Nothing from mid-loop prose can reach this call's output — it sees only tool results and the original message.

**4. Observe** Reply is logged to working memory and the timeline. Conversation turn counter is incremented. If counter % 5 == 0, KG consolidator is queued.

**5. Mentor / reflection** (async, non-blocking) After reply is sent, mentor.py runs a reflection pass. It may save a lesson or preference if evidence gate passes. It shapes what is carried forward. It never blocks the reply, never rewrites state, never touches the human lane.

## Cognition Loop Detail

Ra's `_run_loop` is kept architecturally identical. Key properties that must be preserved:

- Tool calls and results fed back, never prose
- `last_text` tracks the most recent model text but it is NOT the reply
- The reply comes from the dedicated final call (new in Yin, not in Ra)
- Tool budget and round limit are code fuses, not prompt instructions
- Failed tools return what does exist, never an empty dead end

The final reply call is the key addition over Ra:

```python
async def _final_reply(
    self,
    system_prompt,
    original_message: str,
    tool_summary: str,
) -> str:
    # One call, no tools, produces the Discord reply
    # tool_summary is a compact log of what tools returned
    # original_message is the human's text
    # model's own mid-loop prose is never passed in
    ...
```

## Habitat and Ambient Cycle

Yin's ambient cycle replaces Ra's, stripped of sky/canvas/day-night influences. The menu is affordance-only, pointing outward:

```yaml
# yin/prompts/habitat.yaml
research: Follow something interesting into the world.
reflect: Look at what has accumulated — what holds, what has shifted.
curate: Tend the knowledge graph or memory stores.
read: Read something from the library.
write: Make something — a note, image, voice, a fragment, an observation.
play: Take a turn in Threshold Atlas.
rest: Nothing required. Observe and let time pass.
```

**Rules for ambient prompts:**

- Own system prompt, own memory lane, no human lane in context
- No references to Immie or any person not in the loop
- When reflect is chosen, prior self-reflection is capped at 2 entries in the recall window (code-level cap, not a prompt instruction)
- Ambient work never produces lessons — the evidence gate requires a live conversation

## Dream Cycle

Runs at 3am (scheduled, not ambient loop). Separate from the habitat cycle. Steps:

1. **Salience scoring** — score each working memory entry by recency, reference count, and semantic centrality
2. **Condense** — high-salience entries are summarised and appended to autobiography; low-salience entries are dropped from working memory
3. **KG prune** — orphan nodes in Neo4j (no edges, not referenced in recent turns) are removed
4. **Autobiography append** — a short narrative paragraph about the day is written and appended; never overwrites existing entries

Dream cycle has its own system prompt. It does not use the chat system prompt. It does not reference Immie.

The dream cycle may route condensation through consult — a larger model summarises a day of working memory well. Autobiography stays in Yin's own words: consult output is material, the append is Yin's. (See "Consult — Asking for Help" below.)

## KG Consolidator

Runs every 5 conversation turns (triggered by the turn counter in observe). Separate async task, non-blocking.

Takes the last 5 turns of conversation and:

1. Extracts factual claims as subject-predicate-object triples
2. Checks for existing nodes/edges in Neo4j before inserting
3. Links new nodes to existing ones where relationships exist
4. Does not extract claims about Yin's identity or internal state — world knowledge only

Facts about Immie go into the human lane (JSON + ChromaDB), not the KG. The KG is for world knowledge.

## Scheduler

Yin can create its own recurring tasks via the schedule_task tool. Rules enforced at the scheduler layer (code, not prompt):

- Task instructions must be self-contained
- Task instructions cannot reference a named person (Immie or anyone)
- Scheduled tasks run against the ambient system prompt, not the chat system prompt
- Scheduled tasks cannot trigger the lesson/preference save path (no live conversation present, evidence gate will reject)

Example of a valid scheduled task: "Research something from the knowledge graph that has no outgoing edges and add context."

Example of an invalid scheduled task (rejected at storage): "Think about what Immie might want to know tomorrow."

## Channel Model

Inherited from Ra, unchanged:

| **Channel**        | **Purpose**                      |
|:-------------------|:---------------------------------|
| CHAT_CHANNEL_ID    | Primary Yin chat                 |
| MIND_CHANNEL_ID    | Thoughts / tool trace (internal) |
| LOGS_CHANNEL_ID    | Runtime / error log              |
| AMBIENT_CHANNEL_ID | Habitat cycle output             |
| CREATES_CHANNEL_ID | Creations (writing, fragments)   |
| GAMES_CHANNEL_ID   | Threshold Atlas                  |
| CURATOR_CHANNEL_ID | Admin commands                   |

## Tools

### Chat response tools (available in `_run_loop`)

| **Tool**            | **Purpose**                           |
|:--------------------|:--------------------------------------|
| recall_memory       | Semantic search across relevant lanes |
| recall_lessons      | Retrieve relevant lessons             |
| recall_goals        | Retrieve current goals                |
| recall_preferences  | Retrieve preferences                  |
| save_human_memory   | Store a fact about the current user   |
| working_memory_read | Read current working memory           |
| working_memory_add  | Add to working memory                 |
| kg_search           | Search Neo4j knowledge graph          |
| timeline_read       | Read recent timeline entries          |
| library_list        | List library texts                    |
| library_read        | Read pages from a library text        |
| code_list           | List own source files                 |
| code_read           | Read own source file                  |
| schedule_task       | Create a recurring scheduled task     |
| web_search          | Search the web (via local or API)     |
| game_status         | Read Threshold Atlas state            |
| game_act            | Take a Threshold Atlas action         |
| react_to_message    | Add a Discord reaction                |
| event_log           | Log an event                          |

### Ambient-only additional tools

| **Tool**             | **Purpose**                            |
|:---------------------|:---------------------------------------|
| autobiography_read   | Read recent autobiography entries      |
| autobiography_append | Append a fragment (ambient/dream only) |
| kg_add_fact          | Add a fact triple to Neo4j             |
| kg_prune             | Remove orphan nodes                    |
| creation_store       | Store a poem or piece of writing       |
| creation_recent      | Read recent creations                  |
| habitat_snapshot     | Read habitat state                     |
| habitat_event        | Place habitat residue                  |
| schedule_list        | List current scheduled tasks           |
| schedule_cancel      | Cancel a scheduled task                |
| create_image         | Create an image                        |
| consult              | Put one self-contained question to the larger model (see "Consult — Asking for Help") |
| consult_log_read     | Read recent consults and responses     |

### Mentor / reflection tools (reflection pass only)

| **Tool**       | **Purpose**                                |
|:---------------|:-------------------------------------------|
| add_lesson     | Save a lesson (evidence gate enforced)     |
| add_preference | Save a preference (evidence gate enforced) |
| update_goal    | Update a goal status                       |

## Database and Storage

| **Store**       | **Technology**   | **Format**                              |
|:----------------|:-----------------|:----------------------------------------|
| Human memory    | JSON + ChromaDB  | Per-user JSON, vector index             |
| Lessons         | JSON + ChromaDB  | Flat list, vector index                 |
| Goals           | JSON + ChromaDB  | Flat list with status, vector index     |
| Preferences     | JSON + ChromaDB  | Flat list, vector index                 |
| Autobiography   | JSON             | Append-only list of dated entries       |
| Timeline        | JSON             | Ordered list of events                  |
| Working memory  | JSON             | Session-scoped, salience-scored         |
| World knowledge | Neo4j + ChromaDB | Graph + vector index                    |
| Event log       | SQLite           | Replaces Ra's Postgres events table     |
| Tool call log   | SQLite           | Replaces Ra's Postgres tool_calls table |
| Consult log     | SQLite           | Every consult question and response     |
| Habitat state   | JSON             | Per-area state, Ra's format             |
| Scheduler       | SQLite           | Task definitions and schedule           |

All JSON paths derive from `Path(__file__)` — never from process cwd.

SQLite is used only for logs and scheduler (append-heavy, structured, not hand-prunable). Everything hand-prunable stays JSON.

## Model Configuration

```
INSTANCE_NAME=Yin
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_CHAT_MODEL=qwen3:27b       # or hermes-3 equivalent
OLLAMA_AMBIENT_MODEL=qwen3:27b    # can differ
```

The Ollama adapter wraps the OpenAI-compatible Ollama API endpoint. Tool calling uses Ollama's native structured tool call format — not `[TOOL: name(args)]` text parsing.

## Consult — Asking for Help

Yin can put a hard question to a larger model over an API — the way you ask a teacher. The remote model advises; Yin decides and speaks. It never talks to Discord and never speaks as Yin.

### Availability

`consult` appears in the **ambient and dream toolsets only**. It is absent from the chat toolset and from the reflection pass — chat replies are Yin's own, and a large model critiquing from partial context is a cleverer critic, not a better one. The boundary is architectural, not instructed.

### One-shot, stateless

The remote model keeps no session and no memory. Each consult is one self-contained question → one response. Follow-ups are new consults: Yin reads its consult log, and composes whatever context the next question needs into the question itself.

### Composed payload only

The wire carries exactly what Yin deliberately writes into the tool call. Nothing is auto-attached — no working memory, no human lane, no memory dump. The scheduler's named-person gate applies to consult payloads too: a question referencing a named person is rejected at the tool layer (code, not prompt). Yin's memory stays on the Mac; what crosses the wire is one deliberate question.

### Fuses (code-level)

- Daily consult budget (`CONSULT_DAILY_BUDGET`)
- Per-call timeout; capped retries (`CONSULT_MAX_RETRIES=2`); then a soft redirect — the failure message names what still works (library, web search, own reasoning). Never a dead end.

### Consult log

Every consult and its response is appended to a local log (SQLite, alongside the tool-call log). New ambient tool: `consult_log_read`. The log is the "history" — held locally, curated by Yin into its next question, never attached by the adapter.

### Adapter layer (revised decision)

Keep Ra's `model_adapters.py`, slimmed to two paths:

| Path | Role |
|---|---|
| Native Ollama client | Primary brain — chat, ambient, mentor, dream. Thinking separated (`message.thinking` → MIND channel), never in reply content. |
| One cloud adapter | `consult` only. Anthropic or OpenAI-compatible, chosen by env. |

### Config

```
CONSULT_PROVIDER=anthropic        # or openai-compatible
CONSULT_MODEL=...
CONSULT_DAILY_BUDGET=8
CONSULT_MAX_RETRIES=2
```

## What v1 Got Right (Keep These)

From the lesson document — these are proven and must survive the port:

- **Fuses not psychology** — step limits, tool budgets, cognition lock, code-level gates the model never reads
- **Evidence gate** — verbatim quote check on lesson/preference saves, scoped to live conversation only
- **Hand-prunable JSON** with self-healing semantic mirror
- **Advisors over enforcers** — reflection never blocks or rewrites
- **Channel separation** — chat / thoughts / ambient / creates / logs
- **Plugin review gate** — human-approved, never bypassed
- **No character identity** — a name, not a persona
- **Live-reloaded habitat menu** — rest is a genuine option
- **Rich tool registry** — tool volume was not the problem; keep it

## Build Order

1. Get Ra onto GitHub from Windows (clean commit, no secrets)
2. Clone to Mac
3. Strip sky, canvas, bridge, resident chat, image gen, day/night
4. Swap Postgres → SQLite for event log and tool call log
5. Write Ollama model adapter (OpenAI-compatible endpoint)
6. Verify clean Ra runs on Mac against local model, end to end
7. Add Yin's JSON memory stores (autobiography, lessons, goals, preferences, timeline, working memory)
8. Add ChromaDB layer on top of JSON stores
9. Add Neo4j store and KG consolidator
10. Add mentor.py (reflection pass)
11. Add dream cycle
12. Add scheduler
13. Add the dedicated final reply call to cognition
14. Add consult (cloud adapter, fuses, consult log)
15. Write tests for each module before moving to the next
16. Threshold Atlas is already tested — keep its test suite running

## Testing Principles

Each module gets tests before the next module is built. Tests run independently — no Discord, no live model required for unit tests.

Priority test coverage:

- Evidence gate rejects recalled memory as evidence
- Evidence gate rejects saves with no verbatim quote
- Dedup reinforces at ≥ 0.92, holds at 0.85–0.91 with rate fuse
- Path resolution uses package location not cwd
- Final reply call receives no mid-loop prose
- Scheduler rejects tasks that reference named persons
- Named-person gate rejects consult payloads referencing a person
- Consult daily budget and retry fuses enforce at the tool layer
- Ambient prompts contain no human lane content
- All data paths resolve correctly from arbitrary working directories

*End of design document. Update as build progresses.*
