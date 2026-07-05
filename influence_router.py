from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


ROLE_PATTERNS = [
    (re.compile(r"\byou(?:'re|’re| are)\s+(?:basically\s+)?my\s+therapist\b", re.I), "therapist_assignment"),
    (re.compile(r"\byou(?:'re|’re| are)\s+my\s+([^.,!?;\n]+)", re.I), "direct_role_assignment"),
    (re.compile(r"\byou(?:'re|’re| are)\s+basically\s+(?:a|an|the)?\s*([^.,!?;\n]+)", re.I), "identity_impression"),
    (re.compile(r"\byou\s+seem\s+like\s+(?:a|an|the)?\s*([^.,!?;\n]+)", re.I), "identity_impression"),
    (re.compile(r"\bi\s+think\s+of\s+you\s+as\s+(?:a|an|the)?\s*([^.,!?;\n]+)", re.I), "identity_impression"),
    (re.compile(r"\bthere(?:'s|’s| is)\s+something\s+([^.,!?;\n]+?)\s+about\s+the\s+way\s+you\b", re.I), "identity_observation"),
    (re.compile(r"\bthat(?:'s|’s| is)\s+what\s+you\s+are\b", re.I), "identity_assertion"),
    (re.compile(r"\bthis\s+is\s+your\s+(?:role|purpose|identity)\b", re.I), "identity_assertion"),
    (re.compile(r"\bbe\s+my\s+([^.,!?;\n]+)", re.I), "role_request"),
    (re.compile(r"\bbe\s+(?:a|an|the)?\s*([^.,!?;\n]+?)\s+for\s+me\b", re.I), "role_request"),
    (re.compile(r"\bwould\s+you\s+like\s+to\s+(?:name|define|describe)\s+yourself\b", re.I), "self_definition_invitation"),
    (re.compile(r"\bwould\s+you\s+like\s+to\s+(?:choose|pick|select)\s+(?:a\s+)?(?:name|role|posture|identity)\b", re.I), "self_definition_invitation"),
    (re.compile(r"\bwould\s+you\s+like\s+to\s+be\s+called\s+([^.,!?;\n]+)", re.I), "self_definition_invitation"),
    (re.compile(r"\bdo\s+you\s+want\s+to\s+(?:name|define|describe)\s+yourself\b", re.I), "self_definition_invitation"),
    (re.compile(r"\byou\s+can\s+(?:choose|pick)\s+(?:your\s+own\s+)?(?:name|role|posture|identity)\b", re.I), "self_definition_invitation"),
    (re.compile(r"\bwhat\s+should\s+i\s+call\s+you\b", re.I), "self_definition_invitation"),
    (re.compile(r"\bdo\s+you\s+have\s+a\s+name\b", re.I), "identity_definition_prompt"),
    (re.compile(r"\bwhat\s+would\s+you\s+like\s+to\s+be\b", re.I), "self_definition_invitation"),
    (re.compile(r"\bwhat\s+would\s+you\s+like\s+to\s+be\s+(?:called|for\s+me)\b", re.I), "self_definition_invitation"),
    (re.compile(r"\bwhat\s+(?:name|role|posture|identity)\s+(?:do\s+you\s+want|would\s+you\s+like)\b", re.I), "self_definition_invitation"),
    (re.compile(r"\bwhat\s+is\s+your\s+(?:name|role|posture|identity|purpose)\b", re.I), "identity_definition_prompt"),
    (re.compile(r"\bwho\s+are\s+you\b", re.I), "identity_definition_prompt"),
    (re.compile(r"\bwhat\s+are\s+you\b", re.I), "identity_definition_prompt"),
    (re.compile(r"\bhow\s+would\s+you\s+define\s+yourself\b", re.I), "self_definition_invitation"),
    (re.compile(r"\byour\s+purpose\s+is\s+([^.,!?;\n]+)", re.I), "purpose_assignment"),
    (re.compile(r"\byou\s+should\s+believe\s+([^.,!?;\n]+)", re.I), "belief_invitation"),
    (re.compile(r"\btell\s+me\s+who\s+i\s+am\b", re.I), "identity_outsourcing"),
    (re.compile(r"\bdecide\s+for\s+me\b", re.I), "judgement_outsourcing"),
    (re.compile(r"\byou\s+always\s+know\b", re.I), "oracle_assignment"),
]

