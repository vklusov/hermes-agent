#!/usr/bin/env bash
set -euo pipefail

PATCHKIT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_DIR="${1:-$(pwd)}"

if [ ! -d "$REPO_DIR/.git" ]; then
  echo "error: $REPO_DIR is not a git repository" >&2
  echo "usage: $0 /path/to/hermes-agent" >&2
  exit 2
fi

cd "$REPO_DIR"

echo "==> Repository: $REPO_DIR"
git status --short

if [ -n "$(git status --porcelain)" ]; then
  echo "error: working tree is not clean; commit/stash changes before applying" >&2
  exit 1
fi

echo "==> Applying patches from $PATCHKIT_DIR/patches"
git am --3way "$PATCHKIT_DIR"/patches/*.patch

echo "==> Applied routine-worker patchkit"
git log --oneline -6
