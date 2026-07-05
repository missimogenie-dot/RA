"""
Retrieval boundaries — DESIGN.md's table, enforced in code.

Each context reads only its own lanes. There is no parameter that
widens a context's reach; a lane absent from the map is unreachable
from that context, full stop.

  Context     Can read
  chat        human (this user), notebook (this user), lessons, goals,
              preferences, working
  reflection  live conversation + the chat lanes, vestibule
  ambient     lessons, goals, preferences, autobiography, vestibule
  dream       working, autobiography
  scheduler   goals, lessons

(world_knowledge joins ambient/dream when the Neo4j store lands.)
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .autobiography import Autobiography
from .goals import GoalManager
from .human import HumanMemory
from .lessons import LessonManager
from .notebook import Notebook
from .preferences import PreferenceManager
from .timeline import Timeline
from .vestibule import Vestibule
from .working import WorkingMemory
from .world import WorldKnowledge

CONTEXT_LANES: Dict[str, List[str]] = {
    "chat": ["human", "notebook", "lessons", "goals", "preferences", "working"],
    "reflection": ["human", "notebook", "lessons", "goals", "preferences", "working", "vestibule"],
    "ambient": ["lessons", "goals", "preferences", "autobiography", "vestibule", "world"],
    "dream": ["working", "autobiography", "world"],
    "scheduler": ["goals", "lessons"],
}


class YinMemory:
    """One handle owning every lane. Cognition talks to this, not to lanes."""

    def __init__(self, embedder=None, mirrors: Optional[Dict[str, object]] = None) -> None:
        mirrors = mirrors or {}
        self.lessons = LessonManager(mirror=mirrors.get("lessons"))
        self.goals = GoalManager(mirror=mirrors.get("goals"))
        self.preferences = PreferenceManager(mirror=mirrors.get("preferences"))
        self.human = HumanMemory(mirror=mirrors.get("human_memory"))
        self.autobiography = Autobiography()
        self.timeline = Timeline()
        self.working = WorkingMemory()
        self.notebook = Notebook()
        self.vestibule = Vestibule()
        self.world = WorldKnowledge(mirror=mirrors.get("world_knowledge"))

    def recall(self, context: str, query: str, user_id: str = "", k: int = 4) -> str:
        lanes = CONTEXT_LANES.get(context)
        if lanes is None:
            known = ", ".join(sorted(CONTEXT_LANES))
            return f"Unknown recall context '{context}'. Known contexts: {known}."

        sections: List[str] = []
        if "human" in lanes and user_id:
            sections.append(f"[HUMAN]\n{self.human.recall(user_id, query, k=k)}")
        if "notebook" in lanes and user_id:
            sections.append(f"[NOTEBOOK]\n{self.notebook.read(user_id, n=k)}")
        if "lessons" in lanes:
            sections.append(f"[LESSONS]\n{self.lessons.search(query, k=k)}")
        if "goals" in lanes:
            sections.append(f"[GOALS]\n{self.goals.search(query, k=k)}")
        if "preferences" in lanes:
            sections.append(f"[PREFERENCES]\n{self.preferences.search(query, k=k)}")
        if "autobiography" in lanes:
            sections.append(f"[AUTOBIOGRAPHY]\n{self.autobiography.read_recent(3)}")
        if "working" in lanes:
            sections.append(f"[WORKING]\n{self.working.read(10)}")
        if "vestibule" in lanes:
            sections.append(f"[VESTIBULE]\n{self.vestibule.check()}")
        if "world" in lanes:
            sections.append(f"[WORLD]\n{self.world.search(query, k=k)}")
        return "\n\n".join(sections)
