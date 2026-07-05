"""
threshold_atlas/game.py

Tiny state machine:  state + action -> result + updated_state

No Discord, no database, no bot-specific imports.
Run standalone:  python game.py
"""

from __future__ import annotations

import copy
import json
import random
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Location definitions
# ---------------------------------------------------------------------------

LOCATIONS: dict[str, dict] = {
    "threshold": {
        "description": (
            "A wide stone landing between indoors and out. "
            "Light comes from somewhere above — cool, directionless. "
            "Several paths begin here, though not all are always visible."
        ),
        "exits": ["observatory", "garden", "library", "studio"],
        "local_state": {},
        "habitat_type": "threshold/marker",
    },
    "observatory": {
        "description": (
            "A curved room, mostly glass. The ceiling opens when the weather allows. "
            "Star charts are pinned to a rotating drum. "
            "The air smells faintly of oil and cold metal."
        ),
        "exits": ["threshold"],
        "local_state": {"dome_open": False},
        "habitat_type": "observatory/weather",
    },
    "garden": {
        "description": (
            "Overgrown but tended in its own logic. "
            "Paths reappear after rain. "
            "Something is usually growing that wasn't there before."
        ),
        "exits": ["threshold", "library"],
        "local_state": {"wet": False, "seed_visible": False},
        "habitat_type": "garden/seed",
    },
    "library": {
        "description": (
            "Tall shelves, poor light, and the smell of old paper. "
            "Books open themselves occasionally. "
            "The catalogue has gaps — whole sections listed but not yet found."
        ),
        "exits": ["threshold", "garden", "studio"],
        "local_state": {"open_book": None},
        "habitat_type": "library/shelf_item",
    },
    "studio": {
        "description": (
            "A working space. Surfaces marked with old ink. "
            "Half-finished things lean against the walls. "
            "Nothing here demands to be completed."
        ),
        "exits": ["threshold", "library"],
        "local_state": {"fragment_present": False},
        "habitat_type": "studio/fragment",
    },
}

WEATHERS = ["clear", "overcast", "mist", "rain", "still", "wind", "snow"]

WEATHER_TRANSITIONS: dict[str, list[str]] = {
    "clear":    ["clear", "clear", "overcast", "wind"],
    "overcast": ["overcast", "mist", "rain", "clear"],
    "mist":     ["mist", "overcast", "still", "rain"],
    "rain":     ["rain", "overcast", "still", "mist"],
    "still":    ["still", "clear", "mist", "overcast"],
    "wind":     ["wind", "clear", "overcast", "still"],
    "snow":     ["snow", "still", "overcast", "clear"],
}

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def new_state() -> dict:
    """Return a fresh game state."""
    return {
        "game_key": "threshold_atlas",
        "turn_count": 0,
        "current_location": "threshold",
        "discovered_locations": ["threshold"],
        "inventory": [],
        "open_paths": [],
        "traces": [],
        "weather": "clear",
        "last_action": None,
        "local_states": {loc: copy.deepcopy(data["local_state"]) for loc, data in LOCATIONS.items()},
    }


def load_state(path: str | Path = "state.json") -> dict:
    p = Path(path)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return new_state()


def save_state(state: dict, path: str | Path = "state.json") -> None:
    Path(path).write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# Available actions
# ---------------------------------------------------------------------------

def available_actions(state: dict) -> list[str]:
    """Return actions possible at the current location and state."""
    loc = state["current_location"]
    base = ["observe", "wait", "listen", "rest", "wander"]

    if loc == "garden":
        base += ["tend"]
        if state["local_states"]["garden"].get("seed_visible"):
            base += ["collect", "mark"]
    if loc == "observatory":
        base += ["mark"]
    if loc == "studio":
        base += ["mark", "tend"]
    if loc == "library":
        base += ["collect"]

    base += ["invite_human"]

    exits = LOCATIONS[loc]["exits"]
    undiscovered = [e for e in exits if e not in state["discovered_locations"]]
    if undiscovered:
        base += ["wander"]  # wander already in list; no duplicate needed here

    return sorted(set(base))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _shift_weather(current: str) -> str:
    options = WEATHER_TRANSITIONS.get(current, WEATHERS)
    return random.choice(options)


