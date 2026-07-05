from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import sitecustomize  # noqa: F401
from dotenv import load_dotenv

from bot import BotClient
from canvas import CanvasManager
from codebase_rw import CodebaseRW
from cognition import CognitionEngine
from config import (
    AMBIENT_MODEL_BASE_URL,
    AMBIENT_MODEL_NAME,
    AMBIENT_MODEL_PROVIDER,
    BOT_POSTGRES_SCHEMA,
    CANVAS_DIR,
    DAY_NIGHT_ENABLED,
    DAY_END_HOUR,
    DAY_START_HOUR,
    HEARTBEAT_INTERVAL,
    INSTANCE_NAME,
    LIBRARY_DIR,
    MODEL_BASE_URL,
    MODEL_NAME,
    MODEL_PROVIDER,
    OPENAI_API_KEY,
    POSTGRES_DATABASE,
    POSTGRES_DSN,
    RUNTIME_STATE_DIR,
    SKY_STATE_PATH,
)
from day_night import DayNightCycle
from heartbeat import Heartbeat
from bot_postgres import BotPostgres
from library import Library
from memory import BotMemory
from model_adapters import create_model_adapter, provider_api_key
from sky import SkyMap

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

    postgres = BotPostgres(
        dsn=POSTGRES_DSN,
        openai_api_key=OPENAI_API_KEY,
        schema=BOT_POSTGRES_SCHEMA,
        expected_database=POSTGRES_DATABASE,
    )
    if POSTGRES_DSN:
        connected = await postgres.connect()
        if not connected:
            log.warning("Postgres unavailable — continuing with JSONL fallback.")
    else:
        log.info("POSTGRES_DSN not set — running in JSONL-only mode.")

    CANVAS_DIR.mkdir(parents=True, exist_ok=True)
    sky = SkyMap(state_file=SKY_STATE_PATH)
    canvas = CanvasManager(canvas_dir=str(CANVAS_DIR))
    day_night = DayNightCycle(day_start=DAY_START_HOUR, day_end=DAY_END_HOUR) if DAY_NIGHT_ENABLED else None
    phase = day_night.phase if day_night else "continuous"
    log.info("Environment: phase=%s, sky=%d stars, weather=%s", phase, len(sky.stars), sky.weather)

    heartbeat = Heartbeat(interval=HEARTBEAT_INTERVAL)

    cognition = CognitionEngine(
        model_adapter=response_adapter,
        ambient_model_adapter=ambient_adapter,
        memory=memory,
        postgres=postgres,
        library=library,
        codebase=codebase,
        sky=sky,
        canvas=canvas,
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
        await postgres.close()
