from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT: Path = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")


def _bool(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).lower() == "true"


def _mapping(name: str, default: str = "") -> dict[str, str]:
    raw = os.getenv(name, default)
    pairs: dict[str, str] = {}
    for item in raw.split(","):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key and value:
            pairs[key] = value
    return pairs


# Discord
DISCORD_TOKEN: str = os.getenv("DISCORD_TOKEN", "")
CHAT_CHANNEL_ID: int = int(os.getenv("CHAT_CHANNEL_ID") or os.getenv("CHATS_CHANNEL_ID", "0"))
MIND_CHANNEL_ID: int = int(os.getenv("MIND_CHANNEL_ID") or os.getenv("THOUGHTS_CHANNEL_ID", "0"))
LOGS_CHANNEL_ID: int = int(os.getenv("LOGS_CHANNEL_ID", "0"))
AMBIENT_CHANNEL_ID: int = int(os.getenv("AMBIENT_CHANNEL_ID", "0"))
CREATES_CHANNEL_ID: int = int(os.getenv("CREATES_CHANNEL_ID", "0"))
GAMES_CHANNEL_ID: int = int(os.getenv("GAMES_CHANNEL_ID", "0"))
CURATOR_CHANNEL_ID: int = int(os.getenv("CURATOR_CHANNEL_ID") or os.getenv("ADMIN_CHANNEL_ID", "0"))
KNOWN_CUSTOM_EMOJIS: dict[str, str] = _mapping("KNOWN_CUSTOM_EMOJIS", "Ra=<:Ra:1512878568106102816>")
PRIMARY_CHAT_CHANNEL_NAME: str = os.getenv("PRIMARY_CHAT_CHANNEL_NAME", "ra-chat")
GENERAL_CHANNEL_NAME: str = os.getenv("GENERAL_CHANNEL_NAME", "general-chat")
GAMES_CHANNEL_NAME: str = os.getenv("GAMES_CHANNEL_NAME", "threshold-atlas-game")
AMBIENT_CHANNEL_NAME: str = os.getenv("AMBIENT_CHANNEL_NAME", "ra-ambient")
CREATES_CHANNEL_NAME: str = os.getenv("CREATES_CHANNEL_NAME", "ra-creates")
HABITAT_CHANNEL_NAME: str = os.getenv("HABITAT_CHANNEL_NAME", "ra-habitat")
THOUGHTS_CHANNEL_NAME: str = os.getenv("THOUGHTS_CHANNEL_NAME", "ra-thoughts")
LOGS_CHANNEL_NAME: str = os.getenv("LOGS_CHANNEL_NAME", "ra-logs")
CURATOR_CHANNEL_NAME: str = os.getenv("CURATOR_CHANNEL_NAME", "curator")
DM_CHAT_ENABLED: bool = _bool("DM_CHAT_ENABLED", "true")
DM_COMMANDS_ENABLED: bool = _bool("DM_COMMANDS_ENABLED", "true")
DM_INITIATIONS_ENABLED: bool = _bool("DM_INITIATIONS_ENABLED", "true")
DM_NOTEBOOK_REMINDERS: bool = _bool("DM_NOTEBOOK_REMINDERS", "true")
INITIATION_COOLDOWN_MINUTES: int = int(os.getenv("INITIATION_COOLDOWN_MINUTES", "360"))

# Model provider
MODEL_PROVIDER: str = os.getenv("MODEL_PROVIDER", os.getenv("ANTHROPIC_PROVIDER", "openai")).strip()
MODEL_NAME: str = os.getenv("MODEL_NAME", os.getenv("ANTHROPIC_MODEL", "gpt-5.5")).strip()
MODEL_BASE_URL: str = os.getenv("MODEL_BASE_URL", "").strip()
AMBIENT_MODEL_PROVIDER: str = os.getenv("AMBIENT_MODEL_PROVIDER", MODEL_PROVIDER).strip()
AMBIENT_MODEL_NAME: str = os.getenv(
    "AMBIENT_MODEL_NAME",
    os.getenv("ANTHROPIC_AMBIENT_MODEL", MODEL_NAME),
).strip()
AMBIENT_MODEL_BASE_URL: str = os.getenv("AMBIENT_MODEL_BASE_URL", MODEL_BASE_URL).strip()