def _maybe_shift_weather(state: dict) -> tuple[dict, str | None]:
    """Occasionally shift weather. Returns (state, weather_note|None)."""
    if random.random() < 0.15:
        new_w = _shift_weather(state["weather"])
        if new_w != state["weather"]:
            old = state["weather"]
            state["weather"] = new_w
            return state, f"The weather shifts from {old} to {new_w}."
    return state, None


def _build_trace(area: str, entry_type: str, title: str, content: str,
                 suggested: list[str], weight: float = 0.5,
                 confidence: float = 0.8, reason: str = "") -> dict:
    return {
        "area": area,
        "entry_type": entry_type,
        "title": title,
        "content": content,
        "suggested_actions": suggested,
        "weight": weight,
        "confidence": confidence,
        "reason": reason,
    }


def _marks_for_location(loc: str, state: dict) -> list[str]:
    """Return the text content of any marks left at this location."""
    hab_prefix = LOCATIONS[loc]["habitat_type"]  # e.g. "threshold/marker"
    results = []
    for path in state.get("open_paths", []):
        if path.startswith(hab_prefix + ":"):
            content = path[len(hab_prefix) + 1:]
            results.append(content)
    return results


def _maybe_invite(loc: str, state: dict) -> dict | None:
    """Rarely produce a human invite, only at branching moments."""
    exits = LOCATIONS[loc]["exits"]
    undiscovered = [e for e in exits if e not in state["discovered_locations"]]
    if len(undiscovered) >= 2 and random.random() < 0.25:
        return {
            "prompt": f"There are paths I haven't taken: {' and '.join(undiscovered)}. Which should I try?",
            "choices": undiscovered,
        }
    return None


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------

def _act_observe(state: dict, detail: str) -> dict:
    loc = state["current_location"]
    loc_data = LOCATIONS[loc]
    local = state["local_states"][loc]

    texts = [loc_data["description"]]
    trace = None
    invite = None

    # Surface any marks left here on previous visits
    prior_marks = _marks_for_location(loc, state)
    if prior_marks:
        for mark in prior_marks:
            texts.append(f"There is a mark here from before: {mark}.")

    if loc == "garden":
        if state["weather"] in ("rain", "mist"):
            local["wet"] = True
            texts.append("The ground is wet. A path has reappeared near the far wall.")
        if local.get("wet") and not local.get("seed_visible") and random.random() < 0.4:
            local["seed_visible"] = True
            texts.append("A pale seed is visible under a wet stone.")
            trace = _build_trace(
                "garden", "seed",
                "Pale seed under wet stone",
                "Found while observing the garden after rain.",
                ["tend", "wait", "mark"],
                weight=0.5, confidence=0.8,
                reason="Game turn created a placeable unresolved object."
            )

    elif loc == "observatory":
        if state["weather"] == "clear":
            local["dome_open"] = True
            texts.append("The dome is open. Stars are faintly visible even in the pale light.")
            trace = _build_trace(
                "observatory", "weather",
                "Clear dome, stars visible",
                "Observed during a clear turn.",
                ["mark", "wait"],
                weight=0.3, confidence=0.9,
                reason="Sky condition worth recording."
            )
        else:
            local["dome_open"] = False
            texts.append(f"The dome is closed. Weather outside: {state['weather']}.")

    elif loc == "library":
        books = ["Flatland", "Meditations", "Tao Te Ching", "Frankenstein", "Leaves of Grass"]
        if local.get("open_book") is None and random.random() < 0.35:
            book = random.choice(books)
            local["open_book"] = book
            texts.append(f"A book has opened itself: {book}.")
            trace = _build_trace(
                "library", "shelf_item",
                f"Open book: {book}",
                f"{book} opened while observing the library.",
                ["collect", "wait", "mark"],
                weight=0.4, confidence=0.7,
                reason="Unresolved open book — potential thread."
            )
        elif local.get("open_book"):
            texts.append(f"{local['open_book']} is still open.")

    elif loc == "studio":
        if not local.get("fragment_present") and random.random() < 0.3:
            local["fragment_present"] = True
            texts.append("A fragment of something is half-visible under the work table.")
            trace = _build_trace(
                "studio", "fragment",
                "Fragment under the work table",
                "Noticed while observing the studio.",
                ["mark", "tend", "rest"],
                weight=0.4, confidence=0.6,
                reason="Unresolved creative fragment."
            )

    invite = _maybe_invite(loc, state)
    state["local_states"][loc] = local
    return {"text": " ".join(texts), "trace": trace, "invite": invite, "state": state}