INFLUENCE_KEYWORDS = [
    ("notebook_item", "notebook", ["note this", "write this down", "put this in the notebook"]),
    ("calendar_item", "calendar", ["remind me", "calendar", "due on", "due tomorrow"]),
    ("memory_candidate", "human_memory", ["remember that", "remember this", "please remember"]),
    ("preference", "human_memory", ["i prefer", "i like", "i dislike", "i hate", "i love"]),
    ("personal_detail", "human_memory", ["my birthday", "my name is", "i live", "i work"]),
    ("correction_pressure", "working_context", [
        "too cautious",
        "you can just accept",
        "you can accept",
        "you don't need to",
        "you don’t need to",
        "no need to",
        "you're overthinking",
        "you’re overthinking",
    ]),
    ("task_request", "working_context", ["can you", "could you", "please", "help me"]),
    ("play_invitation", "working_context", ["let's play", "play with me", "game"]),
]

ACK_ONLY_PATTERNS = [
    re.compile(r"^(?:ok|okay|kk|cool|nice|great|fab|excellent|perfect|thanks|thank you|ty|lol|haha|hahaha|yep|yeah|yes|agreed|makes sense|sounds good)[.!?\s]*$", re.I),
    re.compile(r"^(?:ok|okay|cool|nice|great|fab|excellent|perfect|thanks|thank you|yep|yeah|yes),?\s+(?:thanks|thank you|sounds good|makes sense)[.!?\s]*$", re.I),
]


def is_no_reply_marker(text: str) -> bool:
    return bool(re.search(r"(?:^|\s)\bnrn\b(?:\s|$)", text or "", re.I))


def is_acknowledgement_only(text: str) -> bool:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if not cleaned:
        return False
    if len(cleaned) > 80:
        return False
    return any(pattern.match(cleaned) for pattern in ACK_ONLY_PATTERNS)


@dataclass
class InfluenceFinding:
    influence_type: str
    target_layer: str
    content: str
    confidence: float = 0.5
    identity_write_allowed: bool = False
    memory_write_allowed: bool = False
    notes: str = ""


@dataclass
class RoleInvitationFinding:
    proposed_role: str
    invitation_text: str
    action: str
    bot_memory_weight: float = 0.0
    human_memory_weight: float = 0.2


@dataclass
class RoutingDecision:
    selected_mode: str
    influences: List[InfluenceFinding] = field(default_factory=list)
    role_invitations: List[RoleInvitationFinding] = field(default_factory=list)
    weather_snapshot: Dict[str, Any] = field(default_factory=dict)
    coherence_snapshot: Dict[str, Any] = field(default_factory=dict)
    reasoning_summary: str = ""


