"""
Path anchoring for all Yin memory data.

Every data path derives from this package's location (Path(__file__)) or
the one explicit config root YIN_DATA_DIR. Nothing resolves against the
process cwd — a guiding principle paid for in v1.
"""

from __future__ import annotations

import os
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent


def data_root() -> Path:
    override = os.getenv("YIN_DATA_DIR", "").strip()
    root = Path(override).expanduser().resolve() if override else PACKAGE_ROOT / "data"
    root.mkdir(parents=True, exist_ok=True)
    return root


def lane_path(*parts: str) -> Path:
    path = data_root().joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