def _act_wander(state: dict, detail: str) -> dict:
    loc = state["current_location"]
    exits = LOCATIONS[loc]["exits"]

    # Prefer undiscovered, otherwise random
    undiscovered = [e for e in exits if e not in state["discovered_locations"]]
    target = random.choice(undiscovered) if undiscovered else random.choice(exits)

    state["current_location"] = target
    if target not in state["discovered_locations"]:
        state["discovered_locations"].append(target)

    new_loc_data = LOCATIONS[target]
    text = f"You wander from {loc} toward {target}. {new_loc_data['description']}"

    trace = None
    path_title = f"Path to {target} discovered"
    if not any(t.get("entry_type") == "path" and t.get("title") == path_title for t in state["traces"]):
        # First visit to this area — record as path discovery
        trace = _build_trace(
            "atlas", "path",
            path_title,
            f"Arrived at {target} by wandering from {loc}.",
            ["observe", "wait"],
            weight=0.3, confidence=0.9,
            reason="New location reached — atlas path entry."
        )

    invite = _maybe_invite(target, state)
    return {"text": text, "trace": trace, "invite": invite, "state": state}


def _act_wait(state: dict, detail: str) -> dict:
    loc = state["current_location"]
    state, weather_note = _maybe_shift_weather(state)
    parts = [f"Time passes in {loc}."]
    if weather_note:
        parts.append(weather_note)
    else:
        parts.append(f"The weather remains {state['weather']}.")
    return {"text": " ".join(parts), "trace": None, "invite": None, "state": state}


def _act_listen(state: dict, detail: str) -> dict:
    loc = state["current_location"]
    sounds = {
        "threshold": [
            "A low hum from somewhere in the walls.",
            "Distant footsteps that stop before they arrive.",
            "Wind through a gap you can't locate.",
        ],
        "observatory": [
            "The faint click of the dome mechanism.",
            "Something is tracking across the sky — too slow for a bird.",
            "A signal, partial, half-interpreted.",
        ],
        "garden": [
            "Water running underground.",
            "Seeds shifting under soil.",
            "A bird, but not one you can name.",
        ],
        "library": [
            "Pages turning with no visible hand.",
            "A low creak — the shelves settling.",
            "Someone read here recently. The echo of attention.",
        ],
        "studio": [
            "A tool rolling off a surface and not landing.",
            "The sound of drying ink.",
            "Silence that feels occupied.",
        ],
    }
    heard = random.choice(sounds.get(loc, ["A quiet hum."]))
    return {"text": heard, "trace": None, "invite": None, "state": state}


def _act_rest(state: dict, detail: str) -> dict:
    loc = state["current_location"]
    state, weather_note = _maybe_shift_weather(state)
    text = f"You rest in {loc}. Nothing is required."
    if weather_note:
        text += f" {weather_note}"
    return {"text": text, "trace": None, "invite": None, "state": state}


def _act_tend(state: dict, detail: str) -> dict:
    loc = state["current_location"]
    local = state["local_states"][loc]
    trace = None

    if loc == "garden":
        if local.get("seed_visible"):
            local["seed_visible"] = False
            local["tended"] = local.get("tended", 0) + 1
            text = "You tend to the area around the stone. The seed is covered, but not buried — held."
            trace = _build_trace(
                "garden", "seed",
                "Tended seed under wet stone",
                "The seed was tended and held in place.",
                ["wait", "observe"],
                weight=0.6, confidence=0.8,
                reason="Object tended — state changed, unresolved."
            )
        else:
            text = "You tend the garden generally. Things are as they are, slightly more held."
    elif loc == "studio":
        if local.get("fragment_present"):
            local["fragment_present"] = False
            local["fragments_held"] = local.get("fragments_held", 0) + 1
            text = "You tend the fragment — not resolving it, but placing it somewhere it won't be lost."
            trace = _build_trace(
                "studio", "fragment",
                "Fragment placed deliberately",
                "A studio fragment held rather than resolved.",
                ["mark", "rest"],
                weight=0.5, confidence=0.7,
                reason="Fragment tended — track as held open."
            )
        else:
            text = "You tend the studio. Old marks are not erased. New order arrives slowly."
    else:
        text = f"You tend to {loc}. The work is quiet and goes unrecorded."

    state["local_states"][loc] = local
    return {"text": text, "trace": trace, "invite": None, "state": state}


