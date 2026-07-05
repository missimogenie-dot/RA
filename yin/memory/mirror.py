"""
Semantic mirror of a JSON lane.

JSON is the source of truth; the mirror only answers "what is similar
to this text?". ChromaMirror persists through ChromaDB. LocalMirror is
a pure-Python cosine index used in tests and as the automatic fallback
when chromadb is unavailable — the bot keeps working either way.

Both self-heal: sync() rebuilds the index whenever the JSON entries and
the indexed ids disagree.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

Embedder = Callable[[List[str]], List[List[float]]]

# (entry_id, similarity 0..1, metadata)
Match = Tuple[str, float, Dict[str, Any]]


class LocalMirror:
    def __init__(self, embedder: Embedder) -> None:
        self.embedder = embedder
        self._vectors: Dict[str, List[float]] = {}
        self._meta: Dict[str, Dict[str, Any]] = {}

    def ids(self) -> List[str]:
        return list(self._vectors.keys())

    def add(self, entry_id: str, text: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        self._vectors[entry_id] = self.embedder([text])[0]
        self._meta[entry_id] = dict(metadata or {})

    def remove(self, entry_id: str) -> None:
        self._vectors.pop(entry_id, None)
        self._meta.pop(entry_id, None)

    def query(
        self,
        text: str,
        k: int = 5,
        where: Optional[Dict[str, Any]] = None,
    ) -> List[Match]:
        if not self._vectors:
            return []
        qv = self.embedder([text])[0]
        scored: List[Match] = []
        for entry_id, vec in self._vectors.items():
            meta = self._meta.get(entry_id, {})
            if where and any(meta.get(key) != val for key, val in where.items()):
                continue
            scored.append((entry_id, _cosine(qv, vec), meta))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:k]

    def sync(self, entries: List[Dict[str, Any]], metadata_keys: Tuple[str, ...] = ()) -> None:
        wanted = {entry["id"] for entry in entries}
        for stale in set(self._vectors) - wanted:
            self.remove(stale)
        for entry in entries:
            if entry["id"] not in self._vectors:
                meta = {key: entry[key] for key in metadata_keys if key in entry}
                self.add(entry["id"], entry["text"], meta)


class ChromaMirror:
    def __init__(
        self,
        collection: str,
        embedder: Embedder,
        persist_dir: Optional[Path] = None,
    ) -> None:
        import chromadb  # deferred so the fallback works without it

        from .paths import data_root

        path = persist_dir or (data_root() / "chroma")
        path.mkdir(parents=True, exist_ok=True)
        self.embedder = embedder
        self._client = chromadb.PersistentClient(path=str(path))
        self._collection = self._client.get_or_create_collection(
            name=collection, metadata={"hnsw:space": "cosine"}
        )

    def ids(self) -> List[str]:
        return list(self._collection.get(include=[])["ids"])

    def add(self, entry_id: str, text: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        self._collection.upsert(
            ids=[entry_id],
            embeddings=self.embedder([text]),
            documents=[text],
            metadatas=[metadata] if metadata else None,
        )

    def remove(self, entry_id: str) -> None:
        self._collection.delete(ids=[entry_id])

    def query(
        self,
        text: str,
        k: int = 5,
        where: Optional[Dict[str, Any]] = None,
    ) -> List[Match]:
        if self._collection.count() == 0:
            return []
        result = self._collection.query(
            query_embeddings=self.embedder([text]),
            n_results=min(k, self._collection.count()),
            where=where or None,
        )
        ids = result["ids"][0]
        distances = result["distances"][0]
        metadatas = (result.get("metadatas") or [[{}] * len(ids)])[0]
        return [
            (entry_id, 1.0 - distance, meta or {})
            for entry_id, distance, meta in zip(ids, distances, metadatas)
        ]

    def sync(self, entries: List[Dict[str, Any]], metadata_keys: Tuple[str, ...] = ()) -> None:
        indexed = set(self.ids())
        wanted = {entry["id"] for entry in entries}
        for stale in indexed - wanted:
            self.remove(stale)
        for entry in entries:
            if entry["id"] not in indexed:
                meta = {key: entry[key] for key in metadata_keys if key in entry}
                self.add(entry["id"], entry["text"], meta or None)


def make_mirror(collection: str, embedder: Optional[Embedder] = None):
    """Chroma if available, LocalMirror otherwise. Never a dead end."""
    from .embedding import OllamaEmbedder

    active = embedder or OllamaEmbedder()
    try:
        return ChromaMirror(collection, active)
    except Exception:
        return LocalMirror(active)


def _cosine(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0
