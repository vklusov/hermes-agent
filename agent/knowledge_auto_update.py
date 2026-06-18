"""Auto-update policy for durable knowledge entries.

Step 8 of the knowledge pipeline: decide whether a candidate should be
applied automatically, suggested for review, or ignored.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal
import re

from agent.knowledge_layer import KnowledgeEntry, KnowledgeExtractionCandidate

AutoUpdateDecision = Literal["no_change", "suggest_update", "apply_update"]


@dataclass(slots=True)
class KnowledgeAutoUpdateResult:
    decision: AutoUpdateDecision
    reason: str
    confidence: float
    patch: dict[str, Any] = field(default_factory=dict)
    existing_entry_id: str | None = None
    candidate_action: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "reason": self.reason,
            "confidence": self.confidence,
            "patch": dict(self.patch),
            "existing_entry_id": self.existing_entry_id,
            "candidate_action": self.candidate_action,
        }


@dataclass(slots=True)
class KnowledgeAutoUpdatePolicy:
    apply_threshold: float = 0.85
    suggest_threshold: float = 0.55

    def decide(
        self,
        candidate: KnowledgeExtractionCandidate,
        existing_entry: KnowledgeEntry | None = None,
    ) -> KnowledgeAutoUpdateResult:
        action = (candidate.action or "").strip().lower()
        confidence = float(candidate.confidence)

        if action == "ignore" or confidence < self.suggest_threshold:
            return KnowledgeAutoUpdateResult(
                decision="no_change",
                reason="Low confidence or explicitly ignored candidate.",
                confidence=confidence,
                existing_entry_id=getattr(existing_entry, "entry_id", None),
                candidate_action=action,
            )

        if action == "archive":
            return KnowledgeAutoUpdateResult(
                decision="suggest_update",
                reason="Archiving should be reviewed manually before applying.",
                confidence=confidence,
                patch={"status": "archived"},
                existing_entry_id=getattr(existing_entry, "entry_id", None),
                candidate_action=action,
            )

        if existing_entry is None:
            if action == "save" and confidence >= self.apply_threshold:
                return KnowledgeAutoUpdateResult(
                    decision="apply_update",
                    reason="High-confidence durable fact with no existing entry.",
                    confidence=confidence,
                    patch=self._candidate_patch(candidate, existing_entry=None),
                    candidate_action=action,
                )
            if action == "save" and confidence >= self.suggest_threshold:
                return KnowledgeAutoUpdateResult(
                    decision="suggest_update",
                    reason="Candidate is plausible but should be reviewed before creating a new durable record.",
                    confidence=confidence,
                    patch=self._candidate_patch(candidate, existing_entry=None),
                    candidate_action=action,
                )
            return KnowledgeAutoUpdateResult(
                decision="no_change",
                reason="New knowledge candidate did not clear the save gate.",
                confidence=confidence,
                candidate_action=action,
            )

        similarity = self._content_similarity(existing_entry.content, candidate.content)
        if action == "save":
            if confidence >= self.apply_threshold and similarity >= 0.4:
                return KnowledgeAutoUpdateResult(
                    decision="apply_update",
                    reason="High-confidence save that closely matches an existing record.",
                    confidence=confidence,
                    patch=self._candidate_patch(candidate, existing_entry=existing_entry),
                    existing_entry_id=existing_entry.entry_id,
                    candidate_action=action,
                )
            if confidence >= self.suggest_threshold:
                return KnowledgeAutoUpdateResult(
                    decision="suggest_update",
                    reason="Candidate is relevant but not confident enough to overwrite automatically.",
                    confidence=confidence,
                    patch=self._candidate_patch(candidate, existing_entry=existing_entry),
                    existing_entry_id=existing_entry.entry_id,
                    candidate_action=action,
                )
            return KnowledgeAutoUpdateResult(
                decision="no_change",
                reason="Save candidate did not clear the confidence floor.",
                confidence=confidence,
                existing_entry_id=existing_entry.entry_id,
                candidate_action=action,
            )

        if action == "update":
            if confidence >= self.apply_threshold and similarity >= 0.5:
                return KnowledgeAutoUpdateResult(
                    decision="apply_update",
                    reason="High-confidence update to a closely related existing record.",
                    confidence=confidence,
                    patch=self._candidate_patch(candidate, existing_entry=existing_entry),
                    existing_entry_id=existing_entry.entry_id,
                    candidate_action=action,
                )
            return KnowledgeAutoUpdateResult(
                decision="suggest_update",
                reason="Update is plausible but should be reviewed before applying.",
                confidence=confidence,
                patch=self._candidate_patch(candidate, existing_entry=existing_entry),
                existing_entry_id=existing_entry.entry_id,
                candidate_action=action,
            )

        return KnowledgeAutoUpdateResult(
            decision="no_change",
            reason="Unsupported candidate action for auto-update.",
            confidence=confidence,
            existing_entry_id=getattr(existing_entry, "entry_id", None),
            candidate_action=action,
        )

    def _candidate_patch(
        self,
        candidate: KnowledgeExtractionCandidate,
        *,
        existing_entry: KnowledgeEntry | None,
    ) -> dict[str, Any]:
        patch: dict[str, Any] = {
            "title": candidate.metadata.get("title") or self._infer_title(candidate.content),
            "content": candidate.content,
            "category": candidate.category,
            "status": "active",
            "source": candidate.metadata.get("source", "conversation"),
            "confidence": round(float(candidate.confidence), 2),
            "updated": date.today().isoformat(),
            "tags": candidate.metadata.get("tags", [candidate.category]),
        }
        if existing_entry is not None:
            patch["entry_id"] = existing_entry.entry_id
            patch.setdefault("supersedes", [existing_entry.entry_id])
            patch.setdefault("superseded_by", [])
        else:
            patch.setdefault("supersedes", [])
            patch.setdefault("superseded_by", [])
        return patch

    def _content_similarity(self, a: str, b: str) -> float:
        a_norm = self._normalize(a)
        b_norm = self._normalize(b)
        if not a_norm and not b_norm:
            return 1.0
        if a_norm == b_norm:
            return 1.0
        a_words = set(a_norm.split())
        b_words = set(b_norm.split())
        if not a_words or not b_words:
            return 0.0
        overlap = len(a_words & b_words)
        union = len(a_words | b_words)
        return overlap / union if union else 0.0

    def _normalize(self, text: str) -> str:
        return re.sub(r"\s+", " ", text).strip().lower()

    def _infer_title(self, content: str) -> str:
        first_line = next((line.strip() for line in content.splitlines() if line.strip()), "")
        if not first_line:
            return "Untitled knowledge entry"
        return first_line[:80]


def decide_auto_update(
    candidate: KnowledgeExtractionCandidate,
    existing_entry: KnowledgeEntry | None = None,
    *,
    apply_threshold: float = 0.85,
    suggest_threshold: float = 0.55,
) -> KnowledgeAutoUpdateResult:
    return KnowledgeAutoUpdatePolicy(
        apply_threshold=apply_threshold,
        suggest_threshold=suggest_threshold,
    ).decide(candidate, existing_entry)
