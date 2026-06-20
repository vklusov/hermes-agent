#!/usr/bin/env bash
set -euo pipefail

TARGET="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

python - "$TARGET" <<'PY'
import pathlib
import re
import sys

root = pathlib.Path(sys.argv[1])
patterns = {
    'github_token': re.compile(r'gh[pousr]_[A-Za-z0-9_]{20,}'),
    'openai_like_key': re.compile(r'\bsk-[A-Za-z0-9][A-Za-z0-9_-]{20,}\b'),
    'google_key': re.compile(r'AIza[0-9A-Za-z_-]{20,}'),
    'private_key': re.compile(r'BEGIN (?:RSA|OPENSSH|EC|DSA)? ?PRIVATE KEY'),
    'real_neurogate_host': re.compile(r'(?:api\.)?neurogate\.space'),
    'local_ip': re.compile(r'\b(?:192\.168\.|100\.\d+\.)\d+\.\d+\b'),
    'local_user_path': re.compile('/home/' + 'wwolfy' + r'|' + '/Users/' + r'[^/]+'),
}
ignore_dirs = {'.git', '__pycache__'}
findings = []
for path in root.rglob('*'):
    if path.is_dir():
        continue
    if any(part in ignore_dirs for part in path.parts):
        continue
    rel = path.relative_to(root)
    if str(rel) == 'scripts/scan-secrets.sh':
        continue
    try:
        text = path.read_text(encoding='utf-8')
    except UnicodeDecodeError:
        continue
    for name, rx in patterns.items():
        for match in rx.finditer(text):
            line_no = text.count('\n', 0, match.start()) + 1
            line = text.splitlines()[line_no - 1]
            findings.append((name, str(path.relative_to(root)), line_no, line[:200]))

if findings:
    print('Potential secret/sensitive findings:')
    for item in findings:
        print(f'{item[0]} {item[1]}:{item[2]}: {item[3]}')
    sys.exit(1)
print('secret scan OK')
PY
