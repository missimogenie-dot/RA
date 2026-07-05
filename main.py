from __future__ import annotations

import asyncio

from dotenv import load_dotenv

# config.py reads env vars at import time — .env must load before any
# module that imports config, or every setting comes up empty.
load_dotenv()

from runtime import main  # noqa: E402


if __name__ == "__main__":
    asyncio.run(main())
