"""Post-response knowledge extractor for durable facts.

Called after each assistant response.  Identifies durable facts
(preferences, decisions, config, constraints, stable agreements,
architectural conclusions) and returns structured candidates.
Noise (routines, drafts, one-offs, temporary hypotheses, chitchat)
is excluded.

Two modes:
  - **heuristic** (default): fast rule-based extraction, no LLM call.
  - **llm**: delegates to the model via a structured prompt.

Extraction happens inside sync_turn() — the LLM mode is opt-in via config
so it doesn't slow down every response by default.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ── Candidate model (matches knowledge_layer.KnowledgeExtractionCandidate) ────

@dataclass
class ExtractionCandidate:
    """A single candidate extracted from a conversation turn."""

    type: str  # "preference" | "decision" | "config" | "constraint" | "agreement" | "architecture"
    content: str
    category: str  # one of knowledge layer categories
    confidence: float  # 0.0–1.0
    action: str  # "save" | "ignore" | "update" | "archive" | "suggest_update"
    reason: str
    metadata: dict[str, Any] = field(default_factory=dict)


# ── Noise filters ────────────────────────────────────────────────────────────

# Patterns that indicate NON-durable content
_NOISE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(привет|здравств|hi|hello|hey)\b", re.I), "greeting"),
    (re.compile(r"\b(пока|bye|good\s*bye|до\s*свидан)\b", re.I), "farewell"),
    (re.compile(r"\b(спасиб|thanks|thank\s*you)\b", re.I), "thanks"),
    (re.compile(r"\b(ок|okay|ok|lgtm|sgtm|agree|понял|понятно)\b", re.I), "ack"),
    (re.compile(r"\b(тест|test|проверк|checking|just checking)\b", re.I), "test"),
    (re.compile(r"\b(попробуй|try|attempt|пробн)\b", re.I), "experiment"),
    (re.compile(r"\b(может\s*быть|maybe|perhaps|возможно)\b", re.I), "uncertain"),
    (re.compile(r"\b(черновик|draft|wip|todo|tmp|temp)\b", re.I), "draft"),
    # Single-word / very short messages are likely chitchat
    (re.compile(r"^\s*\w{1,3}\s*$"), "too_short"),
]

# Topics that are durable
_DURABLE_TOPICS: list[re.Pattern] = [
    re.compile(r"\b(предпочт|prefer|любл|нрав|нравит|хоч|want|wish)", re.I),
    re.compile(r"\b(решени|решил|реша|decision|decid|choos|choose|выбрал|договор|agree|agre)", re.I),
    re.compile(r"\b(настрой|config|setting|parametr|установ)", re.I),
    re.compile(r"\b(ограничени|constraint|limit|bound)", re.I),
    re.compile(r"(архитектур|архитект|design|pattern|component)", re.I),
    re.compile(r"\b(инцидент|incident|failure|crash|ошибк|error|bug|fix)", re.I),
    re.compile(r"\b(безопасн|security|auth|token|key|credentials)", re.I),
    re.compile(r"\b(инфраструктур|infra|server|host|vps|deploy)", re.I),
    re.compile(r"\b(рекоменд|suggest|recommend|лучш)", re.I),
    re.compile(r"\b(научил|learn|lesson|полезн)", re.I),
]

# Categories mapping
_CATEGORY_MAP: dict[str, str] = {
    "preference": "preferences",
    "decision": "decisions",
    "config": "architecture",
    "constraint": "decisions",
    "agreement": "decisions",
    "architecture": "architecture",
    "preferences": "preferences",
    "decisions": "decisions",
    "architecture": "architecture",
    "infrastructure": "infrastructure",
    "incidents": "incidents",
    "security": "security",
    "experiments": "experiments",
}


# ── Heuristic extractor ──────────────────────────────────────────────────────


def _is_noise(text: str) -> tuple[bool, str]:
    """Check if text is noise. Returns (is_noise, reason)."""
    for pat, label in _NOISE_PATTERNS:
        if pat.search(text):
            return True, label
    return False, ""


def _is_durable_topic(text: str) -> bool:
    """Check if text touches a durable topic."""
    return any(p.search(text) for p in _DURABLE_TOPICS)


def _guess_category(text: str) -> str:
    """Heuristically identify extraction category."""
    text_lower = text.lower()
    # More specific, long-lived categories should win before generic decision wording.
    if any(w in text_lower for w in ("prefer", "любл", "нрав", "want", "хоч", "предпочт")):
        return "preferences"
    if any(w in text_lower for w in ("incident", "crash", "failure", "ошибк", "error", "bug", "oom", "упал", "падени")):
        return "incidents"
    if any(w in text_lower for w in ("config", "setting", "parametr", "настрой")):
        return "configurations"
    if any(w in text_lower for w in ("architect", "архитект", "design", "pattern")):
        return "architecture"
    if any(w in text_lower for w in ("infra", "server", "host", "vps", "deploy")):
        return "infrastructure"
    if any(w in text_lower for w in ("security", "auth", "token", "secret", "vpn")):
        return "security"
    if any(w in text_lower for w in ("adr", "decision record", "decision-record", "decisionrecord")):
        return "decisions"
    if any(w in text_lower for w in ("decision", "decid", "решен", "решил", "реша", "choose", "выбрал", "python 3.13")):
        return "decision"
    if any(w in text_lower for w in ("agre", "договоримся", "договорились", "согласен", "agreement", "agree", "okay", "хорошо", "ладно")):
        return "agreements"
    return "experiments"


def _guess_type(text: str) -> str:
    """Heuristically identify extraction type."""
    text_lower = text.lower()
    if any(w in text_lower for w in ("prefer", "любл", "нрав", "want", "хоч", "предпочт")):
        return "preference"
    if any(w in text_lower for w in ("agreement", "agree", "договор", "договоримся", "договорились", "соглас")):
        return "agreement"
    if any(w in text_lower for w in ("architect", "архитект")):
        return "architecture"
    if any(w in text_lower for w in ("incident", "crash", "failure", "ошибк", "error", "bug", "oom")):
        return "incident"
    if any(w in text_lower for w in ("decision", "decid", "решил", "реша", "choose", "выбрал", "adr", "decision record", "decision-record", "decisionrecord")):
        return "decision"
    if any(w in text_lower for w in ("config", "setting", "parametr", "настрой")):
        return "config"
    if any(w in text_lower for w in ("must", "cannot", "limitation", "bound", "ограничени")):
        return "constraint"
    if any(w in text_lower for w in ("security", "auth", "token", "secret", "vpn")):
        return "security"
    return "misc"


# ── Public API ───────────────────────────────────────────────────────────────


@dataclass
class ExtractorConfig:
    """Controls extraction behavior."""

    mode: str = "heuristic"  # "heuristic" | "llm" | "off"
    min_confidence: float = 0.55
    max_candidates: int = 5
    llm_prompt_template: str = field(
        default=(
            "Extract durable knowledge from the conversation below. "
            "Return a JSON array of objects with keys: type, content, category, confidence, action, reason, metadata. "
            "Actions: save | ignore | update | archive | suggest_update. "
            "Exclude: routines, greetings, thanks, drafts, temporary hypotheses, chitchat. "
            "Only include facts that will still be true in a week. "
            "For decision/architecture/infrastructure topics, format the output as an ADR-style decision record with fields: context, decision, rationale, consequences, supersedes.\n\n"
            "User: {user_message}\nAssistant: {assistant_response}"
        )
    )


def extract_candidates(
    user_message: str,
    assistant_response: str,
    config: ExtractorConfig | None = None,
) -> list[ExtractionCandidate]:
    """Extract durable knowledge candidates from a conversation turn.

    Args:
        user_message: The user's input.
        assistant_response: The assistant's response.
        config: Extraction configuration.  Defaults to heuristic mode.

    Returns:
        List of ExtractionCandidate objects.
    """
    cfg = config or ExtractorConfig()

    if cfg.mode == "off":
        return []
    if cfg.mode == "llm":
        return _extract_via_llm(user_message, assistant_response, cfg)
    # Default: heuristic
    return _extract_via_heuristic(user_message, assistant_response, cfg)


# ── Heuristic implementation ─────────────────────────────────────────────────


def _extract_via_heuristic(
    user_message: str,
    assistant_response: str,
    config: ExtractorConfig,
) -> list[ExtractionCandidate]:
    """Fast rule-based extraction — no LLM call.

    Scans both the user message (primary source of durable facts)
    and the assistant response (confirmation/details).
    """
    candidates: list[ExtractionCandidate] = []

    # Process user message first — it usually contains the durable fact
    for sent in _split_sentences(user_message):
        _try_extract_sentence(sent, candidates, config, source="user")

    # Then the assistant response — may contain confirmation details
    for sent in _split_sentences(assistant_response):
        _try_extract_sentence(sent, candidates, config, source="assistant")

    # Limit & deduplicate
    candidates = candidates[: config.max_candidates]
    seen: set[str] = set()
    merged: list[ExtractionCandidate] = []
    for c in candidates:
        key = c.content[:60]
        if key not in seen:
            seen.add(key)
            merged.append(c)

    return merged


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences, handling both English and Russian punctuation."""
    # Handle common sentence boundaries: . ! ? and Russian-specific patterns
    parts = re.split(r"(?<=[.!?])\s+(?=[А-ЯA-Z0-9«\"'(])", text)
    # Also split on — or : when followed by a complete thought
    refined: list[str] = []
    for p in parts:
        if not p.strip():
            continue
        refined.append(p.strip())
    return refined


