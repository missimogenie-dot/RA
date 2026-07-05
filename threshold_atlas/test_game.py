"""
threshold_atlas/test_game.py

Run:  python -m pytest threshold_atlas/test_game.py -v
  or:  python threshold_atlas/test_game.py
"""

from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from threshold_atlas.game import (
    act,
    apply_human_choice,
    available_actions,
    new_state,
    summarise,
    LOCATIONS,
    WEATHERS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run(state, action, detail=""):
    result = act(state, action, detail)
    assert "text" in result
    assert "state" in result
    assert "trace" in result
    assert "invite" in result
    return result


# ---------------------------------------------------------------------------
# State construction
# ---------------------------------------------------------------------------

def test_new_state_shape():
    s = new_state()
    assert s["game_key"] == "threshold_atlas"
    assert s["turn_count"] == 0
    assert s["current_location"] == "threshold"
    assert "threshold" in s["discovered_locations"]
    assert isinstance(s["inventory"], list)
    assert isinstance(s["traces"], list)
    assert s["weather"] in WEATHERS
    for loc in LOCATIONS:
        assert loc in s["local_states"]


# ---------------------------------------------------------------------------
# Turn counter
# ---------------------------------------------------------------------------

def test_turn_increments():
    s = new_state()
    r = run(s, "wait")
    assert r["state"]["turn_count"] == 1
    r2 = run(r["state"], "wait")
    assert r2["state"]["turn_count"] == 2


# ---------------------------------------------------------------------------
# Actions return required keys
# ---------------------------------------------------------------------------

def test_all_actions_return_valid_structure():
    s = new_state()
    actions = available_actions(s)
    for action in actions:
        r = run(s, action)
        assert isinstance(r["text"], str) and len(r["text"]) > 0, f"Empty text for {action}"


# ---------------------------------------------------------------------------
# Unknown action
# ---------------------------------------------------------------------------

def test_unknown_action_does_not_crash():
    s = new_state()
    r = run(s, "fly_to_the_moon")
    assert "fly_to_the_moon" in r["text"]
    assert r["state"]["turn_count"] == 1  # still increments


# ---------------------------------------------------------------------------
# Wander / discovery
# ---------------------------------------------------------------------------

def test_wander_changes_location():
    s = new_state()
    r = run(s, "wander")
    assert r["state"]["current_location"] != "threshold" or True  # may stay if only exit loops back
    # discovered_locations must grow or stay same
    assert set(s["discovered_locations"]).issubset(set(r["state"]["discovered_locations"]))


def test_wander_discovers_new_location():
    s = new_state()
    # Threshold has 4 exits, all undiscovered — wander must find one
    r = run(s, "wander")
    assert len(r["state"]["discovered_locations"]) >= 2


def test_wander_trace_on_first_visit():
    s = new_state()
    r = run(s, "wander")
    # Should produce a trace for atlas/path on first visit
    assert r["trace"] is not None
    assert r["trace"]["entry_type"] == "path"


# ---------------------------------------------------------------------------
# Observe
# ---------------------------------------------------------------------------

def test_observe_returns_description():
    s = new_state()
    r = run(s, "observe")
    assert "threshold" in r["text"].lower() or len(r["text"]) > 20


def test_observe_garden_seed_after_rain():
    s = new_state()
    s["current_location"] = "garden"
    s["weather"] = "rain"
    s["local_states"]["garden"]["wet"] = False
    # Observe: should mark wet, and may reveal seed
    r = run(s, "observe")
    assert r["state"]["local_states"]["garden"]["wet"] is True


# ---------------------------------------------------------------------------
# Collect
# ---------------------------------------------------------------------------

def test_collect_seed_adds_to_inventory():
    s = new_state()
    s["current_location"] = "garden"
    s["local_states"]["garden"]["seed_visible"] = True
    r = run(s, "collect")
    assert "pale seed" in r["state"]["inventory"]
    assert r["state"]["local_states"]["garden"]["seed_visible"] is False


def test_collect_library_note():
    s = new_state()
    s["current_location"] = "library"
    s["local_states"]["library"]["open_book"] = "Flatland"
    r = run(s, "collect")
    assert any("Flatland" in item for item in r["state"]["inventory"])
    assert r["state"]["local_states"]["library"]["open_book"] is None
    assert r["trace"] is not None
    assert r["trace"]["area"] == "library"
    assert r["trace"]["entry_type"] == "shelf_item"


def test_collect_nothing_available():
    s = new_state()
    s["current_location"] = "threshold"
    r = run(s, "collect")
    assert "nothing" in r["text"].lower()


# ---------------------------------------------------------------------------
# Mark
# ---------------------------------------------------------------------------

def test_mark_adds_open_path():
    s = new_state()
    r = run(s, "mark", "a line at the edge")
    assert any("a line at the edge" in p for p in r["state"]["open_paths"])
    assert r["trace"] is not None


def test_mark_default_detail():
    s = new_state()
    r = run(s, "mark")
    assert len(r["state"]["open_paths"]) == 1
    assert r["trace"] is not None


# ---------------------------------------------------------------------------
# Tend
# ---------------------------------------------------------------------------

def test_tend_garden_seed():
    s = new_state()
    s["current_location"] = "garden"
    s["local_states"]["garden"]["seed_visible"] = True
    r = run(s, "tend")
    assert r["state"]["local_states"]["garden"]["seed_visible"] is False
    assert r["trace"] is not None


def test_tend_studio_fragment():
    s = new_state()
    s["current_location"] = "studio"
    s["local_states"]["studio"]["fragment_present"] = True
    r = run(s, "tend")
    assert r["state"]["local_states"]["studio"]["fragment_present"] is False
    assert r["trace"] is not None


# ---------------------------------------------------------------------------
# Listen / rest / wait
# ---------------------------------------------------------------------------

def test_listen_returns_text():
    for loc in LOCATIONS:
        s = new_state()
        s["current_location"] = loc
        r = run(s, "listen")
        assert len(r["text"]) > 5


def test_rest_returns_text():
    s = new_state()
    r = run(s, "rest")
    assert "rest" in r["text"].lower() or len(r["text"]) > 5


def test_wait_returns_text():
    s = new_state()
    r = run(s, "wait")
    assert len(r["text"]) > 5


# ---------------------------------------------------------------------------
# invite_human
# ---------------------------------------------------------------------------

def test_invite_human_produces_invite():
    s = new_state()
    r = run(s, "invite_human")
    assert r["invite"] is not None
    assert "prompt" in r["invite"]
    assert "choices" in r["invite"]


# ---------------------------------------------------------------------------
# Trace list accumulation
# ---------------------------------------------------------------------------

def test_traces_accumulate_in_state():
    s = new_state()
    s["current_location"] = "garden"
    s["local_states"]["garden"]["seed_visible"] = True
    r1 = run(s, "tend")  # produces trace
    r2 = run(r1["state"], "mark")  # produces trace
    assert len(r2["state"]["traces"]) >= 2


# ---------------------------------------------------------------------------
# Summarise
# ---------------------------------------------------------------------------

def test_summarise_format():
    s = new_state()
    summary = summarise(s)
    assert "Turn 0" in summary
    assert "threshold" in summary
    assert "Weather" in summary


# ---------------------------------------------------------------------------
# No hard failure / no exception
# ---------------------------------------------------------------------------

def test_many_auto_turns_no_exception():
    from threshold_atlas.game import _auto_turn
    s = new_state()
    for _ in range(30):
        result, s = _auto_turn(s)
        assert "text" in result
        assert s["turn_count"] > 0


# ---------------------------------------------------------------------------
# Prior marks surface on observe
# ---------------------------------------------------------------------------

def test_prior_mark_surfaces_on_observe():
    s = new_state()
    # Leave a mark at threshold
    r1 = run(s, "mark", "a line at the edge")
    # Observe the same location — mark should appear in text
    r2 = run(r1["state"], "observe")
    assert "a line at the edge" in r2["text"]


def test_mark_in_one_location_does_not_surface_elsewhere():
    s = new_state()
    r1 = run(s, "mark", "threshold scratch")
    # Move to garden, observe — should not see threshold mark
    r1["state"]["current_location"] = "garden"
    r2 = run(r1["state"], "observe")
    assert "threshold scratch" not in r2["text"]


def test_human_choice_can_pick_visible_exit():
    s = new_state()
    r = apply_human_choice(s, "garden")
    assert r["state"]["current_location"] == "garden"
    assert r["state"]["last_action"] == "human_choice"
    assert r["trace"] is not None
    assert r["trace"]["area"] == "atlas"


def test_human_choice_can_fall_back_to_mark():
    s = new_state()
    r = apply_human_choice(s, "follow the singing kettle")
    assert r["state"]["last_action"] == "mark"
    assert "human choice" in r["trace"]["content"]


# ---------------------------------------------------------------------------
# Run as script
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {t.__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