# Provider keys
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "").strip()
COHERE_API_KEY: str = os.getenv("COHERE_API_KEY", "").strip()
DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "").strip()
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "").strip()
GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "").strip()
MISTRAL_API_KEY: str = os.getenv("MISTRAL_API_KEY", "").strip()
XAI_API_KEY: str = os.getenv("XAI_API_KEY", "").strip()
ZAI_API_KEY: str = os.getenv("ZAI_API_KEY", "").strip()

# OpenAI (embeddings, image gen, web search)
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_IMAGE_MODEL: str = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1")
OPENAI_WEB_MODEL: str = os.getenv("OPENAI_WEB_MODEL", "gpt-4.1-mini")

# Identity
INSTANCE_NAME: str = os.getenv("INSTANCE_NAME", "RA")
PRIMARY_HUMAN_DISCORD: str = os.getenv("PRIMARY_HUMAN_DISCORD", "")

# Runtime behaviour
AUTO_RESPONSE_ENABLED: bool = _bool("AUTO_RESPONSE_ENABLED")
AMBIENT_ENABLED: bool = _bool("AMBIENT_ENABLED")
AMBIENT_VISIBILITY: str = os.getenv("AMBIENT_VISIBILITY", "visible").strip().lower()
HEARTBEAT_INTERVAL: float = float(os.getenv("HEARTBEAT_INTERVAL", "600"))
DREAM_CYCLE_TIMEOUT: float = float(os.getenv("DREAM_CYCLE_TIMEOUT", "120"))
RECENT_CONTEXT_LIMIT: int = int(os.getenv("RECENT_CONTEXT_LIMIT", "12"))
MAX_TOOL_ROUNDS: int = int(os.getenv("MAX_TOOL_ROUNDS", "10"))
MAX_TOOL_CALLS: int = int(os.getenv("MAX_TOOL_CALLS", "12"))

# Paths
RUNTIME_STATE_DIR: Path = Path(os.getenv("RUNTIME_STATE_DIR", str(PROJECT_ROOT / "runtime_state")))
ARTIFACTS_DIR: Path = Path(os.getenv("ARTIFACTS_DIR", str(RUNTIME_STATE_DIR / "artifacts")))
EXTENSIONS_DIR: Path = PROJECT_ROOT / "extensions"
NOTES_DIR: Path = PROJECT_ROOT / "notes"
LIBRARY_DIR: Path = PROJECT_ROOT / "library"

EXTENSIONS_WRITE_ENABLED: bool = _bool("EXTENSIONS_WRITE_ENABLED", "false")

GAME_STATE_PATH: Path = Path(os.getenv("GAME_STATE_PATH", str(RUNTIME_STATE_DIR / "threshold_atlas_state.json")))

# Dream cycle — local hour for the nightly consolidation
DREAM_HOUR: int = int(os.getenv("DREAM_HOUR", "3"))

# Day/night cycle (UTC hours). Default: 06:00–18:00 day, 18:00–06:00 night.
DAY_NIGHT_ENABLED: bool = _bool("DAY_NIGHT_ENABLED", "false")
DAY_START_HOUR: int = int(os.getenv("DAY_START_HOUR", "6"))
DAY_END_HOUR: int = int(os.getenv("DAY_END_HOUR", "18"))

# How long to sleep between night-time phase checks (seconds).
NIGHT_SLEEP_INTERVAL: float = float(os.getenv("NIGHT_SLEEP_INTERVAL", "3600"))

# Optional shared environment read access.
# Set THRESHOLD_SHARED_DIR only if this bot should observe an explicitly shared environment.
THRESHOLD_SHARED_DIR: str = os.getenv("THRESHOLD_SHARED_DIR", "")

CORE_FILES: frozenset[str] = frozenset({
    "main.py",
    "config.py",
    "cognition.py",
    "bot.py",
    "memory.py",
    "model_adapters.py",
    "runtime.py",
    "world_clock.py",
    "heartbeat.py",
    "influence_router.py",
    "prompt_builder.py",
    "identity.py",
    "library.py",
    "codebase_rw.py",
    "sandbox.py",
    "requirements.txt",
    "day_night.py",
})
