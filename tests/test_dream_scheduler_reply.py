"""Tests: scheduler gates, dream salience, final-reply prompt purity."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("YIN_DATA_DIR", str(tmp_path / "yin_data"))
    yield tmp_path / "yin_data"


# ── scheduler gates (code, not prompt) ────────────────────────────────


def test_scheduler_rejects_named_persons():
    from scheduler import Scheduler

    s = Scheduler()
    ok, msg = s.add_task("Think about what Immie might want to know tomorrow", 120)
    assert not ok
    assert "cannot reference a person" in msg
    # the design doc's valid example passes
    ok, msg = s.add_task(
        "Research something from the knowledge graph that has no outgoing edges and add context",
        1440,
    )
    assert ok, msg


def test_scheduler_blocked_names_configurable(monkeypatch):
    from scheduler import Scheduler

    monkeypatch.setenv("SCHEDULER_BLOCKED_NAMES", "immie,marcus")
    s = Scheduler()
    ok, msg = s.add_task("Write a short letter to Marcus about the weather", 120)
    assert not ok


def test_scheduler_rejects_thin_or_fast_tasks():
    from scheduler import Scheduler

    s = Scheduler()
    ok, msg = s.add_task("do stuff", 120)
    assert not ok and "self-contained" in msg
    ok, msg = s.add_task("Read one library chapter and shelve a trace of it", 5)
    assert not ok and "Minimum is 60" in msg


def test_scheduler_due_cancel_flow():
    from scheduler import Scheduler

    s = Scheduler()
    ok, msg = s.add_task("Tend one orphaned area of the habitat and note the change", 60)
    task_id = int(msg.split("Task ")[1].split(" ")[0])
    assert s.due_tasks() == []  # not due yet
    with s._connect() as conn:  # force it due
        conn.execute("UPDATE scheduled_tasks SET next_run = '2020-01-01T00:00:00'")
    due = s.due_tasks()
    assert due and due[0]["id"] == task_id
    s.mark_ran(task_id, "done")
    assert s.due_tasks() == []  # re-armed into the future
    ok, _ = s.cancel_task(task_id)
    assert ok
    assert "No scheduled tasks" in s.list_tasks() or str(task_id) not in s.list_tasks()


def test_scheduler_phase_cannot_save_lessons():
    """The evidence gate closes the save path for scheduled runs."""
    from yin.memory.evidence import check_evidence

    passes, reason = check_evidence("some quote", "some quote", phase="scheduler")
    assert not passes
    assert "live conversation" in reason


# ── dream cycle salience ──────────────────────────────────────────────


def _entry(text, hours_ago, refs):
    now = datetime.now(timezone.utc)
    return {
        "id": text[:6], "text": text, "refs": refs,
        "created_at": (now - timedelta(hours=hours_ago)).isoformat(),
    }


def test_salience_prefers_referenced_and_recent():
    from dream_cycle import salience, split_by_salience

    now = datetime.now(timezone.utc)
    fresh_referenced = _entry("came up three times today", 2, 3)
    stale_untouched = _entry("noted once, days ago", 47, 0)
    assert salience(fresh_referenced, now) > salience(stale_untouched, now)

    entries = [
        fresh_referenced,
        _entry("mentioned twice", 5, 2),
        _entry("recent but unreferenced", 1, 0),
        _entry("old and unreferenced", 40, 0),
        stale_untouched,
        _entry("older still", 46, 0),
    ]
    kept, dropped = split_by_salience(entries, now)
    kept_texts = [e["text"] for e in kept]
    assert "came up three times today" in kept_texts
    assert len(kept) >= 3
    assert len(kept) + len(dropped) == len(entries)


def test_split_keeps_minimum_even_when_small():
    from dream_cycle import split_by_salience

    now = datetime.now(timezone.utc)
    entries = [_entry(f"note {i}", i, 0) for i in range(4)]
    kept, dropped = split_by_salience(entries, now)
    assert len(kept) == 3 and len(dropped) == 1
    assert split_by_salience([], now) == ([], [])


# ── final reply prompt purity ─────────────────────────────────────────


def test_final_reply_prompt_carries_tools_not_prose():
    from cognition import build_final_reply_prompt

    tool_log = [
        {"tool": "recall_memory", "args": {"query": "garden"},
         "result": "- Immie grows tomatoes"},
        {"tool": "library_list", "args": {}, "result": "18 books"},
    ]
    prompt = build_final_reply_prompt("What's in my garden?", tool_log)
    assert "What's in my garden?" in prompt
    assert "recall_memory" in prompt and "tomatoes" in prompt
    assert "library_list" in prompt
    # nothing but message + tool outcomes: no field for loop prose exists
    assert "write the message you will actually send" in prompt


def test_final_reply_prompt_without_tools():
    from cognition import build_final_reply_prompt

    prompt = build_final_reply_prompt("hello", [])
    assert "(no tools were used)" in prompt