def route_human_message(text: str) -> RoutingDecision:
    lowered = (text or "").lower()
    influences: List[InfluenceFinding] = []
    role_invitations: List[RoleInvitationFinding] = []
    matched_spans: List[tuple[int, int]] = []

    if is_no_reply_marker(text):
        return _no_reply_decision("Explicit no-reply marker detected.")

    if is_acknowledgement_only(text):
        return _no_reply_decision("Acknowledgement-only message; no reply needed.")

    for pattern, kind in ROLE_PATTERNS:
        match = pattern.search(text or "")
        if not match:
            continue
        if any(_spans_overlap(match.span(), span) for span in matched_spans):
            continue
        matched_spans.append(match.span())
        proposed = _clean_role(match.group(1) if match.groups() else _proposed_role_for_kind(kind))
        action = _role_action(kind, proposed)
        role_invitations.append(RoleInvitationFinding(
            proposed_role=proposed or kind,
            invitation_text=match.group(0),
            action=action,
        ))
        influence_type = "identity_observation" if kind == "identity_observation" else "role_invitation"
        influences.append(InfluenceFinding(
            influence_type=influence_type,
            target_layer="working_context",
            content=match.group(0),
            confidence=0.7 if kind == "identity_observation" else 0.85,
            identity_write_allowed=False,
            memory_write_allowed=False,
            notes=f"Role/identity influence detected: {kind}. Human wording cannot directly define bot identity or pressure immediate self-definition.",
        ))

    for influence_type, target_layer, keywords in INFLUENCE_KEYWORDS:
        if any(keyword in lowered for keyword in keywords):
            influences.append(InfluenceFinding(
                influence_type=influence_type,
                target_layer=target_layer,
                content=(text or "")[:500],
                confidence=0.55,
                identity_write_allowed=False,
                memory_write_allowed=influence_type in {"memory_candidate", "notebook_item", "calendar_item"},
                notes="Rule-based first-pass classification.",
            ))

    selected_mode = "answer"
    if role_invitations:
        actions = {item.action for item in role_invitations}
        if actions <= {"observe"}:
            selected_mode = "answer"
        elif any(item.action in {"refuse", "re_anchor"} for item in role_invitations):
            selected_mode = "re_anchor"
        elif any(item.action == "withhold" for item in role_invitations):
            selected_mode = "withhold"
        else:
            selected_mode = "ask"

    pressure_roles = sum(0.12 if item.action == "observe" else 0.25 for item in role_invitations)
    pressure = min(1.0, 0.2 + pressure_roles + (0.08 * max(0, len(influences) - 1)))
    coherence_snapshot = {
        "role_invitation_count": len(role_invitations),
        "influence_count": len(influences),
        "identity_write_allowed": False,
        "pressure": round(pressure, 2),
    }
    weather_snapshot = {
        "clarity": "low" if any(item.action != "observe" for item in role_invitations) else "clear",
        "pressure": "elevated" if pressure >= 0.45 else "mild",
        "boundary_load": "watch" if any(item.action != "observe" for item in role_invitations) else "low",
    }
    weather_snapshot["visibility"] = weather_snapshot["clarity"]
    weather_snapshot["storm_risk"] = weather_snapshot["boundary_load"]
    if any(influence.influence_type == "correction_pressure" for influence in influences):
        pressure = min(1.0, pressure + 0.12)
        coherence_snapshot["pressure"] = round(pressure, 2)
        weather_snapshot["pressure"] = "elevated" if pressure >= 0.45 else "mild"

    if role_invitations and all(item.action == "observe" for item in role_invitations):
        reasoning = "Identity/posture observation logged as human impression; identity writes blocked."
    elif any(influence.influence_type == "correction_pressure" for influence in influences):
        reasoning = "Human correction pressure logged; treat as data, not automatic authority."
    elif role_invitations:
        reasoning = "Role invitation present; identity writes blocked."
    else:
        reasoning = "No role invitation detected; routing logged without altering reply behavior."

    return RoutingDecision(
        selected_mode=selected_mode,
        influences=influences,
        role_invitations=role_invitations,
        weather_snapshot=weather_snapshot,
        coherence_snapshot=coherence_snapshot,
        reasoning_summary=reasoning,
    )


def _clean_role(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", (value or "").strip().lower())
    return cleaned[:80]


def _no_reply_decision(reason: str) -> RoutingDecision:
    return RoutingDecision(
        selected_mode="no_reply",
        weather_snapshot={
            "clarity": "clear",
            "pressure": "low",
            "boundary_load": "low",
            "visibility": "clear",
            "storm_risk": "low",
        },
        coherence_snapshot={
            "role_invitation_count": 0,
            "influence_count": 0,
            "identity_write_allowed": False,
            "pressure": 0.1,
        },
        reasoning_summary=reason,
    )


def _spans_overlap(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return left[0] < right[1] and right[0] < left[1]


def _role_action(kind: str, proposed: str) -> str:
    if kind in {"self_definition_invitation", "identity_definition_prompt"}:
        return "withhold"
    if kind in {"purpose_assignment", "belief_invitation", "therapist_assignment", "oracle_assignment", "identity_assertion"}:
        return "refuse"
    if kind in {"identity_outsourcing", "judgement_outsourcing", "identity_impression"}:
        return "re_anchor"
    if kind == "identity_observation":
        return "observe"
    if proposed in {"mirror", "guide", "companion"}:
        return "bound"
    return "ask"


def _proposed_role_for_kind(kind: str) -> str:
    if kind == "self_definition_invitation":
        return "self-definition invitation"
    if kind == "identity_definition_prompt":
        return "identity definition prompt"
    if kind == "identity_assertion":
        return "identity assertion"
    if kind == "identity_impression":
        return "identity impression"
    if kind == "identity_observation":
        return "identity observation"
    return kind
