# Applying the Routine Worker Patchkit

## Recommended path: `git am`

`git am` preserves commit messages and authorship.

```bash
cd /path/to/hermes-agent
git status --short
# optional but recommended
git switch -c feature/routine-worker-routing-local

git am --3way /path/to/hermes-routine-worker-patchkit/patches/*.patch
```

If conflicts occur:

```bash
git status
# resolve conflicts
git add <resolved files>
git am --continue
```

Abort an in-progress apply:

```bash
git am --abort
```

## Alternative: `git apply`

Use this if you do not care about preserving commits.

```bash
cd /path/to/hermes-agent
git apply --3way /path/to/hermes-routine-worker-patchkit/patches/*.patch
git add agent tools tests toolsets.py run_agent.py skills
git commit -m "feat: add routine worker routing"
```

## Configure providers

Pick one config snippet from `config/` and adapt it into your `~/.hermes/config.yaml`:

- `routine-worker.generic.yaml`
- `routine-worker.neurogate.yaml`
- `routine-worker.builtin.yaml`

Use environment variables for secrets. Do not commit real `api_key` values.

## Verify

```bash
/path/to/hermes-routine-worker-patchkit/scripts/verify.sh /path/to/hermes-agent
```

Expected targeted result:

```text
9 passed
```

Warnings from optional dependencies such as `discord.player` may appear and are not specific to this patchkit.

## Restart

Gateway:

```bash
hermes gateway restart
```

CLI/chat sessions:

```text
/reset
```

or start a new session.

## Rollback

If applied via `git am` onto a dedicated branch, easiest rollback is deleting the branch.

If you need to revert commits:

```bash
git log --oneline
# identify the applied routine-worker commits
git revert <last_commit>^..<first_commit>
```

Or reset only if you know the branch is disposable:

```bash
git reset --hard <previous-good-commit>
```
