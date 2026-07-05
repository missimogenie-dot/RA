from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from bot import BotClient
from codebase_rw import CodebaseRW
from cognition import CognitionEngine
from config import (
    AMBIENT_MODEL_BASE_URL,
    AMBIENT_MODEL_NAME,
    AMBIENT_MODEL_PROVIDER,
    DAY_NIGHT_ENABLED,
    DAY_END_HOUR,
    DAY_START_HOUR,
    HEARTBEAT_INTERVAL,
    INSTANCE_NAME,
    LIBRARY_DIR,
    MODEL_BASE_URL,
    MODEL_NAME,
    MODEL_PROVIDER,
    RUNTIME_STATE_DIR,
)
from day_night import DayNightCycle
from heartbeat import Heartbeat
from library import Library
from memory import BotMemory
from model_adapters import create_model_adapter, provider_api_key
from yin.bridge import YinStore

log = logging.getLogger("ra.runtime")


async def main() -> None:
    load_dotenv()
    Path(RUNTIME_STATE_DIR).mkdir(parents=True, exist_ok=True)
    log_file = Path(RUNTIME_STATE_DIR) / f"{INSTANCE_NAME.lower()}_runtime.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )

    env = dict(os.environ)
    response_adapter = create_model_adapter(
        MODEL_PROVIDER,
        api_key=provider_api_key(MODEL_PROVIDER, env),
        base_url=MODEL_BASE_URL,
    )
    ambient_adapter = create_model_adapter(
        AMBIENT_MODEL_PROVIDER,
        api_key=provider_api_key(AMBIENT_MODEL_PROVIDER, env),
        base_url=AMBIENT_MODEL_BASE_URL,
    )
    memory = BotMemory(state_dir=RUNTIME_STATE_DIR)
    library = Library(library_dir=LIBRARY_DIR)
    codebase = CodebaseRW()

    store = YinStore(memory=memory)
    log.info("YinStore ready — SQLite logs + JSON lanes, all local.")

    day_night = DayNightCycle(day_start=DAY_START_HOUR, day_end=DAY_END_HOUR) if DAY_NIGHT_ENABLED else None
    phase = day_night.phase if day_night else "continuous"
    log.info("Environment: phase=%s", phase)

    heartbeat = Heartbeat(interval=HEARTBEAT_INTERVAL)

    cognition = CognitionEngine(
        model_adapter=response_adapter,
        ambient_model_adapter=ambient_adapter,
        memory=memory,
        store=store,
        library=library,
        codebase=codebase,
        day_night=day_night,
        model=MODEL_NAME,
        ambient_model=AMBIENT_MODEL_NAME,
        instance_name=INSTANCE_NAME,
    )

    bot = BotClient(
        cognition=cognition,
        memory=memory,
        heartbeat=heartbeat,
    )

    try:
        await bot.run_bot()
    finally:
        memory.save()
