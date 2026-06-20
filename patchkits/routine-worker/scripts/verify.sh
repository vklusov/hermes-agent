#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${1:-$(pwd)}"

if [ ! -d "$REPO_DIR/.git" ]; then
  echo "error: $REPO_DIR is not a git repository" >&2
  echo "usage: $0 /path/to/hermes-agent" >&2
  exit 2
fi

cd "$REPO_DIR"

echo "==> Checking routine_worker files"
test -f tools/routine_worker.py
test -f tests/tools/test_routine_worker.py
test -f skills/autonomous-ai-agents/routine-worker/SKILL.md

echo "==> Running targeted tests"
python -m pytest \
  tests/tools/test_routine_worker.py \
  tests/tools/test_delegate.py::TestDispatchDelegateTask::test_provider_model_overrides_forwarded \
  -q -o 'addopts='

echo "==> Verifying toolset registration"
python - <<'PY'
from toolsets import resolve_toolset
assert 'routine_worker' in resolve_toolset('delegation')
assert 'routine_worker' in resolve_toolset('hermes-cli')
print('routine_worker registered in delegation and hermes-cli toolsets')
PY

echo "==> Verification OK"
