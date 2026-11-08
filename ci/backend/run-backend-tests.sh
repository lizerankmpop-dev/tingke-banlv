#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CI_HOME="${TINGKE_CI_HOME:-/opt/tingke-ci}"
LOCK_REL="ci/backend/requirements-linux-py311.lock"
DEV_REQ_REL="backend/requirements.txt"
DEV_SHA_FILE="ci/backend/backend-requirements.sha256"
ENV_FILE="${TINGKE_RESOURCE_ENV:-/tmp/tingke-test-resources.env}"
ARTIFACT_DIR="${ARTIFACT_DIR:-$ROOT_DIR/ci-reports}"
EXPECTED_REVISION="ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66"

cd "$ROOT_DIR"

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

log() {
    echo "[INFO] $*"
}

[ -f "$CI_HOME/requirements.lock" ] || fail "image lock not found: $CI_HOME/requirements.lock"
[ -f "$LOCK_REL" ] || fail "repository lock not found: $LOCK_REL"

IMAGE_LOCK_SHA="$(sha256sum "$CI_HOME/requirements.lock" | awk '{print $1}')"
REPO_LOCK_SHA="$(sha256sum "$LOCK_REL" | awk '{print $1}')"
[ "$IMAGE_LOCK_SHA" = "$REPO_LOCK_SHA" ] || \
    fail "image lock ($IMAGE_LOCK_SHA) does not match repository lock ($REPO_LOCK_SHA); rebuild the py311-v3 image"
log "lock snapshot matches: $REPO_LOCK_SHA"

[ -f "$DEV_SHA_FILE" ] || fail "development requirements baseline missing: $DEV_SHA_FILE"
EXPECTED_DEV_SHA="$(awk 'NR==1 {print $1}' "$DEV_SHA_FILE")"
ACTUAL_DEV_SHA="$(tr -d '\r' < "$DEV_REQ_REL" | sha256sum | awk '{print $1}')"
[ "$EXPECTED_DEV_SHA" = "$ACTUAL_DEV_SHA" ] || \
    fail "backend/requirements.txt changed (expected $EXPECTED_DEV_SHA, got $ACTUAL_DEV_SHA); rebuild the environment image before running tests"
log "development requirements baseline matches: $ACTUAL_DEV_SHA"

[ -f "$CI_HOME/model-revision.txt" ] || fail "image model revision file missing"
IMAGE_REVISION="$(cat "$CI_HOME/model-revision.txt")"
[ "$IMAGE_REVISION" = "$EXPECTED_REVISION" ] || \
    fail "image model revision ($IMAGE_REVISION) does not match expected ($EXPECTED_REVISION)"
log "model revision matches: $IMAGE_REVISION"

"$CI_HOME/verify-environment.sh" >/dev/null
log "image environment verified"

mkdir -p "$ARTIFACT_DIR"
rm -f "$ENV_FILE"

python ci/backend/prepare_test_resources.py \
    --local-model "$CI_HOME/model" \
    --expected-revision "$EXPECTED_REVISION" \
    --emit-env > "$ENV_FILE"

test -s "$ENV_FILE" || fail "resource env file is empty: $ENV_FILE"
cat "$ENV_FILE"

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

export HF_HUB_OFFLINE=1

log "recording environment before pytest"
python -m pip freeze > "$ARTIFACT_DIR/backend-dependencies.txt"
cp "$LOCK_REL" "$ARTIFACT_DIR/requirements-linux-py311.lock"
cp "$CI_HOME/environment.txt" "$ARTIFACT_DIR/environment.txt"
cp "$CI_HOME/model.sha256" "$ARTIFACT_DIR/model.sha256"
cp "$CI_HOME/model-revision.txt" "$ARTIFACT_DIR/model-revision.txt"
cp "$CI_HOME/pip-check.txt" "$ARTIFACT_DIR/pip-check.txt"

log "running syntax check"
python -m compileall -q backend

log "running full backend pytest"
python -m pytest backend/tests -q -rs --junitxml="$ARTIFACT_DIR/backend-tests.xml"

log "artifacts archived in: $ARTIFACT_DIR"