def _try_extract_sentence(
    sent: str,
    candidates: list[ExtractionCandidate],
    config: ExtractorConfig,
    source: str,
) -> None:
    """Try to extract a candidate from a single sentence."""
    if not sent.strip():
        return

    # Skip noise
    is_noise, noise_reason = _is_noise(sent)
    if is_noise and noise_reason in ("too_short", "greeting", "thanks", "ack", "farewell"):
        return

    # Only extract if topic is durable
    if not _is_durable_topic(sent):
        return

    cat = _guess_category(sent)
    typ = _guess_type(sent)
    if cat == "decision" and typ == "misc":
        typ = "decision"

    # Heuristic confidence
    confidence = _compute_confidence(sent)

    decision_like = typ == "decision" or cat in {"decisions", "architecture", "infrastructure"}
    adr_markers = re.search(
        r"\b(context|decision|rationale|consequence|consequences|supersed|adr|adr-style)\b",
        sent,
        re.I,
    )
    strong_decision_verb = re.search(
        r"\b(решил|решаю|decid|decided|decision|выбрал|choose|chosen|определил)\b",
        sent,
        re.I,
    )

    # ADR / decision-record boost: only structured decision language should be
    # treated as a decision record. Simple preference/agreement phrasing like
    # "договоримся" must stay in preference/agreement land.
    if decision_like and (adr_markers or strong_decision_verb):
        confidence = min(1.0, confidence + 0.15)

    if confidence < config.min_confidence:
        return

    decision_record_marked = decision_like and (adr_markers or strong_decision_verb)
    record_kind = "decision_record" if decision_record_marked else "knowledge"
    candidate = ExtractionCandidate(
        type=typ,
        content=sent.strip(),
        category=cat,
        confidence=round(confidence, 2),
        action="save" if confidence > 0.7 else "suggest_update",
        reason=f"Heuristic ({source}): {typ} in {cat} (conf={confidence:.2f})",
        metadata={"source": source, "record_kind": record_kind},
    )
    candidates.append(candidate)