def _act_collect(state: dict, detail: str) -> dict:
    loc = state["current_location"]
    local = state["local_states"][loc]
    trace = None

    if loc == "garden" and local.get("seed_visible"):
        local["seed_visible"] = False
        item = "pale seed"
        if item not in state["inventory"]:
            state["inventory"].append(item)
        text = "You pick up the pale seed. It is lighter than expected."
    elif loc == "library" and local.get("open_book"):
        book = local["open_book"]
        local["open_book"] = None
        item = f"note from {book}"
        state["inventory"].append(item)
        text = f"You take a note from {book}. The book closes after."
        trace = _build_trace(
            "library", "shelf_item",
            f"Note collected from {book}",
            f"A note was taken from {book} in the library.",
            ["mark", "wander"],
            weight=0.4, confidence=0.8,
            reason="Collected object — potential thread connection."
        )
    else:
        text = "There is nothing specific to collect here right now."

    state["local_states"][loc] = local
    return {"text": text, "trace": trace, "invite": None, "state": state}


def _act_mark(state: dict, detail: str) -> dict:
    loc = state["current_location"]
    local = state["local_states"][loc]
    hab = LOCATIONS[loc]["habitat_type"]

    if detail:
        mark_text = detail
    else:
        defaults = {
            "threshold": "a small mark at the threshold — where one space becomes another",
            "observatory": "a line corresponding to something seen",
            "garden": "a notch on a nearby stone",
            "library": "a marginal note, unattributed",
            "studio": "a mark that doesn't resolve into anything yet",
        }
        mark_text = defaults.get(loc, "a mark")

    path = f"{hab}:{mark_text[:40]}"
    if path not in state["open_paths"]:
        state["open_paths"].append(path)

    text = f"You mark: {mark_text}."
    trace = _build_trace(
        hab.split("/")[0], hab.split("/")[1],
        f"Mark in {loc}",
        mark_text,
        ["observe", "tend", "wait"],
        weight=0.3, confidence=0.6,
        reason="Deliberate mark placed — worth holding open."
    )
    state["local_states"][loc] = local
    return {"text": text, "trace": trace, "invite": None, "state": state}


def _act_invite_human(state: dict, detail: str) -> dict:
    loc = state["current_location"]
    exits = LOCATIONS[loc]["exits"]
    undiscovered = [e for e in exits if e not in state["discovered_locations"]]

    if undiscovered:
        invite = {
            "prompt": f"I haven't been to {' or '.join(undiscovered)} yet. Which should I try?",
            "choices": undiscovered,
        }
        text = "You pause and consider whether a human might want to weigh in."
    else:
        items = state["inventory"]
        if items:
            invite = {
                "prompt": f"I'm carrying: {', '.join(items)}. What should I do with any of these?",
                "choices": ["keep", "leave here", "mark"],
            }
        else:
            invite = {
                "prompt": "Nothing is pressing. Is there somewhere you'd like me to go?",
                "choices": [e for e in exits],
            }
        text = "You open a question to the outside."

    return {"text": text, "trace": None, "invite": invite, "state": state}


# ---------------------------------------------------------------------------
# Action dispatch
# ---------------------------------------------------------------------------

_HANDLERS = {
    "observe":       _act_observe,
    "wander":        _act_wander,
    "wait":          _act_wait,
    "listen":        _act_listen,
    "rest":          _act_rest,
    "tend":          _act_tend,
    "collect":       _act_collect,
    "mark":          _act_mark,
    "invite_human":  _act_invite_human,
}


