"""
Tests for Yin's memory lanes — DESIGN.md "Testing Principles".

Runs with no Discord, no live model, no ChromaDB: a fake embedder
drives LocalMirror so similarity values are exact and controllable.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    """Every test gets a throwaway YIN_DATA_DIR — never the real lanes."""
    monkeypatch.setenv("YIN_DATA_DIR", str(tmp_path / "yin_data"))
    yield tmp_path / "yin_data"


class VocabEmbedder:
    """Deterministic embeddings: same text -> same vector; unrelated
    texts are near-orthogonal. Specific pairs can be pinned to an exact
    cosine similarity for threshold tests."""

    def __init__(self):
        self.pinned = {}

    def pin(self, text_a: str, text_b: str, similarity: float):
        # Two 2-d unit vectors with the requested cosine similarity.
        import math

        angle = math.acos(similarity)
        self.pinned[text_a] = [1.0, 0.0]
        self.pinned[text_b] = [math.cos(angle), math.sin(angle)]

    def __call__(self, texts):
        import hashlib

        out = []
        for text in texts:
            if text in self.pinned:
                out.append(self.pinned[text] + [0.0] * 6)
                continue
            digest = hashlib.sha256(text.encode()).digest()
            vec = [b / 255.0 for b in digest[:8]]
            out.append(vec)
        return out


def fresh_mirror(embedder=None):
    from yin.memory.mirror import LocalMirror

    return LocalMirror(embedder or VocabEmbedder())


# ── evidence gate ─────────────────────────────────────────────────────


def test_evidence_gate_rejects_missing_quote():
    from yin.memory.lessons import LessonManager

    lessons = LessonManager(mirror=fresh_mirror())
    ok, msg = lessons.add_lesson(
        "Immie prefers short replies",
        evidence="",
        live_conversation="please keep replies short",
    )
    assert not ok
    assert "verbatim" in msg


def test_evidence_gate_rejects_paraphrase():
    from yin.memory.lessons import LessonManager

    lessons = LessonManager(mirror=fresh_mirror())
    ok, msg = lessons.add_lesson(
        "Immie prefers short replies",
        evidence="she wants brevity always",
        live_conversation="please keep replies short",
    )
    assert not ok
    assert "not found" in msg


def test_evidence_gate_accepts_verbatim_quote_case_and_spacing_insensitive():
    from yin.memory.lessons import LessonManager

    lessons = LessonManager(mirror=fresh_mirror())
    ok, msg = lessons.add_lesson(
        "Immie prefers short replies",
        evidence="Keep Replies   Short",
        live_conversation="please keep replies short, thanks",
    )
    assert ok, msg
    assert lessons.entries()


def test_evidence_gate_rejects_recalled_memory_as_evidence():
    from yin.memory.lessons import LessonManager

    lessons = LessonManager(mirror=fresh_mirror())
    ok, msg = lessons.add_lesson(
        "Immie likes gardens",
        evidence="I like gardens a lot",
        live_conversation="today we talked and I like gardens a lot came up",
        recalled_texts=["[2026-06-01] I like gardens a lot"],
    )
    assert not ok
    assert "recalled memory" in msg


def test_evidence_gate_rejects_ambient_and_dream_saves():
    from yin.memory.preferences import PreferenceManager

    prefs = PreferenceManager(mirror=fresh_mirror())
    for phase in ("ambient", "dream", "scheduler"):
        ok, msg = prefs.add_preference(
            "quiet mornings are best",
            evidence="quiet mornings",
            live_conversation="quiet mornings",
            phase=phase,
        )
        assert not ok, phase
        assert "live conversation" in msg


# ── dedup and rate fuse ───────────────────────────────────────────────


def test_dedup_reinforces_at_092():
    from yin.memory.goals import GoalManager

    embedder = VocabEmbedder()
    embedder.pin("read the whole library", "read every library book", 0.95)
    goals = GoalManager(mirror=fresh_mirror(embedder))

    goals.add_goal("read the whole library")
    ok, msg = goals.add_goal("read every library book")
    assert ok
    assert "Reinforced" in msg
    entries = goals.entries()
    assert len(entries) == 1
    assert entries[0]["weight"] == 2


def test_fuse_window_decisions_hold_after_daily_limit():
    from yin.memory.dedup import DAILY_CLUSTER_LIMIT, RateFuse, decide

    fuse = RateFuse("test_lane")
    # Inside the 0.85–0.91 window: first N near-duplicates on one theme
    # save as new, then the fuse trips and further saves hold quietly.
    outcomes = [decide(0.88, "theme1", fuse).action for _ in range(DAILY_CLUSTER_LIMIT + 2)]
    assert outcomes[:DAILY_CLUSTER_LIMIT] == ["new"] * DAILY_CLUSTER_LIMIT
    assert outcomes[DAILY_CLUSTER_LIMIT:] == ["hold", "hold"]
    # A different theme is unaffected by theme1's tripped fuse
    assert decide(0.88, "theme2", fuse).action == "new"
    # Outside the window the fuse is never consulted
    assert decide(0.95, "theme1", fuse).action == "reinforce"
    assert decide(0.50, "theme1", fuse).action == "new"


def test_fuse_hold_is_quiet_in_store_response(monkeypatch):
    from yin.memory import dedup
    from yin.memory.goals import GoalManager

    embedder = VocabEmbedder()
    embedder.pin("tend the knowledge graph", "tend the graph of knowledge", 0.88)
    goals = GoalManager(mirror=fresh_mirror(embedder))
    goals.add_goal("tend the knowledge graph")
    monkeypatch.setattr(dedup, "DAILY_CLUSTER_LIMIT", 0)
    ok, msg = goals.add_goal("tend the graph of knowledge")
    # Held: reads as success, mentions no fuse, saves nothing.
    assert ok
    assert msg == "Noted."
    assert len(goals.entries()) == 1


def test_dedup_saves_new_below_085():
    from yin.memory.goals import GoalManager

    embedder = VocabEmbedder()
    embedder.pin("learn the stars", "bake good bread", 0.30)
    goals = GoalManager(mirror=fresh_mirror(embedder))
    goals.add_goal("learn the stars")
    ok, msg = goals.add_goal("bake good bread")
    assert ok
    assert "Saved" in msg
    assert len(goals.entries()) == 2


# ── retrieval boundaries ──────────────────────────────────────────────


def test_human_lane_is_scoped_per_user():
    from yin.memory.human import HumanMemory

    human = HumanMemory(mirror=fresh_mirror())
    human.store("111", "loves foxes")
    human.store("222", "hates rain")
    recall_111 = human.recall("111", "foxes")
    assert "loves foxes" in recall_111
    assert "hates rain" not in recall_111
    # other user's lane is unreachable from user 111's recall
    assert "hates rain" not in human.recall("111", "rain")


def test_context_lane_map_blocks_private_lanes():
    from yin.memory.recall import CONTEXT_LANES

    assert "autobiography" not in CONTEXT_LANES["chat"]
    assert "human" not in CONTEXT_LANES["ambient"]
    assert "human" not in CONTEXT_LANES["dream"]
    assert CONTEXT_LANES["scheduler"] == ["goals", "lessons"]


def test_yin_memory_recall_chat_excludes_autobiography():
    from yin.memory.recall import YinMemory

    memory = YinMemory(mirrors={
        "lessons": fresh_mirror(), "goals": fresh_mirror(),
        "preferences": fresh_mirror(), "human_memory": fresh_mirror(),
    })
    memory.autobiography.append("a private day, quietly held")
    output = memory.recall("chat", "day", user_id="111")
    assert "quietly held" not in output
    assert "[AUTOBIOGRAPHY]" not in output


# ── append-only autobiography ─────────────────────────────────────────


def test_autobiography_has_no_edit_or_delete_api():
    from yin.memory.autobiography import Autobiography

    autobiography = Autobiography()
    autobiography.append("first entry")
    assert not hasattr(autobiography, "delete")
    assert not hasattr(autobiography, "edit")
    assert autobiography.count() == 1


# ── paths ─────────────────────────────────────────────────────────────


def test_paths_ignore_cwd(tmp_path, monkeypatch, data_dir):
    from yin.memory.paths import data_root

    monkeypatch.chdir(tmp_path)
    first = data_root()
    monkeypatch.chdir(tmp_path.parent)
    second = data_root()
    assert first == second
    assert str(first).startswith(str(data_dir))


# ── never an empty dead end ───────────────────────────────────────────


def test_empty_lanes_state_what_exists():
    from yin.memory.lessons import LessonManager
    from yin.memory.timeline import Timeline
    from yin.memory.working import WorkingMemory

    assert "accumulate" in LessonManager(mirror=fresh_mirror()).search("anything")
    assert "empty" in Timeline().read_recent()
    assert "fresh session" in WorkingMemory().read()


def test_unknown_goal_update_names_what_exists():
    from yin.memory.goals import GoalManager

    goals = GoalManager(mirror=fresh_mirror())
    goals.add_goal("finish the atlas")
    ok, msg = goals.update_goal("nope123", "done")
    assert not ok
    assert "Existing goal ids" in msg
    ok, msg = goals.update_goal(goals.entries()[0]["id"], "flying")
    assert not ok
    assert "What works" in msg


# ── self-healing mirror ───────────────────────────────────────────────


def test_mirror_rebuilds_after_hand_pruning():
    from yin.memory.goals import GoalManager

    embedder = VocabEmbedder()
    goals = GoalManager(mirror=fresh_mirror(embedder))
    goals.add_goal("first goal here")
    goals.add_goal("second goal there")
    # Immie hand-prunes the JSON: delete the first entry
    entries = goals.entries()
    from yin.memory.jsonstore import save_entries

    save_entries(goals.path, entries[1:])
    # A fresh store over the same file resyncs the mirror to match
    reopened = GoalManager(mirror=fresh_mirror(embedder))
    assert len(reopened.entries()) == 1
    assert set(reopened.mirror.ids()) == {entries[1]["id"]}


# ── sqlite logs ───────────────────────────────────────────────────────


def test_sqlite_logs_roundtrip(data_dir):
    from yin.logs import YinLogs

    logs = YinLogs()
    logs.log_event("system", "boot complete", {"version": 2})
    logs.log_tool_call("recall_memory", "chat", {"query": "foxes"}, "2 matches", True)
    logs.log_tool_call("web_search", "ambient", {"query": "owls"}, "timeout", False)
    events = logs.recent_events()
    calls = logs.recent_tool_calls()
    assert "boot complete" in events
    assert "recall_memory" in calls
    assert "FAILED" in calls
    assert (Path(os.environ["YIN_DATA_DIR"]) / "logs.db").exists()


def test_sqlite_logs_empty_states_are_not_dead_ends(data_dir):
    from yin.logs import YinLogs

    logs = YinLogs()
    assert "accumulate" in logs.recent_events()
