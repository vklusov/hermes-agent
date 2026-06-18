"""Knowledge layer for Silverbullet vault.

Implements MemoryProvider to plug into Hermes' existing prefetch pipeline.
Before each turn, searches the Silverbullet vault knowledge.
After each turn, runs knowledge extraction.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from agent.knowledge_extractor import (
    ExtractorConfig,
    extract_and_format_summary,
)
from agent.knowledge_auto_update import decide_auto_update
from agent.knowledge_layer import KnowledgeEntry, KnowledgeExtractionCandidate, KnowledgeSearchHit
from agent.memory_provider import MemoryProvider
from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)


@dataclass
class KnowledgePrefetchConfig:
    """Configuration for knowledge retrieval."""

    top_k: int = 3
    max_context_tokens: int = 600
    max_summary_chars: int = 400
    min_score: float = 0.5

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "KnowledgePrefetchConfig":
        return cls(
            top_k=d.get("top_k", cls.top_k),
            max_context_tokens=d.get("max_context_tokens", cls.max_context_tokens),
            max_summary_chars=d.get("max_summary_chars", cls.max_summary_chars),
            min_score=d.get("min_score", cls.min_score),
        )


class SilverbulletKnowledgeProvider(MemoryProvider):
    """Memory provider that retrieves knowledge from a Silverbullet vault."""

    name = "silverbullet_knowledge"

    def __init__(
        self,
        config: KnowledgePrefetchConfig | None = None,
        search_fn: Callable[[str], list[KnowledgeSearchHit]] | None = None,
        extractor_config: ExtractorConfig | None = None,
        vault_root: str | None = None,
        silverbullet_url: str | None = None,
    ) -> None:
        super().__init__()
        self._config = config or KnowledgePrefetchConfig()
        self._search_fn = search_fn
        self._extractor_config = extractor_config or ExtractorConfig()
        default_vault_root = get_hermes_home() / "knowledge"
        self._vault_root = Path(vault_root).expanduser() if vault_root else default_vault_root
        self._silverbullet_url = (silverbullet_url or os.environ.get("SILVERBULLET_URL") or "http://127.0.0.1:3002").rstrip("/")
        self._silverbullet_search_endpoint = os.environ.get("SILVERBULLET_SEARCH_ENDPOINT", "/api/v1/search")
        self._silverbullet_note_endpoint = os.environ.get("SILVERBULLET_NOTE_ENDPOINT", "/api/v1/note")
        self._silverbullet_upsert_endpoint = os.environ.get("SILVERBULLET_UPSERT_ENDPOINT", "/api/v1/note")
        self._silverbullet_archive_endpoint = os.environ.get("SILVERBULLET_ARCHIVE_ENDPOINT", "/api/v1/note/archive")
        self._silverbullet_vault_root = Path(
            os.environ.get("SILVERBULLET_VAULT_ROOT", str(default_vault_root))
        ).expanduser()
        self._use_filesystem_vault = os.environ.get("SILVERBULLET_IO_MODE", "filesystem") == "filesystem"

    def is_available(self) -> bool:
        return True

    def initialize(self, **kwargs: Any) -> None:
        pass

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Search the knowledge base before each turn."""
        if not query or not query.strip():
            return ""

        query = query.strip()
        hits = self._search_vault(query)
        if not hits:
            return ""

        hits = [h for h in hits if (h.score or 0) >= self._config.min_score]
        hits = hits[: self._config.top_k]
        if not hits:
            return ""

        logger.info(
            "Knowledge prefetch hits: query=%r hits=%d sources=%s",
            query,
            len(hits),
            ", ".join(hit.source for hit in hits[:3] if hit.source) or "<none>",
        )

        return self._render_hits(hits)

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        """Post-response: run knowledge extraction and persist candidates."""
        block = extract_and_format_summary(
            user_content,
            assistant_content,
            config=self._extractor_config,
        )
        if block:
            logger.info("Knowledge extraction:\n%s", block)
        try:
            candidates = self._extract_candidates(user_content, assistant_content)
            for candidate in candidates:
                self._apply_candidate(candidate, session_id=session_id)
        except Exception:
            logger.exception("Knowledge extraction/save failed")

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return []

    def handle_tool_call(self, tool_name: str, args: dict[str, Any], **kwargs: Any) -> str:
        raise ValueError(f"{self.name} has no tool '{tool_name}'")

    def search_knowledge(self, query: str, top_k: int, max_tokens: int) -> list[KnowledgeSearchHit]:
        hits = [hit for hit in self._search_vault(query) if (hit.score or 0) >= self._config.min_score]
        limited: list[KnowledgeSearchHit] = []
        total_tokens = 0
        for hit in hits:
            if len(limited) >= top_k:
                break
            hit_tokens = hit.tokens or max(1, len(hit.excerpt) // 4)
            if total_tokens + hit_tokens > max_tokens and limited:
                break
            limited.append(hit)
            total_tokens += hit_tokens
        return limited

    def save_knowledge_entry(self, entry):
        return self._write_entry(entry, action="save")

    def update_knowledge_entry(self, entry_id: str, patch: dict[str, Any]):
        return self._mutate_entry(entry_id, patch, action="update")

    def archive_knowledge_entry(self, entry_id: str):
        return self._mutate_entry(entry_id, {"status": "archived"}, action="archive")

    def auto_update_knowledge_entry(
        self,
        candidate: KnowledgeExtractionCandidate,
        existing_entry: KnowledgeEntry | None = None,
    ):
        decision = decide_auto_update(candidate, existing_entry)
        if decision.decision == "apply_update":
            if existing_entry is None:
                self.save_knowledge_entry(decision.patch)
            else:
                self.update_knowledge_entry(existing_entry.entry_id, decision.patch)
        return decision

    def _search_vault(self, query: str) -> list[KnowledgeSearchHit]:
        """Actual search implementation.

        If a custom search function was supplied, delegate to it.
        Otherwise query Silverbullet and fall back to local vault indexing.
        """
        if self._search_fn:
            try:
                return self._search_fn(query)
            except Exception:
                logger.exception("Custom knowledge search failed for query: %s", query)
                return []

        return self._search_silverbullet(query)

    def _search_vault_local(self, query: str) -> list[KnowledgeSearchHit]:
        if not self._vault_root.exists():
            logger.debug("Knowledge vault not found: %s", self._vault_root)
            return []

        terms = [t.lower() for t in query.split() if t.strip()]
        if not terms:
            return []

        hits: list[KnowledgeSearchHit] = []
        for path in self._vault_root.rglob("*.md"):
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:
                logger.exception("Failed to read knowledge file: %s", path)
                continue

            lowered = text.lower()
            score = sum(lowered.count(term) for term in terms)
            if score <= 0:
                continue

            excerpt = self._build_excerpt(text, terms)
            meta = self._parse_frontmatter(text)
            status = str(meta.get("status") or "active").strip().lower()
            if status != "active":
                continue
            entry_id = meta.get("entry_id") or path.stem
            category = meta.get("category") or path.parent.name
            tags = tuple(meta.get("tags") or ())
            source = meta.get("source") or f"silverbullet://{path.relative_to(self._vault_root).as_posix()}"
            reason = f"Matched {score} keyword occurrence(s) in local knowledge vault."
            hits.append(
                KnowledgeSearchHit(
                    entry_id=str(entry_id),
                    category=str(category),
                    score=min(1.0, 0.3 + (score / 5.0)),
                    reason=reason,
                    excerpt=excerpt,
                    source=str(source),
                    tags=tags,
                    tokens=max(1, len(excerpt) // 4),
                )
            )

        hits.sort(key=lambda h: (h.score or 0, h.entry_id), reverse=True)
        return hits

    def _search_silverbullet(self, query: str) -> list[KnowledgeSearchHit]:
        if self._use_filesystem_vault:
            return self._search_vault_local(query)
        try:
            payload = self._silverbullet_request(
                "GET",
                self._silverbullet_search_endpoint,
                query={"q": query, "limit": str(self._config.top_k)},
            )
        except Exception:
            logger.exception("Silverbullet search failed; falling back to local vault")
            return self._search_vault_local(query)

        hits: list[KnowledgeSearchHit] = []
        items = payload.get("items") if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            return self._search_vault_local(query)

        for item in items[: self._config.top_k]:
            if not isinstance(item, dict):
                continue
            tags = item.get("tags") or []
            if isinstance(tags, str):
                tags = [tags]
            hits.append(
                KnowledgeSearchHit(
                    entry_id=str(item.get("entry_id") or item.get("id") or item.get("path") or ""),
                    category=str(item.get("category") or "knowledge"),
                    score=float(item.get("score") or item.get("confidence") or 0.0),
                    reason=str(item.get("reason") or "Matched in Silverbullet"),
                    excerpt=str(item.get("excerpt") or item.get("content") or ""),
                    source=str(item.get("source") or item.get("path") or f"silverbullet://{item.get('entry_id', '')}"),
                    tags=tuple(str(t) for t in tags),
                    tokens=int(item.get("tokens") or max(1, len(str(item.get("excerpt") or "")) // 4)),
                )
            )
        if not hits:
            return self._search_vault_local(query)
        hits.sort(key=lambda h: (h.score or 0, h.entry_id), reverse=True)
        return hits

    def _extract_candidates(self, user_content: str, assistant_content: str) -> list[dict[str, Any]]:
        return []

    def _apply_candidate(self, candidate: dict[str, Any], *, session_id: str = "") -> None:
        action = candidate.get("action")
        if action == "ignore":
            return
        logger.info("Knowledge candidate (%s) session=%s: %s", action, session_id, candidate)

    def _write_entry(self, entry: Any, *, action: str):
        payload = self._entry_payload(entry)
        try:
            if self._use_filesystem_vault:
                return self._write_entry_to_vault(payload)
            return self._silverbullet_request(
                "POST",
                self._silverbullet_upsert_endpoint,
                json_body=payload,
            )
        except Exception:
            logger.exception("Silverbullet %s failed", action)
            return None

    def _mutate_entry(self, entry_id: str, patch: dict[str, Any], *, action: str):
        try:
            if self._use_filesystem_vault:
                if action == "archive":
                    return self._archive_entry_in_vault(entry_id)
                return self._update_entry_in_vault(entry_id, patch)
            payload = {"entry_id": entry_id, **patch}
            endpoint = self._silverbullet_archive_endpoint if action == "archive" else self._silverbullet_note_endpoint
            return self._silverbullet_request(
                "PATCH",
                endpoint,
                json_body=payload,
                query={"entry_id": entry_id},
            )
        except Exception:
            logger.exception("Silverbullet %s failed for %s", action, entry_id)
            return None

    def _write_entry_to_vault(self, payload: dict[str, Any]):
        from dataclasses import fields
        from agent.knowledge_layer import KnowledgeEntry

        allowed = {f.name for f in fields(KnowledgeEntry)}
        kwargs = {k: v for k, v in payload.items() if k in allowed}
        if isinstance(kwargs.get("tags"), list):
            kwargs["tags"] = tuple(kwargs["tags"])
        if isinstance(kwargs.get("supersedes"), list):
            kwargs["supersedes"] = tuple(kwargs["supersedes"])
        if isinstance(kwargs.get("superseded_by"), list):
            kwargs["superseded_by"] = tuple(kwargs["superseded_by"])
        kwargs.setdefault("entry_id", payload.get("entry_id", "unknown"))
        kwargs.setdefault("category", payload.get("category", "manual"))
        kwargs.setdefault("content", payload.get("content", ""))
        entry = KnowledgeEntry(**kwargs)
        path = self._entry_path(entry)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(entry.to_markdown(), encoding="utf-8")
        return {"path": str(path), "entry_id": entry.entry_id}

    def _update_entry_in_vault(self, entry_id: str, patch: dict[str, Any]):
        path = self._resolve_entry_path(entry_id)
        if not path.exists():
            raise FileNotFoundError(f"Knowledge entry not found: {entry_id}")
        from agent.knowledge_layer import KnowledgeEntry
        raw = path.read_text(encoding="utf-8")
        frontmatter, body = self._split_markdown(raw)
        entry = self._entry_from_markdown(frontmatter, body)
        for key, value in patch.items():
            if hasattr(entry, key):
                setattr(entry, key, value)
            else:
                entry.metadata[key] = value
        entry.updated = self._today()
        path.write_text(entry.to_markdown(), encoding="utf-8")
        return {"path": str(path), "entry_id": entry.entry_id}

    def _entry_from_markdown(self, frontmatter: dict[str, Any], body: str):
        from dataclasses import fields
        from agent.knowledge_layer import KnowledgeEntry

        allowed = {f.name for f in fields(KnowledgeEntry)}
        kwargs = {k: v for k, v in frontmatter.items() if k in allowed}
        kwargs.setdefault("entry_id", frontmatter.get("entry_id", Path("unknown").stem))
        kwargs.setdefault("category", frontmatter.get("category", "manual"))
        kwargs.setdefault("content", body.strip())
        kwargs["tags"] = tuple(kwargs.get("tags") or [])
        kwargs["supersedes"] = tuple(kwargs.get("supersedes") or [])
        kwargs["superseded_by"] = tuple(kwargs.get("superseded_by") or [])
        kwargs.setdefault("metadata", {k: v for k, v in frontmatter.items() if k not in allowed})
        return KnowledgeEntry(**kwargs)

    def _split_markdown(self, text: str) -> tuple[dict[str, Any], str]:
        if not text.startswith("---\n"):
            return {}, text
        end = text.find("\n---\n", 4)
        if end == -1:
            return {}, text
        fm_text = text[4:end]
        body = text[end + 5 :]
        frontmatter = self._parse_frontmatter_block(fm_text)
        return frontmatter, body

    def _parse_frontmatter_block(self, text: str) -> dict[str, Any]:
        raw = text.splitlines()
        data: dict[str, Any] = {}
        current_key: str | None = None
        for line in raw:
            if line.startswith("  - ") and current_key:
                data.setdefault(current_key, []).append(line[4:].strip())
                continue
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            current_key = key
            if value == "":
                data[key] = []
            elif value.startswith("[") and value.endswith("]"):
                try:
                    data[key] = json.loads(value)
                except Exception:
                    data[key] = value
            else:
                data[key] = value
        return data

    def _archive_entry_in_vault(self, entry_id: str):
        return self._update_entry_in_vault(entry_id, {"status": "archived"})

    def _resolve_entry_path(self, entry_id: str) -> Path:
        for path in self._vault_root.rglob("*.md"):
            if path.stem == entry_id:
                return path
        return self._vault_root / "manual" / f"{entry_id}.md"

    def _entry_path(self, entry: Any) -> Path:
        category = getattr(entry, "category", None) or (entry.get("category") if isinstance(entry, dict) else None) or "manual"
        entry_id = getattr(entry, "entry_id", None) or (entry.get("entry_id") if isinstance(entry, dict) else None) or "unknown"
        return self._vault_root / str(category) / f"{entry_id}.md"

    def _today(self) -> str:
        from datetime import date
        return date.today().isoformat()

    def _entry_payload(self, entry: Any) -> dict[str, Any]:
        if hasattr(entry, "to_dict"):
            data = dict(entry.to_dict())
        elif isinstance(entry, dict):
            data = dict(entry)
        else:
            data = {"content": str(entry)}
        if "content" not in data:
            data["content"] = getattr(entry, "content", "")
        if "entry_id" not in data:
            data["entry_id"] = getattr(entry, "entry_id", "")
        return data

    def _silverbullet_request(
        self,
        method: str,
        endpoint: str,
        *,
        json_body: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self._silverbullet_url:
            raise RuntimeError("Silverbullet URL is not configured")
        url = f"{self._silverbullet_url}{endpoint}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        body = None
        headers = {"Content-Type": "application/json"}
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                raw = response.read().decode("utf-8", errors="replace")
                if not raw:
                    return {"status": response.status}
                try:
                    return json.loads(raw)
                except Exception:
                    return {"status": response.status, "raw": raw}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Silverbullet HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Silverbullet unavailable: {exc.reason}") from exc

    def _render_hits(self, hits: list[KnowledgeSearchHit]) -> str:
        """Render a compact <knowledge-context> block from search hits."""
        if not hits:
            return ""

        max_chars = max(1, self._config.max_context_tokens * 4)
        opening = "<knowledge-context>"
        closing = "</knowledge-context>"
        min_total = len(opening) + len(closing) + 1
        if max_chars < min_total:
            return (opening + "\n" + closing)[:max_chars]

        parts: list[str] = [opening]
        used = len(opening) + len(closing) + 1  # include the newline before closing
        line_budget = max_chars - used

        for idx, hit in enumerate(hits, 1):
            line = self._format_hit_line(idx, hit, line_budget)
            if not line:
                break
            parts.append(line)
            used += len(line) + 1
            line_budget = max_chars - used
            if line_budget <= 0:
                break

        parts.append(closing)
        rendered = "\n".join(parts)
        return rendered if len(rendered) <= max_chars else rendered[: max_chars - len(closing)] + closing

    @staticmethod
    def _compact_source(source: str) -> str:
        if not source:
            return "-"
        if "://" in source:
            source = source.split("://", 1)[1]
        return source.rsplit("/", 1)[-1]

    def _format_hit_line(self, idx: int, hit: KnowledgeSearchHit, line_budget: int) -> str:
        source = self._compact_source(hit.source)
        tags = ",".join(hit.tags) if hit.tags else "-"
        prefix = f"[{idx}] {hit.entry_id} {hit.category} {hit.score:.2f} {source} {hit.reason} {tags} "
        if line_budget <= 0:
            return ""
        if len(prefix) >= line_budget:
            return self._truncate(f"[{idx}] {hit.entry_id} {hit.category} {hit.score:.2f}", line_budget)

        excerpt_budget = max(1, line_budget - len(prefix))
        excerpt = self._truncate(hit.excerpt, min(self._config.max_summary_chars, excerpt_budget))
        line = prefix + excerpt

        if len(line) > line_budget:
            # Ensure the truncated excerpt keeps an ellipsis visible if possible.
            keep = max(1, line_budget - len(prefix))
            excerpt = self._truncate(hit.excerpt, keep)
            line = prefix + excerpt

        if len(line) > line_budget:
            line = self._truncate(line, line_budget)

        return line

    @staticmethod
    def _truncate(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 1)].rstrip() + "…"

    @staticmethod
    def _parse_frontmatter(text: str) -> dict[str, Any]:
        if not text.startswith("---\n"):
            return {}
        end = text.find("\n---\n", 4)
        if end == -1:
            return {}
        raw = text[4:end].splitlines()
        data: dict[str, Any] = {}
        current_key: str | None = None
        for line in raw:
            if line.startswith("  - ") and current_key:
                data.setdefault(current_key, []).append(line[4:].strip())
                continue
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            current_key = key
            if value == "":
                data[key] = []
            elif value.startswith("[") and value.endswith("]"):
                try:
                    data[key] = json.loads(value)
                except Exception:
                    data[key] = value
            else:
                data[key] = value
        return data

    @staticmethod
    def _build_excerpt(text: str, terms: list[str], limit: int = 240) -> str:
        lowered = text.lower()
        best = 0
        for term in terms:
            pos = lowered.find(term)
            if pos != -1:
                best = pos
                break
        start = max(0, best - limit // 3)
        end = min(len(text), start + limit)
        excerpt = text[start:end].replace("\n", " ").strip()
        return excerpt
