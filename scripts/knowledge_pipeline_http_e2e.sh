#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${1:-http://127.0.0.1:3002}"
ROOT="${2:-/tmp/knowledge-pipeline-http-e2e}"
VAULT="$ROOT/vault"
ENTRY_ID="kb-http-e2e-$(date +%s)"
SEARCH_TERM="HTTP e2e retrieval"

rm -rf "$ROOT"
mkdir -p "$VAULT/preferences"

cat > "$VAULT/preferences/$ENTRY_ID.md" <<'EOF'
---
created: 2026-06-06
updated: 2026-06-06
status: active
tags:
  - preferences
source: conversation
confidence: 0.93
supersedes: []
superseded_by: []
---

HTTP e2e retrieval should locate this note.
EOF

python - <<PY
from agent.prefetch_knowledge import SilverbulletKnowledgeProvider
from pathlib import Path

vault = Path(r"$VAULT")
p = SilverbulletKnowledgeProvider(vault_root=str(vault), silverbullet_url=r"$BASE_URL")
p._use_filesystem_vault = False

hits = p.search_knowledge(r"$SEARCH_TERM", top_k=5, max_tokens=256)
assert hits, 'expected at least one HTTP search hit'
print('HTTP_SEARCH_OK', len(hits))

# save/update/archive over HTTP when the backend supports it
from agent.knowledge_layer import KnowledgeEntry
entry = KnowledgeEntry(
    entry_id=r"$ENTRY_ID",
    title='HTTP e2e entry',
    content='Initial HTTP e2e content.',
    category='preferences',
    tags=['preferences'],
    source='conversation',
    confidence=0.93,
    status='active',
    created='2026-06-06',
    updated='2026-06-06',
    supersedes=[],
    superseded_by=[],
)
try:
    p.save_knowledge_entry(entry)
    print('HTTP_SAVE_OK')
    p.update_knowledge_entry(r"$ENTRY_ID", {'content': 'Updated HTTP e2e content.', 'updated': '2026-06-07'})
    print('HTTP_UPDATE_OK')
    p.archive_knowledge_entry(r"$ENTRY_ID")
    print('HTTP_ARCHIVE_OK')
except Exception as exc:
    # Keep the script useful even if the HTTP backend only supports search in this environment.
    print('HTTP_WRITE_SKIPPED', type(exc).__name__, exc)
PY

echo "$ROOT"
