"""
Embeddings via local Ollama (nomic-embed-text).

The embedder is a plain callable: List[str] -> List[List[float]].
Stores accept any callable with that shape, so tests inject fakes and
the semantic layer never needs a network in CI.
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import List

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")


class OllamaEmbedder:
    def __init__(self, base_url: str = "", model: str = "") -> None:
        self.base_url = (base_url or OLLAMA_BASE_URL).rstrip("/")
        self.model = model or EMBED_MODEL

    def __call__(self, texts: List[str]) -> List[List[float]]:
        payload = json.dumps({"model": self.model, "input": texts}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/api/embed",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        embeddings = body.get("embeddings") or []
        if len(embeddings) != len(texts):
            raise RuntimeError(
                f"embedding count mismatch: sent {len(texts)}, got {len(embeddings)}"
            )
        return embeddings
