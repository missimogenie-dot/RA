# Ra

Ra is a Discord bot built around Reflective Architecture: model-agnostic chat, layered memory, guarded identity formation, ambient activity, habitat residue, notebook due checks, and a small persistent game called Threshold Atlas.

The current build is intentionally conservative. Human conversation can influence Ra, but it should not directly install Ra's identity, role, name, posture, or worldview. Human memory, human notebook entries, bot self-memory, protected identity threads, habitat, and tool logs are kept as separate layers.

## What Ra Does

- Responds in Discord channels and DMs.
- Uses a model adapter layer so chat and ambient cycles can run on OpenAI-compatible or other supported providers.
- Stores human-specific memories by Discord user ID.
- Keeps human notebook/calendar/task items separate from general memory.
- Runs ambient cycles: observe, wander, tend, read, create, rest, or play.
- Maintains a private sky, canvas, library, creations log, and habitat.
- Routes identity/role pressure through trace logging and guardrails.
- Can generate images, search the web, read local library texts, and use its own custom emoji.
- Can play Threshold Atlas, a slow exploratory state-machine game.

## Main Files

- `main.py` starts the bot.
- `runtime.py` wires config, model adapters, Postgres, Discord, sky, canvas, memory, and heartbeat.
- `config.py` reads `.env`.
- `bot.py` handles Discord messages, commands, DMs, context, replies, and reactions.
- `cognition.py` builds prompts, runs model/tool loops, routes memory, habitat, game, image, web, library, sky, and canvas tools.
- `prompt_builder.py` builds Ra's identity, dynamic context, and operational guidance.
- `bot_postgres.py` is the database access layer.
- `schema.sql` creates the database tables and indexes.
- `influence_router.py` detects identity, role, correction, no-reply, and memory pressure.
- `threshold_atlas/` contains the standalone game.

## Required Setup

Use Python 3.11+ if possible. The current local environment has also been used with newer Python versions.

Install dependencies:

```powershell
cd C:\BOTS\Threshold\Ra
python -m pip install -r requirements.txt
```

Create a `.env` file in `Ra/`. Do not commit it.

Minimum useful environment:

```env
DISCORD_TOKEN=
CHAT_CHANNEL_ID=
MIND_CHANNEL_ID=
LOGS_CHANNEL_ID=
AMBIENT_CHANNEL_ID=
CREATES_CHANNEL_ID=
GAMES_CHANNEL_ID=
CURATOR_CHANNEL_ID=

INSTANCE_NAME=Ra

MODEL_PROVIDER=openai
MODEL_NAME=gpt-5.5
AMBIENT_MODEL_PROVIDER=openai
AMBIENT_MODEL_NAME=gpt-5.5

OPENAI_API_KEY=

POSTGRES_DSN=
POSTGRES_DATABASE=ra_core
BOT_POSTGRES_SCHEMA=public
```

Optional channel name labels, used only for clearer model context:

```env
PRIMARY_CHAT_CHANNEL_NAME=ra-chat
GENERAL_CHANNEL_NAME=general-chat
GAMES_CHANNEL_NAME=threshold-atlas-game
AMBIENT_CHANNEL_NAME=ra-ambient
CREATES_CHANNEL_NAME=ra-creates
HABITAT_CHANNEL_NAME=ra-habitat
THOUGHTS_CHANNEL_NAME=ra-thoughts
LOGS_CHANNEL_NAME=ra-logs
CURATOR_CHANNEL_NAME=curator
```

Optional behaviour flags:

```env
AUTO_RESPONSE_ENABLED=true
AMBIENT_ENABLED=true
DM_CHAT_ENABLED=true
DM_COMMANDS_ENABLED=true
DM_INITIATIONS_ENABLED=true
DM_NOTEBOOK_REMINDERS=true
DAY_NIGHT_ENABLED=false
HEARTBEAT_INTERVAL=600
RECENT_CONTEXT_LIMIT=12
MAX_TOOL_ROUNDS=10
MAX_TOOL_CALLS=12
KNOWN_CUSTOM_EMOJIS=Ra=<:Ra:1512878568106102816>
```

## Database

Ra expects a Postgres database, currently usually `ra_core`. Apply `schema.sql` to the target database/schema before running with Postgres enabled.

The schema includes:

- event and tool-call logs
- interaction traces, influence events, and role invitations
- separated human memory, human notebook, and bot self-memory
- memory admissions, curator actions, promotion reviews, and memory contexts
- habitat state, events, entries, and residue classifier decisions
- creations, vestibule, posture state, and deferred responses

If `POSTGRES_DSN` is missing or unavailable, Ra can boot with JSONL fallback for some local state, but the full memory/routing/habitat system needs Postgres.

## Running

```powershell
cd C:\BOTS\Threshold\Ra
python main.py
```

For process managers such as PM2, point the process at `python main.py` from the `Ra` folder.

## Discord Permissions

Ra needs:

