#!/bin/sh
set -eu

ROOT="${TINGKE_CI_ROOT:-/opt/tingke-ci}"
EXPECTED_REVISION="ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66"
EXPECTED_LOCK_SHA="c7fa61338a14f0a437e355b6b297e62a948e8eea0d993ad25deeece7f60835d3"

test "$(python -c 'import sys; print("%d.%d" % sys.version_info[:2])')" = "3.11"
test -f "$ROOT/requirements.lock"
test "$(sha256sum "$ROOT/requirements.lock" | cut -d' ' -f1)" = "$EXPECTED_LOCK_SHA"

python - "$ROOT/requirements.lock" <<'PY'
import importlib.metadata as metadata
import re
import sys
from pathlib import Path


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


lock = Path(sys.argv[1])
installed: dict[str, set[str]] = {}
for dist in metadata.distributions():
    name = dist.metadata.get("Name")
    if name:
        installed.setdefault(canonical(name), set()).add(dist.version)

problems: list[str] = []
checked = 0
for lineno, raw in enumerate(lock.read_text(encoding="utf-8").splitlines(), 1):
    line = raw.strip()
    if not line or line.startswith("#"):
        continue
    if "==" not in line:
        problems.append(f"line {lineno}: cannot parse {raw!r}")
        continue
    name, version = (part.strip() for part in line.split("==", 1))
    if not name or not version:
        problems.append(f"line {lineno}: empty name or version in {raw!r}")
        continue
    versions = installed.get(canonical(name))
    if not versions:
        problems.append(f"{name}: not installed")
        continue
    if version not in versions:
        problems.append(f"{name}: expected {version}, found {', '.join(sorted(versions))}")
        continue
    if len(versions) > 1:
        problems.append(f"{name}: multiple installed versions {', '.join(sorted(versions))}")
        continue
    checked += 1

if problems:
    print("DEPENDENCY MISMATCH", file=sys.stderr)
    for problem in problems:
        print(f"  {problem}", file=sys.stderr)
    sys.exit(1)

print(f"DEPENDENCIES OK ({checked} packages)")
PY

test -f "$ROOT/model.sha256"
test -f "$ROOT/model-revision.txt"
test "$(cat "$ROOT/model-revision.txt")" = "$EXPECTED_REVISION"
test -d "$ROOT/model"
test -f "$ROOT/pip-check.txt"
test -f "$ROOT/environment.txt"
(cd "$ROOT/model" && sha256sum -c "$ROOT/model.sha256" >/dev/null)
python -m pip check >/dev/null
echo "environment-ok python=$(python --version 2>&1) revision=$EXPECTED_REVISION lock=$EXPECTED_LOCK_SHA"
