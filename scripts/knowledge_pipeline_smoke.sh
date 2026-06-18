#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-/tmp/knowledge-pipeline-smoke}"
VAULT="$ROOT/vault"
AUDIT_DAYS="${2:-7}"

rm -rf "$ROOT"
mkdir -p "$VAULT/preferences" "$VAULT/architecture" "$VAULT/incidents"

cat > "$VAULT/preferences/pref-1.md" <<'EOF'
---
created: 2026-06-06
updated: 2026-06-06
status: active
tags:
  - preferences
source: conversation
confidence: 0.91
supersedes: []
superseded_by: []
---

User prefers concise responses and Russian language.
EOF

cat > "$VAULT/architecture/arch-1.md" <<'EOF'
---
created: 2026-06-06
updated: 2026-06-06
status: active
tags:
  - architecture
source: conversation
confidence: 0.88
supersedes: []
superseded_by: []
---

Knowledge retrieval should run before answering and keep the context compact.
EOF

cat > "$VAULT/incidents/inc-1.md" <<'EOF'
---
created: 2026-05-01
updated: 2026-05-01
status: active
tags:
  - incidents
source: conversation
confidence: 0.77
supersedes: []
superseded_by: []
---

A temporary notebook sync issue happened during testing.
EOF

python - <<PY
from pathlib import Path
from agent.prefetch_knowledge import SilverbulletKnowledgeProvider
from agent.knowledge_layer import KnowledgeEntry

vault = Path(r"$VAULT")
p = SilverbulletKnowledgeProvider(vault_root=str(vault), silverbullet_url="http://127.0.0.1:3002")
p._use_filesystem_vault = True

hits = p.search_knowledge('concise Russian responses', top_k=3, max_tokens=256)
assert hits, 'search should return at least one hit'
print('SEARCH_OK', len(hits))

entry = KnowledgeEntry(
    entry_id='kb-999',
    title='Reply style',
    content='User prefers short, factual replies.',
    category='preferences',
    tags=['preferences'],
    source='conversation',
    confidence=0.95,
    status='active',
    created='2026-06-06',
    updated='2026-06-06',
    supersedes=[],
    superseded_by=[],
)
p.save_knowledge_entry(entry)
created = list((vault/'preferences').glob('kb-999*.md'))
assert created, 'saved file missing'
print('SAVE_OK', created[0].name)

p.update_knowledge_entry('kb-999', {'content': 'User prefers very short, factual replies.', 'updated': '2026-06-07'})
text = created[0].read_text(encoding='utf-8')
assert 'very short, factual replies' in text
print('UPDATE_OK')

p.archive_knowledge_entry('kb-999')
text = created[0].read_text(encoding='utf-8')
assert 'status: archived' in text
print('ARCHIVE_OK')
PY

python "$PWD/scripts/run_weekly_audit.py" --vault-root "$VAULT" --stale-after-days "$AUDIT_DAYS" | tee "$ROOT/audit.txt"

grep -q 'weekly knowledge audit' "$ROOT/audit.txt"

echo "AUDIT_OK"
echo "$ROOT"
