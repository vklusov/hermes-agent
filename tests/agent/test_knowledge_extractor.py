"""Tests for knowledge extractor — post-response durable fact extraction."""

from __future__ import annotations

from agent.knowledge_extractor import (
    ExtractorConfig,
    ExtractionCandidate,
    extract_and_format_summary,
    extract_candidates,
    format_decision_record,
)


class TestExtractionCandidate:
    def test_minimal(self):
        c = ExtractionCandidate(
            type="preference",
            content="User prefers short answers",
            category="preferences",
            confidence=0.85,
            action="save",
            reason="Heuristic preference match",
        )
        assert c.type == "preference"
        assert c.confidence == 0.85


class TestExtractorConfig:
    def test_defaults(self):
        cfg = ExtractorConfig()
        assert cfg.mode == "heuristic"
        assert cfg.min_confidence == 0.55
        assert cfg.max_candidates == 5


class TestHeuristicExtraction:
    def test_off_mode_returns_empty(self):
        cfg = ExtractorConfig(mode="off")
        assert extract_candidates("hi", "hello", cfg) == []

    def test_empty_response(self):
        assert extract_candidates("hi", "", ExtractorConfig()) == []

    def test_greeting_noise_filtered(self):
        """Greetings are noise — no candidates."""
        candidates = extract_candidates(
            "Привет!",
            "Привет! Чем могу помочь?",
            ExtractorConfig(),
        )
        assert candidates == []

    def test_thanks_noise_filtered(self):
        candidates = extract_candidates(
            "Спасибо большое!",
            "Пожалуйста! Обращайтесь.",
            ExtractorConfig(),
        )
        assert candidates == []

    def test_preference_extracted(self):
        """Durable preference should be extracted."""
        candidates = extract_candidates(
            "Давай договоримся: я предпочитаю краткие ответы",
            "Хорошо, буду отвечать коротко и по делу.",
            ExtractorConfig(min_confidence=0.3),
        )
        assert len(candidates) >= 1
        # At least one should have type 'preference' or 'agreement'
        types = {c.type for c in candidates}
        assert "preference" in types or "agreement" in types

    def test_decision_extracted(self):
        candidates = extract_candidates(
            "Я решил: используем PostgreSQL для всех новых проектов",
            "Принято. PostgreSQL — стандарт для новых проектов.",
            ExtractorConfig(min_confidence=0.3),
        )
        types = {c.type for c in candidates}
        assert "decision" in types or "agreement" in types

    def test_constraint_extracted(self):
        candidates = extract_candidates(
            "Важное ограничение: нельзя использовать Docker в продакшене",
            "Понял. No Docker в production.",
            ExtractorConfig(min_confidence=0.3),
        )
        types = {c.type for c in candidates}
        assert "constraint" in types or "decision" in types or "agreement" in types

    def test_config_extracted(self):
        candidates = extract_candidates(
            "Настрой: таймаут HTTP-запросов должен быть 30 секунд",
            "Ок, ставлю timeout=30s.",
            ExtractorConfig(min_confidence=0.3),
        )
        types = {c.type for c in candidates}
        assert "config" in types or "decision" in types

    def test_chitchat_not_extracted(self):
        """Casual conversation should not produce candidates."""
        candidates = extract_candidates(
            "Как дела?",
            "Всё отлично! А у тебя?",
            ExtractorConfig(),
        )
        assert candidates == []

    def test_uncertain_penalized(self):
        """Maybe/perhaps should lower confidence."""
        cfg = ExtractorConfig(min_confidence=0.6)
        candidates = extract_candidates(
            "Может быть, используем Redis?",
            "Да, возможно, это хорошая идея.",
            cfg,
        )
        # Low confidence due to speculative language — might still extract
        # but any candidate should have lower confidence
        for c in candidates:
            assert c.confidence < 0.7

    def test_specific_facts_high_confidence(self):
        """Specific numbers, paths → higher confidence."""
        candidates = extract_candidates(
            "Решение: используем /data/backups/ как путь для бекапов",
            "Принято: /data/backups/ стандартный путь бекапов.",
            ExtractorConfig(min_confidence=0.3),
        )
        assert len(candidates) >= 1
        for c in candidates:
            if "data/backups" in c.content:
                assert c.confidence > 0.6

    def test_draft_filtered(self):
        """Draft/temp/todo content should be excluded."""
        cfg = ExtractorConfig(min_confidence=0.3)
        candidates = extract_candidates(
            "Это черновик: возможно, добавим логи",
            "Да, черновик, ещё не готово.",
            cfg,
        )
        # Might still extract if there are durable markers too
        for c in candidates:
            assert "draft" not in c.action or c.action != "save"

    def test_long_sentence_higher_confidence(self):
        cfg = ExtractorConfig(min_confidence=0.3)
        long_sentence = (
            "Я принял важное архитектурное решение: "
            "все микросервисы должны использовать единый протокол аутентификации "
            "через OAuth2 с передачей JWT-токенов в заголовках запросов."
        )
        candidates = extract_candidates(long_sentence, "Хорошо, архитектура принята.", cfg)
        assert len(candidates) >= 1
        for c in candidates:
            if len(c.content) > 50:
                assert c.confidence >= 0.6

    def test_short_sentence_lower_confidence(self):
        cfg = ExtractorConfig(min_confidence=0.3)
        candidates = extract_candidates("Хочу кратко", "Хорошо", cfg)
        # Very short — should have low confidence or none
        for c in candidates:
            assert c.confidence < 0.7

    def test_max_candidates_limit(self):
        cfg = ExtractorConfig(min_confidence=0.3, max_candidates=2)
        long_text = (
            "Prefer A. Decide B. Config C=1. "
            "Constraint: no D. Architecture E pattern. Agreement F."
        )
        candidates = extract_candidates(long_text, "Принято всё.", cfg)
        assert len(candidates) <= 2

    def test_no_duplicates(self):
        cfg = ExtractorConfig(min_confidence=0.3, max_candidates=10)
        candidates = extract_candidates(
            "Решение: PostgreSQL. Решение: PostgreSQL. Решение: PostgreSQL.",
            "PostgreSQL принят.",
            cfg,
        )
        contents = [c.content[:60] for c in candidates]
        assert len(contents) == len(set(contents)), "Duplicates not deduplicated"

    def test_architecture_category(self):
        cfg = ExtractorConfig(min_confidence=0.3)
        candidates = extract_candidates(
            "Мы выбрали новую архитектуру очередей для обработки событий.",
            "Ок, принято.",
            cfg,
        )
        for c in candidates:
            if "архитектур" in c.content.lower() or "архитек" in c.content.lower():
                assert c.category in {"architecture", "decisions"}
                if c.category in {"architecture", "decisions"}:
                    md = format_decision_record(c)
                    assert md.startswith("---")
                    assert "type: decision_record" in md
                    assert "## Decision" in md
                    assert "## Context" in md
                    assert "## Rationale" in md
                    assert "## Consequences" in md
                    assert c.content in md

    def test_infrastructure_category(self):
        cfg = ExtractorConfig(min_confidence=0.3)
        candidates = extract_candidates(
            "Настроим VPS с 4GB RAM и 2 CPU",
            "Ок, VPS настроен: 4GB, 2 CPU.",
            cfg,
        )
        for c in candidates:
            if "vps" in c.content.lower():
                assert c.category == "infrastructure"

    def test_english_preference(self):
        cfg = ExtractorConfig(min_confidence=0.3)
        candidates = extract_candidates(
            "I prefer short answers in English",
            "OK, I'll keep it brief.",
            cfg,
        )
        types = {c.type for c in candidates}
        assert "preference" in types

    def test_english_decision(self):
        cfg = ExtractorConfig(min_confidence=0.3)
        candidates = extract_candidates(
            "I decided to use Ruff for Python linting",
            "Ruff is now the linter.",
            cfg,
        )
        types = {c.type for c in candidates}
        assert "decision" in types

    def test_incident_detected(self):
        cfg = ExtractorConfig(min_confidence=0.3)
        candidates = extract_candidates(
            "Вчера был инцидент: сервер упал из-за OOM",
            "Да, добавили swap и limit памяти.",
            cfg,
        )
        for c in candidates:
            if "инцидент" in c.content.lower() or "oom" in c.content.lower():
                assert c.category == "incidents"


class TestExtractAndFormatSummary:
    def test_empty_candidates(self):
        result = extract_and_format_summary("Привет", "Привет!")
        assert result == ""

    def test_has_candidates(self):
        cfg = ExtractorConfig(min_confidence=0.3)
        result = extract_and_format_summary(
            "Решение: используем Python 3.13",
            "Принято.",
            cfg,
        )
        assert result.startswith("<extracted-knowledge>")
        assert result.endswith("</extracted-knowledge>")
        assert any(token in result for token in ("decision", "config", "architecture"))

    def test_off_mode(self):
        cfg = ExtractorConfig(mode="off")
        result = extract_and_format_summary("Решение: Python", "Ок.", cfg)
        assert result == ""


class TestIntegrationWithProvider:
    """Verify the extractor wiring in sync_turn doesn't break anything."""

    def test_provider_sync_turn_runs(self):
        from agent.prefetch_knowledge import SilverbulletKnowledgeProvider

        p = SilverbulletKnowledgeProvider()
        # Must not raise
        p.sync_turn("Привет", "Привет!")
        p.sync_turn("Решение: PostgreSQL", "Принято.")