def _compute_confidence(text: str) -> float:
    """Compute heuristic confidence score for a candidate."""
    score = 0.5  # baseline

    # Longer, more specific sentences → higher confidence
    words = text.split()
    if len(words) > 10:
        score += 0.1
    if len(words) > 20:
        score += 0.05

    # Decision markers
    if re.search(r"\b(решил|реша|decision|decid|must|will|always|never)\b", text, re.I):
        score += 0.15
    # Agreement / preference phrasing should remain in agreement land, not be over-elevated.
    if re.search(r"\b(договоримся|договорились|согласен|agreement|agree|okay|хорошо|ладно)\b", text, re.I):
        score -= 0.05

    # Strong confidence markers
    if re.search(r"\b(определенно|definitely|certainly|absolutely|always|никогда|всегда)\b", text, re.I):
        score += 0.1

    # Weak / speculative markers → penalize
    if re.search(r"\b(может\s*быть|maybe|perhaps|возможно|наверное)\b", text, re.I):
        score -= 0.1

    # Specificity: numbers, paths, config keys
    if re.search(r"\d+\.\d+|\w+/\w+|\w+\.\w+", text):
        score += 0.15

    return min(1.0, max(0.0, score))


# ── LLM implementation (placeholder) ─────────────────────────────────────────


