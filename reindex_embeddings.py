"""
reindex_embeddings.py - one-off script to reembed existing bot memory rows.

Run from the bot directory where your .env file lives:
    python reindex_embeddings.py

Covers:
    - <BOT_POSTGRES_SCHEMA>.memory_interpretations
    - <BOT_POSTGRES_SCHEMA>.creations
"""

import asyncio
import logging
import os

from dotenv import load_dotenv

load_dotenv()

POSTGRES_DSN = os.getenv("POSTGRES_DSN", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
SCHEMA = os.getenv("BOT_POSTGRES_SCHEMA", os.getenv("ISUUI_POSTGRES_SCHEMA", "public"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)


async def embed(client, text: str):
    resp = await client.embeddings.create(
        model="text-embedding-3-small",
        input=text[:8000],
    )
    return resp.data[0].embedding


async def reindex_table(conn, client, table: str, schema: str):
    rows = await conn.fetch(
        f"SELECT id, content FROM {schema}.{table} WHERE embedding IS NULL"
    )
    log.info(f"{table}: {len(rows)} rows to reindex")
    for i, row in enumerate(rows, 1):
        try:
            embedding = await embed(client, row["content"])
            await conn.execute(
                f"UPDATE {schema}.{table} SET embedding = $1 WHERE id = $2",
                embedding,
                row["id"],
            )
            log.info(f"  [{i}/{len(rows)}] {table} {row['id']} ✓")
        except Exception as exc:
            log.error(f"  [{i}/{len(rows)}] {table} {row['id']} FAILED: {exc}")


async def main():
    if not POSTGRES_DSN:
        log.error("POSTGRES_DSN not set in .env")
        return
    if not OPENAI_API_KEY:
        log.error("OPENAI_API_KEY not set in .env")
        return

    try:
        import asyncpg
        from openai import AsyncOpenAI
        from pgvector.asyncpg import register_vector
    except ImportError as e:
        log.error(f"Missing dependency: {e}")
        return

    client = AsyncOpenAI(api_key=OPENAI_API_KEY)

    async def _init(conn):
        await register_vector(conn)

    pool = await asyncpg.create_pool(
        POSTGRES_DSN,
        min_size=1,
        max_size=3,
        server_settings={"search_path": f"{SCHEMA},public"},
        init=_init,
    )

    async with pool.acquire() as conn:
        await reindex_table(conn, client, "memory_interpretations", SCHEMA)
        await reindex_table(conn, client, "creations", SCHEMA)

    await pool.close()
    log.info("Reindexing complete!")


if __name__ == "__main__":
    asyncio.run(main())
