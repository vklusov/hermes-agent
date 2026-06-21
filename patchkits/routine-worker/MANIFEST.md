# Routine Worker Patchkit Manifest

Generated from Hermes Agent branch:

```text
feature/routine-worker-routing
```

Base:

```text
origin/main @ 1a0ef1311c8e65b50ccaad46754c97a089122d81
```

## Patch list

```text
0001 feat: add native routine worker tool
0002 feat: add per-task model routing to routine worker tool
0003 fix: forward routine worker model overrides through agent dispatch
0004 docs: add routine worker skill
0005 docs: add routine worker config examples
0006 docs: avoid key-like config placeholder
0007 feat: autoroute web research to routine workers
```

## Files changed by patch application

```text
agent/agent_runtime_helpers.py
agent/tool_executor.py
run_agent.py
skills/autonomous-ai-agents/routine-worker/README.md
skills/autonomous-ai-agents/routine-worker/SKILL.md
tests/tools/test_delegate.py
tests/tools/test_routine_worker.py
tools/delegate_tool.py
tools/routine_worker.py
toolsets.py
```

## Verification commands

```bash
python -m pytest \
  tests/tools/test_routine_worker.py \
  tests/tools/test_delegate.py::TestDispatchDelegateTask::test_provider_model_overrides_forwarded \
  -q -o 'addopts='
```

## Security scan

The patchkit includes `scripts/scan-secrets.sh`. It scans the patchkit for key-like strings, private-key headers, real Neurogate hostnames, and local private paths/IPs.
