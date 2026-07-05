"""Tests for steps 9/10/14: consolidator filters, mentor gating, consult fuses."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("YIN_DATA_DIR", str(tmp_path / "yin_data"))
    yield tmp_path / "yin_data"


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


class FakeAdapter:
    """Canned responses in Ollama's dict shape."""

    provider = "fake"

    def __init__(self, text="", tool_calls=None, fail=False):
        self.text = text
        self.tool_calls = tool_calls or []
        self.fail = fail

    async def complete(self, **kwargs):
        if self.fail:
            raise RuntimeError("model unavailable")
        return {"message": {"content": self.text, "tool_calls": self.tool_calls}}

    def extract_text(self, response):
        return response["message"].get("content", "")

    def extract_thinking(self, response):
        return ""

    def extract_tool_calls(self, response):
        from model_adapters import ToolCall

        return [
            ToolCall(id=f"{c['function']['name']}#{i}", name=c["function"]["name"],
                     input=c["function"]["arguments"])
            for i, c in enumerate(response["message"].get("tool_calls", []))
        ]


# ── kg consolidator filters ───────────────────────────────────────────


def test_parse_triples_tolerates_fences_and_noise():
    from kg_consolidator import parse_triples

    fenced = 'Here you go:\n```json\n[{"subject": "a", "predicate": "b", "object": "c"}]\n```'
    assert parse_triples(fenced) == [{"subject": "a", "predicate": "b", "object": "c"}]
    assert parse_triples("no json here") == []
    assert parse_triples("") == []


def test_worldly_filter_blocks_self_and_persons():
    from kg_consolidator import _is_worldly

    assert _is_worldly({"subject": "The Tao Te Ching", "predicate": "written_by", "object": "Laozi"}, "Yin")
    assert not _is_worldly({"subject": "Yin", "predicate": "feels", "object": "curious"}, "Yin")
    assert not _is_worldly({"subject": "I", "predicate": "learned", "object": "patience"}, "Yin")
    # person facts go to the human lane (SCHEDULER_BLOCKED_NAMES default: immie)
    assert not _is_worldly({"subject": "Immie", "predicate": "grows", "object": "tomatoes"}, "Yin")
    assert not _is_worldly({"subject": "", "predicate": "x", "object": "y"}, "Yin")


def test_consolidator_stores_worldly_triples_only():
    from kg_consolidator import KGConsolidator
    from yin.logs import YinLogs
    from yin.memory.world import WorldKnowledge
    from tests.test_yin_memory import fresh_mirror

    class FakeGraph:
        def __init__(self):
            self.facts = []

        async def add_fact(self, s, p, o):
            self.facts.append((s, p, o))
            return True, "ok"

    adapter = FakeAdapter(text=(
        '[{"subject": "Flatland", "predicate": "describes", "object": "two-dimensional space"},'
        ' {"subject": "Immie", "predicate": "likes", "object": "gardens"},'
        ' {"subject": "I", "predicate": "am", "object": "reflective"}]'
    ))
    graph = FakeGraph()
    world = WorldKnowledge(mirror=fresh_mirror())
    consolidator = KGConsolidator(adapter, "fake", graph, world, YinLogs(), "Yin")
    result = run(consolidator.run("Human: tell me about Flatland\nYin: ..."))
    assert result == "1 facts consolidated"
    assert graph.facts == [("Flatland", "describes", "two-dimensional space")]
    assert "Flatland" in world.recent(3)


# ── mentor: advisory, evidence-gated ──────────────────────────────────


def _mentor(adapter):
    from mentor import Mentor
    from yin.logs import YinLogs
    from yin.memory.recall import YinMemory
    from tests.test_yin_memory import fresh_mirror

    yin = YinMemory(mirrors={
        "lessons": fresh_mirror(), "goals": fresh_mirror(),
        "preferences": fresh_mirror(), "human_memory": fresh_mirror(),
        "world_knowledge": fresh_mirror(),
    })
    return Mentor(adapter, "fake", yin, YinLogs(), "Yin"), yin


def test_mentor_saves_lesson_with_verbatim_evidence():
    adapter = FakeAdapter(tool_calls=[{
        "function": {"name": "add_lesson", "arguments": {
            "lesson": "short answers land better in quick exchanges",
            "evidence": "just give me the short version",
        }},
    }])
    mentor, yin = _mentor(adapter)
    run(mentor.reflect("please just give me the short version", "Noted — short it is."))
    assert len(yin.lessons.entries()) == 1


def test_mentor_rejects_paraphrased_evidence():
    adapter = FakeAdapter(tool_calls=[{
        "function": {"name": "add_lesson", "arguments": {
            "lesson": "brevity is preferred",
            "evidence": "she asked for brevity",  # paraphrase, not a quote
        }},
    }])
    mentor, yin = _mentor(adapter)
    run(mentor.reflect("please just give me the short version", "Noted."))
    assert yin.lessons.entries() == []


def test_mentor_never_raises_on_model_failure():
    mentor, yin = _mentor(FakeAdapter(fail=True))
    run(mentor.reflect("hello", "hi"))  # must not raise
    assert yin.lessons.entries() == []


# ── consult fuses ─────────────────────────────────────────────────────


def test_consult_gates_thin_questions_and_persons(monkeypatch):
    from consult import Consult

    monkeypatch.setenv("CONSULT_MODEL", "claude-sonnet-5")
    c = Consult()
    assert "self-contained" in run(c.ask("why?"))
    out = run(c.ask("What would be the best gift idea for Immie this year?"))
    assert "cannot reference a person" in out


def test_consult_soft_redirect_without_model(monkeypatch):
    from consult import Consult

    monkeypatch.delenv("CONSULT_MODEL", raising=False)
    c = Consult()
    out = run(c.ask("How should a small agent structure long-term memory stores?"))
    assert "library" in out and "web_search" in out  # names what works


def test_consult_budget_and_log(monkeypatch):
    from consult import Consult

    monkeypatch.setenv("CONSULT_MODEL", "claude-sonnet-5")
    monkeypatch.setenv("CONSULT_DAILY_BUDGET", "1")
    c = Consult()
    c._adapter = FakeAdapter(text="Consider separate lanes per origin.")
    out = run(c.ask("How should a small agent structure long-term memory stores?"))
    assert "Consider separate lanes" in out
    # budget of one is now spent
    out2 = run(c.ask("What is a good cadence for consolidation of working memory?"))
    assert "budget is spent" in out2
    log_text = c.log_read()
    assert "separate lanes" in log_text
    assert "How should a small agent" in log_text


def test_consult_log_empty_is_not_dead_end():
    from consult import Consult

    assert "No consults yet" in Consult().log_read()