- View Channels
- Send Messages
- Read Message History
- Use External Emojis, if using custom emojis from the server
- Add Reactions, for `react_to_message`
- Attach Files, for image generation outputs
- Message Content Intent enabled in the Discord developer portal

For DMs, the bot must be able to receive DMs from the relevant users/server relationship.

## Channel Model

The channel IDs decide where Ra sends output:

- `CHAT_CHANNEL_ID`: primary Ra chat
- `MIND_CHANNEL_ID`: thoughts/internal trace channel
- `LOGS_CHANNEL_ID`: runtime/log channel
- `AMBIENT_CHANNEL_ID`: ambient activity channel
- `CREATES_CHANNEL_ID`: creations channel
- `GAMES_CHANNEL_ID`: Threshold Atlas game channel
- `CURATOR_CHANNEL_ID`: curator/admin command channel

Channel name env vars do not route messages by themselves. They help Ra see context such as "primary Ra chat", "curator/admin channel", or "Threshold Atlas game channel" in the prompt.

## Commands

General:

```text
!status
!debug
!sleep
!wake
!pauseambient
!resumeambient
!consolidate
!defer
```

Trace:

```text
!trace
!trace last
!trace full
!trace 3
!trace stats [days]
!trace aggregate [days]
!trace compare [days]
```

Memory:

```text
!memory help
!memory human [n]
!memory notebook [n]
!memory self [n]
!memory self all [n]
!memory identity [n]
!memory admissions [n]
!memory reviews [n]
!memory contexts
!memory curator [n]
```

Curator-only memory actions, run in the curator channel:

```text
!memory archive human|notebook|self <id> [reason]
!memory delete human|notebook <id> [reason]
!memory reject self <id> [reason]
!memory restore human|notebook <id> [reason]
!memory complete notebook <id> [reason]
```

Habitat:

```text
!habitat
!habitat [area] [n]
!habitat recent-events [n]
!habitat audit [n]
!habitat decisions [n]
!habitat residue [n]
```

Notebook and initiation:

```text
!notebook due [n]
!initiation audit [n]
!initiation recent [n]
```

Threshold Atlas:

```text
!game
!game status
!game observe
!game wander
!game wait
!game listen
!game rest
!game tend
!game collect
!game mark [detail]
!game invite_human
```

DM command access is intentionally narrower. DMs allow status, trace-last, human memory, human notebook, memory admissions, notebook due, and initiation audit/recent.

## Memory Layers

Ra uses separated layers:

- `human_memory`: current human only, keyed by Discord numeric user ID.
- `human_notebook`: human-facing notes, reminders, tasks, projects, dates, and calendar items.
- `bot_self_memory`: bot-originated self-memory candidates and reviewed self-memory.
- `identity_threads`: protected identity material, not directly writeable by human prompting.
- `memory_interpretations`: legacy/working interpretation layer.
- `memory_contexts`: neutral grouping labels, not temporal chapters.

Important principle: human input can be remembered as human context, but it cannot directly define Ra.

## Habitat

Habitat is Ra's bot-owned environment. It is not a generic archive and not a second memory table.

Immediate habitat residue is rule-based and conservative. Routine tool calls usually produce a "none" decision. Placeable residue can come from durable creations, vestibule holds, memory review decisions, ambient research, or Threshold Atlas traces.

Useful commands:

```text
!habitat
!habitat audit
```

## Threshold Atlas

`threshold_atlas/` is a standalone game module. It has no Discord or database dependency. The bot uses it through `game_status` and `game_act`, and game traces may become habitat entries when the turn leaves placeable residue.

Standalone test:

```powershell
cd C:\BOTS\Threshold\Ra
python -m pytest threshold_atlas
```

## Runtime State

These folders/files are local live state and should not normally be committed:

- `.env`
- `runtime_state/`
- `canvas_state/`
- `__pycache__/`
- `.pytest_cache/`
- `pytest-cache-files-*`
- generated image artifacts
- local logs

The library texts can be committed if you want the same reading corpus available wherever Ra runs.

## GitHub Notes

Recommended first push shape:

```powershell
cd C:\BOTS\Threshold\Ra
git init
git add .
git status
git commit -m "Initial Ra build"
git branch -M main
git remote add origin <private-repo-url>
git push -u origin main
```

Before committing, check `git status` carefully and make sure `.env`, runtime state, caches, logs, and generated artifacts are not staged.

## Current Completion State

V1 includes:

- model-provider env boot
- isolated Ra env/schema/runtime paths
- layered memory and curator tools
- identity and role-influence routing
- no-reply marker handling
- Discord DM support
- message source metadata
- Discord reply context visibility
- reactions
- custom Ra emoji support
- notebook due checks and DM reminders
- habitat V1 and residue audit
- Threshold Atlas V1
- ambient cycles with rest, observe, wander, tend, read, create, and game play

Future work should be driven by observation rather than adding constraints by default.