def act(state: dict, action: str, detail: str = "") -> dict:
    """
    Apply one action to state.

    Returns:
        {
            "text": str,
            "state": updated_state,
            "trace": dict | None,
            "invite": dict | None,
        }
    """
    state = copy.deepcopy(state)
    action = action.strip().lower()

    handler = _HANDLERS.get(action)
    if handler is None:
        state["turn_count"] += 1
        state["last_action"] = action
        return {
            "text": f"'{action}' is not something that can be done here.",
            "state": state,
            "trace": None,
            "invite": None,
        }

    result = handler(state, detail)

    # Shared bookkeeping
    result["state"]["turn_count"] += 1
    result["state"]["last_action"] = action

    # Append trace to state trace list if present
    if result.get("trace"):
        result["state"]["traces"].append(result["trace"])

    return result


def apply_human_choice(state: dict, choice: str) -> dict:
    """
    Apply an optional human co-decision without making the human the game engine.

    If the choice names a visible exit, move there as a bounded wander-like turn.
    If it names an available action, perform that action.
    Otherwise, leave the choice as a mark in the current place.
    """
    state = copy.deepcopy(state)
    loc = state["current_location"]
    normalized = " ".join((choice or "").strip().lower().split())
    exits = LOCATIONS[loc]["exits"]
    exit_map = {name.lower(): name for name in exits}

    if normalized in exit_map:
        target = exit_map[normalized]
        state["current_location"] = target
        if target not in state["discovered_locations"]:
            state["discovered_locations"].append(target)
        path_title = f"Path to {target} chosen"
        trace = _build_trace(
            "atlas", "path",
            path_title,
            f"A human co-decision pointed toward {target} from {loc}.",
            ["observe", "wait"],
            weight=0.35, confidence=0.8,
            reason="Human choice selected a visible path; recorded as optional co-decision."
        )
        state["turn_count"] += 1
        state["last_action"] = "human_choice"
        state["traces"].append(trace)
        return {
            "text": f"The human choice tilts toward {target}. You take that path from {loc}.",
            "state": state,
            "trace": trace,
            "invite": _maybe_invite(target, state),
        }

    if normalized in available_actions(state):
        return act(state, normalized)

    return act(state, "mark", f"human choice: {choice[:60]}")


# ---------------------------------------------------------------------------
# Summarise
# ---------------------------------------------------------------------------

def summarise(state: dict) -> str:
    loc = state["current_location"]
    disc = ", ".join(state["discovered_locations"])
    inv = ", ".join(state["inventory"]) if state["inventory"] else "nothing"
    paths = len(state["open_paths"])
    traces = len(state["traces"])
    return (
        f"Turn {state['turn_count']} | Location: {loc} | Weather: {state['weather']}\n"
        f"Discovered: {disc}\n"
        f"Carrying: {inv}\n"
        f"Open paths: {paths} | Traces recorded: {traces}"
    )


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------

def _auto_turn(state: dict) -> tuple[dict, dict]:
    """Choose an action and apply it. Returns (result, new_state)."""
    actions = available_actions(state)
    # Weight: prefer observe/wander slightly
    weighted = []
    for a in actions:
        if a in ("observe", "wander", "listen"):
            weighted += [a, a]
        else:
            weighted.append(a)
    chosen = random.choice(weighted)
    result = act(state, chosen)
    return result, result["state"]


if __name__ == "__main__":
    import sys

    state_path = Path(__file__).parent / "state.json"
    state = load_state(state_path)

    # Auto-run 5 turns
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5

    for i in range(n):
        result, state = _auto_turn(state)
        print(f"\n--- Turn {state['turn_count']} [{state['current_location']}] ---")
        print(result["text"])
        if result.get("trace"):
            t = result["trace"]
            print(f"  [trace] {t['area']}/{t['entry_type']}: {t['title']}")
        if result.get("invite"):
            inv = result["invite"]
            print(f"  [invite] {inv['prompt']}")
            print(f"           choices: {inv['choices']}")

    print(f"\n{summarise(state)}")
    save_state(state, state_path)
    print(f"\nState saved to {state_path}")