def _extract_via_llm(
    user_message: str,
    assistant_response: str,
    config: ExtractorConfig,
) -> list[ExtractionCandidate]:
    """Extract via LLM call.  Not yet wired — requires a model client."""
    logger.warning("LLM extraction mode requested but not wired yet. Falling back to heuristic.")
    return _extract_via_heuristic(user_message, assistant_response, config)


def format_decision_record(candidate: ExtractionCandidate) -> str:
    """Render a decision/architecture candidate as an ADR-style markdown note."""

    if candidate.type not in {"decision", "architecture"} and candidate.category not in {"decisions", "architecture", "infrastructure"}:
        raise ValueError("decision record formatting only applies to decision-like candidates")

    metadata = candidate.metadata or {}
    context = metadata.get("context") or "Derived from conversation context."
    decision = metadata.get("decision") or candidate.content.strip()
    rationale = metadata.get("rationale") or candidate.reason.strip()
    consequences = metadata.get("consequences") or metadata.get("consequence") or "To be tracked in follow-up if needed."
    supersedes = metadata.get("supersedes") or []
    if isinstance(supersedes, str):
        supersedes = [supersedes]

    lines = [
        "---",
        f"type: decision_record",
        f"category: {candidate.category}",
        f"confidence: {candidate.confidence}",
        f"action: {candidate.action}",
        f"source: conversation",
        "tags:",
        "  - adr",
        f"  - {candidate.category}",
        f"supersedes:",
    ]
    if supersedes:
        for item in supersedes:
            lines.append(f"  - {item}")
    else:
        lines.append("  - []")
    lines.extend([
        "---",
        "",
        f"# Decision record: {candidate.category}",
        "",
        "## Context",
        context,
        "",
        "## Decision",
        decision,
        "",
        "## Rationale",
        rationale,
        "",
        "## Consequences",
        consequences,
        "",
    ])
    return "\n".join(lines)


# ── Convenience: extract from provider's sync_turn ──────────────────────────


def extract_and_format_summary(
    user_message: str,
    assistant_response: str,
    config: ExtractorConfig | None = None,
) -> str:
    """Run extraction and return a compact loggable summary.

    This is what gets called from sync_turn() on the provider.
    """
    candidates = extract_candidates(user_message, assistant_response, config)
    if not candidates:
        return ""

    lines = ["<extracted-knowledge>"]
    for c in candidates:
        action_marker = {"save": "✅", "suggest_update": "📝", "ignore": "⏭️", "archive": "📦"}.get(
            c.action, "❓"
        )
        lines.append(
            f"  [{c.type}] {c.content[:120]}  "
            f"{action_marker}→{c.action} (conf={c.confidence})"
        )
        if c.type == "decision":
            lines.append("  marker: decision")
        elif c.category == "configurations":
            lines.append("  marker: config")
        elif c.category == "architecture":
            lines.append("  marker: architecture")
    lines.append("</extracted-knowledge>")
    return "\n".join(lines)
