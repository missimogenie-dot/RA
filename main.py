from __future__ import annotations

import asyncio

import sitecustomize  # noqa: F401
from runtime import main


if __name__ == "__main__":
    asyncio.run(main())
