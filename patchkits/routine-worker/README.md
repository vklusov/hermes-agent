# Hermes Routine Worker Patchkit

Portable patch bundle for adding the native `routine_worker` tool to Hermes Agent.

## What this patchkit adds

- Native `routine_worker` tool for dispatching routine work to source-specific workers.
- Presets for marketplace research, web research, cheap flash checks, KB triage, and single-worker tasks.
- Per-task provider/model routing via `config.yaml` section `routine_worker`.
- Forwarding of `provider`, `model`, `base_url`, `api_key`, and `api_mode` overrides into delegated child agents.
- In-repo `routine-worker` skill and config examples.

## Intended use

Use this when you want the main Hermes agent to stay as orchestrator while broad routine work runs in cheaper/smaller worker models.

Common examples:

- marketplace/product research across multiple sources;
- routine web source collection;
- cheap exploratory checks;
- local KB/source-of-truth triage.

## Contents

```text
patches/   git-format-patch files, apply with git am
config/    config.yaml snippets: generic, Neurogate-style, built-in providers
scripts/   helper scripts: apply, verify, secret scan
APPLY.md   detailed apply / rollback instructions
MANIFEST.md exact patch list and changed files
```

## Requirements

- A git checkout of Hermes Agent.
- Base close to the upstream commit used by the patchkit. The patchkit was generated from branch `feature/routine-worker-routing` on top of `origin/main`.
- Python test dependencies installed if you want to run verification tests.

## Quick start

### If you are reading this inside the branch

Copy this directory or run the scripts directly from it:

```bash
cd patchkits/routine-worker
./scripts/apply.sh /path/to/hermes-agent
./scripts/verify.sh /path/to/hermes-agent
```

### If you received the tar.gz archive

From the root of any workspace:

```bash
tar -xzf hermes-routine-worker-patchkit.tar.gz
cd hermes-routine-worker-patchkit
./scripts/apply.sh /path/to/hermes-agent
./scripts/verify.sh /path/to/hermes-agent
```

Then add a `routine_worker` section to your `~/.hermes/config.yaml`. See `config/` examples.

Restart Hermes / gateway after applying code or config changes:

```bash
hermes gateway restart
```

For CLI sessions, start a new session or run `/reset` after tools/config changes.

## Safety notes

- No real API keys are included.
- Config examples use `api_key_env` and placeholders.
- The native tool does not perform external writes or purchases; it wraps delegation for worker research/triage.
- Workers should still be constrained by the parent task context.

## Upstream branch

Published fork branch, if you prefer Git over patch files:

```text
https://github.com/vklusov/hermes-agent/tree/feature/routine-worker-routing
```
