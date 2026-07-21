#!/usr/bin/env bash
# scripts/verify_stage20_server.sh
#
# Stage 20 Typography Pipeline — server verification (container-based)
#
# REQUIRES: docker, git, curl
# Host python3 is NOT required.  All Python work runs inside a Docker helper
# container built from worker/Dockerfile.verify (build context: worker/).
# Root .dockerignore excludes worker/, so worker/ is the correct build context.
#
# What it does:
#   1. Builds creative-stage20-verify:<sha> from worker/Dockerfile.verify
#   2. Starts a sleep-infinity helper container (Python module checks + pytest)
#   3. Starts an isolated Flask worker on loopback port 48092 (HTTP checks)
#   4. Runs: import-check, check-roles, check-templates, check-flags,
#            check-dedup, check-quality (all via helper container)
#   5. Runs pytest for test_stage20_typography.py  (100 tests expected)
#   6. Runs Stage 19 regression pytest
#   7. HTTP-checks /v1/typography/health and /v1/background/health
#   8. Generates verification report JSON + MD
#   9. Compares production container snapshot before/after
#  10. Cleans up all test containers and networks
#
# Usage:
#   bash scripts/verify_stage20_server.sh
#   bash scripts/verify_stage20_server.sh --skip-build   # reuse existing image
#
# Exit codes:
#   0  ALL PASS
#   1  FAIL
#
# Containers created (auto-removed on exit via trap):
#   creative-stage20-helper-<sha>-<pid>   helper (network=none)
#   creative-stage20-worker-<sha>-<pid>   isolated Flask worker (127.0.0.1:48092)
#
# Safety guarantees:
#   - Production containers (creative-nginx, creative-api, creative-worker) NEVER touched
#   - docker system prune / volume prune NEVER run
#   - docker compose down NEVER run
#   - Stage 18 / Stage 19 code NEVER modified
#   - /app/worker path NEVER used  (correct path: /app)
#   - Production localhost:5000 NEVER used as verification target

set -Eeuo pipefail

# ─── Paths ────────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
WORKER_DIR="${PROJECT_ROOT}/worker"

cd "${PROJECT_ROOT}"

# ─── Git SHA ──────────────────────────────────────────────────────────────────

if ! GIT_SHA="$(git -c "safe.directory=${PROJECT_ROOT}" rev-parse --short HEAD 2>/dev/null)"; then
    printf '[FAIL]  Cannot determine Git SHA in %s\n' "${PROJECT_ROOT}" >&2
    printf '[FAIL]  Ensure this is a git repository and git is accessible\n' >&2
    printf '[INFO]  If git ownership error:\n' >&2
    printf '        git config --global --add safe.directory %s\n' "${PROJECT_ROOT}" >&2
    exit 1
fi

[[ -z "${GIT_SHA}" ]] && { printf '[FAIL]  git rev-parse returned empty\n' >&2; exit 1; }

TIMESTAMP="$(date -u +%Y%m%dT%H%M%S 2>/dev/null || date +%Y%m%dT%H%M%S)"
SESSION_ID="${GIT_SHA}-$$"

STAGE20_IMAGE="creative-stage20-verify:${GIT_SHA}"
HELPER_CONTAINER="creative-stage20-helper-${SESSION_ID}"
WORKER_CONTAINER="creative-stage20-worker-${SESSION_ID}"

VERIFY_PORT=48092
VERIFY_WORKER_URL="http://127.0.0.1:${VERIFY_PORT}"
HTTP_TIMEOUT=30
WORKER_WAIT_RETRIES=40
WORKER_WAIT_INTERVAL=2

ARTIFACT_DIR="${PROJECT_ROOT}/verify-artifacts/stage20-${TIMESTAMP}"

# ─── State ────────────────────────────────────────────────────────────────────

HELPER_STARTED=false
WORKER_STARTED=false
FINAL_EXIT=0

# ─── Colors ───────────────────────────────────────────────────────────────────

RED='\033[0;31m'
GRN='\033[0;32m'
YLW='\033[1;33m'
BLU='\033[0;34m'
CYN='\033[0;36m'
NC='\033[0m'

ok()   { printf "${GRN}[OK]${NC}    %s\n"    "$*"; }
err()  { printf "${RED}[FAIL]${NC}  %s\n"    "$*" >&2; }
warn() { printf "${YLW}[WARN]${NC}  %s\n"    "$*"; }
info() { printf "${BLU}[INFO]${NC}  %s\n"    "$*"; }
sect() { printf "\n${CYN}== %s ==${NC}\n"    "$*"; }

# ─── Docker helpers ───────────────────────────────────────────────────────────
# All Python runs inside the helper container at /app (PYTHONPATH=/app).
# /scripts = PROJECT_ROOT/scripts  (read-only; verify_stage20_server.py lives here)
# /artifacts = ARTIFACT_DIR         (writable; all JSON + log artifacts go here)

run_python() {
    docker exec \
        -e PYTHONPATH=/app \
        -e PYTHONDONTWRITEBYTECODE=1 \
        "${HELPER_CONTAINER}" \
        python "$@"
}

run_python_module() {
    docker exec \
        -e PYTHONPATH=/app \
        -e PYTHONDONTWRITEBYTECODE=1 \
        "${HELPER_CONTAINER}" \
        python -m "$@"
}

# ─── Cleanup trap ─────────────────────────────────────────────────────────────
# Captures the true exit code even when set -e fires before FINAL_EXIT is set.

cleanup() {
    local _actual=$?
    local _preserve="${FINAL_EXIT:-0}"
    set +e

    echo ""
    info "Cleaning up test containers..."

    if [[ "${WORKER_STARTED}" == "true" ]]; then
        docker rm -f "${WORKER_CONTAINER}" >/dev/null 2>&1 \
            && info "Removed worker: ${WORKER_CONTAINER}" \
            || warn "Could not remove: ${WORKER_CONTAINER}"
    fi
    if [[ "${HELPER_STARTED}" == "true" ]]; then
        docker rm -f "${HELPER_CONTAINER}" >/dev/null 2>&1 \
            && info "Removed helper: ${HELPER_CONTAINER}" \
            || warn "Could not remove: ${HELPER_CONTAINER}"
    fi

    echo ""
    info "Artifacts: ${ARTIFACT_DIR}"

    if [[ "${_preserve}" -ne 0 ]]; then
        exit "${_preserve}"
    fi
    exit "${_actual}"
}
trap cleanup EXIT

# ─── Args ─────────────────────────────────────────────────────────────────────

SKIP_BUILD=false
for _arg in "$@"; do
    [[ "${_arg}" == "--skip-build" ]] && SKIP_BUILD=true
done

# ═══════════════════════════════════════════════════════════════════════════════
# Step 1: Prerequisites
# ═══════════════════════════════════════════════════════════════════════════════
sect "Step 1: Prerequisites"

mkdir -p "${ARTIFACT_DIR}"
info "Project root:     ${PROJECT_ROOT}"
info "Git SHA:          ${GIT_SHA}"
info "Timestamp:        ${TIMESTAMP}"
info "Verify image:     ${STAGE20_IMAGE}"
info "Helper:           ${HELPER_CONTAINER}"
info "Worker:           ${WORKER_CONTAINER} → ${VERIFY_WORKER_URL}"
info "Artifact dir:     ${ARTIFACT_DIR}"

_prereq_fail=false

if ! command -v docker >/dev/null 2>&1; then
    err "docker not found (required for all Python work)"
    _prereq_fail=true
fi

if ! docker info >/dev/null 2>&1; then
    err "Docker daemon not running"
    _prereq_fail=true
fi

if ! command -v curl >/dev/null 2>&1; then
    err "curl not found (required for HTTP health checks)"
    _prereq_fail=true
fi

if [[ "${_prereq_fail}" == "true" ]]; then
    FINAL_EXIT=1
    exit 1
fi

# Host python3 not required; note it informatively only
if command -v python3 >/dev/null 2>&1; then
    info "Host python3: $(python3 --version 2>&1 | head -1)  (INFO — not used by this script)"
else
    info "Host python3: not found  (expected — all Python runs inside Docker)"
fi

# Port availability check (warn only; Docker will error if truly occupied)
if ss -ln 2>/dev/null | grep -q ":${VERIFY_PORT} " || \
   netstat -ln 2>/dev/null | grep -q ":${VERIFY_PORT} "; then
    warn "Port ${VERIFY_PORT} appears to be in use — may cause Docker publish conflict"
fi

ok "Prerequisites satisfied (docker + curl + git SHA ${GIT_SHA})"

# ═══════════════════════════════════════════════════════════════════════════════
# Step 1.5: Disk space
# ═══════════════════════════════════════════════════════════════════════════════
sect "Step 1.5: Disk Space"

AVAIL_KB=$(df -k "${PROJECT_ROOT}" 2>/dev/null | awk 'NR==2{print $4}' || echo "0")
AVAIL_GB=$(( AVAIL_KB / 1024 / 1024 ))
info "Available: ~${AVAIL_GB} GB on ${PROJECT_ROOT}"
(( AVAIL_KB < 2097152 )) && warn "Low disk (~${AVAIL_GB} GB) — docker build may fail" || true
ok "Disk check done"

# ═══════════════════════════════════════════════════════════════════════════════
# Step 2: Production container snapshot (before)
# ═══════════════════════════════════════════════════════════════════════════════
sect "Step 2: Production Containers Snapshot (before)"

PROD_BEFORE="${ARTIFACT_DIR}/production-before.txt"
docker ps --filter "name=creative" \
    --format "{{.Names}}\t{{.Status}}\t{{.Image}}" \
    > "${PROD_BEFORE}" 2>/dev/null || true
PROD_COUNT_BEFORE=$(wc -l < "${PROD_BEFORE}" | tr -d ' ')
info "Production 'creative*' containers: ${PROD_COUNT_BEFORE}"
[[ "${PROD_COUNT_BEFORE}" -gt 0 ]] && cat "${PROD_BEFORE}" || true
ok "Snapshot (before) saved"

# ═══════════════════════════════════════════════════════════════════════════════
# Step 3: Build verify image
# ═══════════════════════════════════════════════════════════════════════════════
# Build context = WORKER_DIR (not project root).
# Root .dockerignore excludes worker/ entirely; all COPY paths are relative to worker/.
sect "Step 3: Build Verify Image (${STAGE20_IMAGE})"

BUILD_LOG="${ARTIFACT_DIR}/docker-build.log"

if [[ "${SKIP_BUILD}" == "true" ]] && docker image inspect "${STAGE20_IMAGE}" >/dev/null 2>&1; then
    info "--skip-build: reusing existing ${STAGE20_IMAGE}"
else
    # Pre-build file existence checks
    for _req in \
        "${WORKER_DIR}/requirements.txt" \
        "${WORKER_DIR}/requirements-stage19-verify.txt" \
        "${WORKER_DIR}/Dockerfile.verify" \
        "${WORKER_DIR}/app.py" \
        "${WORKER_DIR}/test_stage20_typography.py" \
        "${WORKER_DIR}/typography/__init__.py" \
        "${SCRIPT_DIR}/verify_stage20_server.py"
    do
        if [[ ! -f "${_req}" ]]; then
            err "Pre-build check FAILED: missing ${_req}"
            FINAL_EXIT=1
            exit 1
        fi
    done
    ok "Pre-build file checks passed"

    # Build context size guard
    CONTEXT_KB=$(du -sk "${WORKER_DIR}" 2>/dev/null | cut -f1 || echo "0")
    CONTEXT_MB=$(( CONTEXT_KB / 1024 ))
    info "Build context: ${WORKER_DIR} (~${CONTEXT_MB} MB)"
    if (( CONTEXT_KB > 256000 )); then
        err "Build context too large (${CONTEXT_MB} MB) — check worker/.dockerignore"
        FINAL_EXIT=1
        exit 1
    elif (( CONTEXT_KB > 51200 )); then
        warn "Build context is large (${CONTEXT_MB} MB) — consider worker/.dockerignore"
    fi

    info "Building ${STAGE20_IMAGE} from ${WORKER_DIR}/Dockerfile.verify ..."
    _build_exit=0
    docker build \
        -t "${STAGE20_IMAGE}" \
        --file "${WORKER_DIR}/Dockerfile.verify" \
        "${WORKER_DIR}" \
        2>&1 | tee "${BUILD_LOG}" || _build_exit=$?

    if [[ "${_build_exit}" -ne 0 ]]; then
        err "docker build FAILED (exit=${_build_exit}) — see ${BUILD_LOG}"
        FINAL_EXIT=1
        exit 1
    fi
fi
ok "Verify image ready: ${STAGE20_IMAGE}"

# ═══════════════════════════════════════════════════════════════════════════════
# Step 3.5: Start helper container
# ═══════════════════════════════════════════════════════════════════════════════
sect "Step 3.5: Start Helper Container"

_helper_exit=0
docker run -d \
    --name "${HELPER_CONTAINER}" \
    --network none \
    -v "${PROJECT_ROOT}/scripts:/scripts:ro" \
    -v "${ARTIFACT_DIR}:/artifacts" \
    -e PYTHONPATH=/app \
    -e PYTHONDONTWRITEBYTECODE=1 \
    "${STAGE20_IMAGE}" \
    sleep infinity \
    >/dev/null || _helper_exit=$?

if [[ "${_helper_exit}" -ne 0 ]]; then
    err "Failed to start helper container (exit=${_helper_exit})"
    FINAL_EXIT=1
    exit 1
fi
HELPER_STARTED=true

docker exec "${HELPER_CONTAINER}" echo "helper-alive" >/dev/null
ok "Helper container running: ${HELPER_CONTAINER}"

# ═══════════════════════════════════════════════════════════════════════════════
# Step 3.6: Import check inside helper
# ═══════════════════════════════════════════════════════════════════════════════
sect "Step 3.6: Typography Module Import Check"

IMPORT_LOG="${ARTIFACT_DIR}/helper-import.log"
_import_exit=0
run_python /scripts/verify_stage20_server.py \
    import-check \
    --artifact-dir /artifacts \
    2>&1 | tee "${IMPORT_LOG}" || _import_exit=$?

if [[ "${_import_exit}" -ne 0 ]]; then
    err "Import check FAILED (exit=${_import_exit}) — see ${IMPORT_LOG}"
    err "All 15 typography module symbols must be importable from /app with PYTHONPATH=/app"
    FINAL_EXIT=1
    exit 1
fi
ok "All Stage 20 typography symbols importable"

# ═══════════════════════════════════════════════════════════════════════════════
# Step 4: Start isolated worker container
# ═══════════════════════════════════════════════════════════════════════════════
# Port is published on 127.0.0.1 only (loopback).
# NEVER uses host's localhost:5000 (production port).
sect "Step 4: Start Isolated Worker Container (127.0.0.1:${VERIFY_PORT})"

_worker_exit=0
docker run -d \
    --name "${WORKER_CONTAINER}" \
    -p "127.0.0.1:${VERIFY_PORT}:5000" \
    -e TYPOGRAPHY_PIPELINE_ENABLED=false \
    -e BACKGROUND_PIPELINE_ENABLED=false \
    -e BACKGROUND_PIPELINE_COMPARE_ONLY=true \
    -e PSD_TEXT_RELAYOUT_ENABLED=false \
    -e CTA_RELAYOUT_ENABLED=false \
    -e FLATTENED_OCR_ENABLED=false \
    -e FLATTENED_OCR_COMPARE_ONLY=true \
    -e OUTPUT_DIR=/tmp/stage20-verify-outputs \
    "${STAGE20_IMAGE}" \
    python app.py \
    >/dev/null || _worker_exit=$?

if [[ "${_worker_exit}" -ne 0 ]]; then
    err "Failed to start isolated worker (exit=${_worker_exit})"
    FINAL_EXIT=1
    exit 1
fi
WORKER_STARTED=true
info "Worker: ${WORKER_CONTAINER} → ${VERIFY_WORKER_URL}"
ok "Isolated worker container started"

# ═══════════════════════════════════════════════════════════════════════════════
# Step 5: Worker health check
# ═══════════════════════════════════════════════════════════════════════════════
sect "Step 5: Worker Health Check"

_retries=0
while (( _retries < WORKER_WAIT_RETRIES )); do
    if curl -sf "${VERIFY_WORKER_URL}/health" --max-time 3 -o /dev/null 2>/dev/null; then
        break
    fi
    _retries=$(( _retries + 1 ))
    info "Waiting for worker... (${_retries}/${WORKER_WAIT_RETRIES})"
    sleep "${WORKER_WAIT_INTERVAL}"
done

if (( _retries >= WORKER_WAIT_RETRIES )); then
    err "Worker did not start in time (${WORKER_WAIT_RETRIES} retries × ${WORKER_WAIT_INTERVAL}s)"
    info "Worker container logs:"
    docker logs "${WORKER_CONTAINER}" 2>&1 | tail -40 | tee "${ARTIFACT_DIR}/worker.log" || true
    FINAL_EXIT=1
    exit 1
fi

# Save worker log for artifact
docker logs "${WORKER_CONTAINER}" 2>&1 > "${ARTIFACT_DIR}/worker.log" || true
ok "Worker is up at ${VERIFY_WORKER_URL}"

# ── Typography health ──────────────────────────────────────────────────────────
TYPO_HEALTH="${ARTIFACT_DIR}/typography-health.json"
_typo_status=$(curl -s -w "%{http_code}" \
    -o "${TYPO_HEALTH}" \
    "${VERIFY_WORKER_URL}/v1/typography/health" \
    --max-time "${HTTP_TIMEOUT}" 2>/dev/null || echo "000")

if [[ "${_typo_status}" == "200" ]]; then
    ok "GET /v1/typography/health → HTTP 200"
    info "Typography health response:"
    cat "${TYPO_HEALTH}" && echo ""
    # Verify typographyPipelineEnabled field is false (no host python3 needed)
    if grep -q '"typographyPipelineEnabled": false' "${TYPO_HEALTH}" 2>/dev/null; then
        ok "typographyPipelineEnabled=false (default confirmed)"
    elif grep -q '"typographyPipelineEnabled": true' "${TYPO_HEALTH}" 2>/dev/null; then
        warn "typographyPipelineEnabled=true (expected false — flag may have been set)"
    else
        warn "typographyPipelineEnabled field not found in response"
    fi
else
    err "GET /v1/typography/health → HTTP ${_typo_status} (not 200)"
    info "Worker container logs (last 30 lines):"
    docker logs "${WORKER_CONTAINER}" 2>&1 | tail -30 || true
    echo '{"status":"error"}' > "${TYPO_HEALTH}"
    FINAL_EXIT=1
fi

# ── Background health (Stage 19 regression) ───────────────────────────────────
BG_HEALTH="${ARTIFACT_DIR}/background-health.json"
_bg_status=$(curl -s -w "%{http_code}" \
    -o "${BG_HEALTH}" \
    "${VERIFY_WORKER_URL}/v1/background/health" \
    --max-time "${HTTP_TIMEOUT}" 2>/dev/null || echo "000")

if [[ "${_bg_status}" == "200" ]]; then
    ok "GET /v1/background/health → HTTP 200 (Stage 19 not broken)"
else
    err "GET /v1/background/health → HTTP ${_bg_status} — Stage 19 regression detected"
    echo '{"status":"error"}' > "${BG_HEALTH}"
    FINAL_EXIT=1
fi

# ═══════════════════════════════════════════════════════════════════════════════
# Step 6: Module checks (roles, templates, flags, dedup, quality gate)
# ═══════════════════════════════════════════════════════════════════════════════
sect "Step 6: Module Checks"

for _cmd in check-roles check-templates check-flags check-dedup check-quality; do
    _log="${ARTIFACT_DIR}/module-${_cmd}.log"
    _exit=0
    run_python /scripts/verify_stage20_server.py \
        "${_cmd}" \
        --artifact-dir /artifacts \
        2>&1 | tee "${_log}" || _exit=$?

    if [[ "${_exit}" -ne 0 ]]; then
        err "Module check '${_cmd}' FAILED (exit=${_exit}) — see ${_log}"
        FINAL_EXIT=1
    else
        ok "Module check '${_cmd}' PASSED"
    fi
done

# ═══════════════════════════════════════════════════════════════════════════════
# Step 7: pytest — Stage 20.1 Typography unit tests (≥123 expected)
# ═══════════════════════════════════════════════════════════════════════════════
# Exit-code policy: redirect output to file, then capture $? immediately.
# This avoids the | tee pipe masking pytest's exit code regardless of pipefail.
sect "Step 7: pytest — Stage 20.1 Typography Unit Tests"

PYTEST20_LOG="${ARTIFACT_DIR}/stage20-pytest.log"
PYTEST20_EXIT=0

docker exec \
    -e PYTHONPATH=/app \
    -e PYTHONDONTWRITEBYTECODE=1 \
    "${HELPER_CONTAINER}" \
    python -m pytest /app/test_stage20_typography.py \
        -q -p no:cacheprovider --tb=short \
    > "${PYTEST20_LOG}" 2>&1 || PYTEST20_EXIT=$?

cat "${PYTEST20_LOG}" | tail -20

if [[ "${PYTEST20_EXIT}" -ne 0 ]]; then
    err "pytest Stage 20.1 FAILED (exit=${PYTEST20_EXIT}) — see ${PYTEST20_LOG}"
    FINAL_EXIT=1
else
    _pt20=$(grep -oP '\d+(?= passed)' "${PYTEST20_LOG}" | tail -1 || echo "?")
    ok "pytest Stage 20.1: ${_pt20} passed"
    if (( ${_pt20:-0} < 100 )) 2>/dev/null; then
        warn "Expected ≥100 tests; got ${_pt20:-?}"
    fi
fi

# ═══════════════════════════════════════════════════════════════════════════════
# Step 7.5: pytest — Stage 20.2 Source Faithful Repair unit tests (≥57 expected)
# ═══════════════════════════════════════════════════════════════════════════════
# Packages live at /app/background/ and /app/typography/ (PYTHONPATH=/app).
# test_stage20_2.py uses sys.path.insert(0, dirname(__file__)) — no worker. prefix.
sect "Step 7.5: pytest — Stage 20.2 Source Faithful Repair Unit Tests"

PYTEST20_2_LOG="${ARTIFACT_DIR}/stage20-2-pytest.log"
STAGE20_2_TEST_EXIT=0

_s20_2_exists=0
docker exec "${HELPER_CONTAINER}" test -f /app/test_stage20_2.py >/dev/null 2>&1 || _s20_2_exists=$?

if [[ "${_s20_2_exists}" -ne 0 ]]; then
    err "test_stage20_2.py not found in image — cannot run Stage 20.2 tests"
    echo "missing" > "${PYTEST20_2_LOG}"
    STAGE20_2_TEST_EXIT=1
    FINAL_EXIT=1
else
    # Import smoke — must succeed before running full test suite
    _import_smoke=0
    docker exec \
        -e PYTHONPATH=/app \
        "${HELPER_CONTAINER}" \
        python -c "
import background
import background.smart_fit_guard
import background.mode_selector
import background.prompt_builder
import background.source_faithful_repair
import typography
import typography.text_extractor
import typography.role_resolver
print('Stage20.2 import smoke: OK')
" > "${ARTIFACT_DIR}/stage20-2-import-smoke.log" 2>&1 || _import_smoke=$?

    if [[ "${_import_smoke}" -ne 0 ]]; then
        err "Stage 20.2 import smoke FAILED — see ${ARTIFACT_DIR}/stage20-2-import-smoke.log"
        cat "${ARTIFACT_DIR}/stage20-2-import-smoke.log" >&2
        STAGE20_2_TEST_EXIT=1
        FINAL_EXIT=1
    else
        ok "Stage 20.2 import smoke: OK"
    fi

    # Run the actual tests — redirect to file to preserve exit code
    docker exec \
        -e PYTHONPATH=/app \
        -e PYTHONDONTWRITEBYTECODE=1 \
        "${HELPER_CONTAINER}" \
        python -m pytest /app/test_stage20_2.py \
            -q -p no:cacheprovider --tb=short \
        > "${PYTEST20_2_LOG}" 2>&1 || STAGE20_2_TEST_EXIT=$?

    cat "${PYTEST20_2_LOG}" | tail -20

    _pt20_2=$(grep -oP '\d+(?= passed)' "${PYTEST20_2_LOG}" | tail -1 || echo "?")
    _ft20_2=$(grep -oP '\d+(?= failed)' "${PYTEST20_2_LOG}" | tail -1 || echo "0")

    if [[ "${STAGE20_2_TEST_EXIT}" -ne 0 ]]; then
        err "pytest Stage 20.2 FAILED (exit=${STAGE20_2_TEST_EXIT}, passed=${_pt20_2:-?}, failed=${_ft20_2:-?}) — see ${PYTEST20_2_LOG}"
        FINAL_EXIT=1
    else
        ok "pytest Stage 20.2: ${_pt20_2:-?} passed"
        if (( ${_pt20_2:-0} < 57 )) 2>/dev/null; then
            warn "Expected ≥57 tests; got ${_pt20_2:-?}"
        fi
    fi
fi

# ─── Stage 20.2 Module checks ─────────────────────────────────────────────────
for _cmd in check-smart-fit-guard check-mode-selector check-source-faithful-repair check-sfr-masks check-prompt-builder; do
    _log="${ARTIFACT_DIR}/module-${_cmd}.log"
    _exit=0
    docker exec \
        -e PYTHONPATH=/app \
        -e PYTHONDONTWRITEBYTECODE=1 \
        "${HELPER_CONTAINER}" \
        python /scripts/verify_stage20_server.py \
            "${_cmd}" \
            --artifact-dir /artifacts \
        > "${_log}" 2>&1 || _exit=$?

    if [[ "${_exit}" -ne 0 ]]; then
        err "Stage 20.2 module check '${_cmd}' FAILED (exit=${_exit}) — see ${_log}"
        FINAL_EXIT=1
    else
        ok "Stage 20.2 module check '${_cmd}' PASSED"
    fi
done

# ═══════════════════════════════════════════════════════════════════════════════
# Step 8: Stage 19 regression tests
# ═══════════════════════════════════════════════════════════════════════════════
sect "Step 8: Stage 19 Regression Tests"

PYTEST19_LOG="${ARTIFACT_DIR}/stage19-pytest.log"
PYTEST19_EXIT=0

_s19_exists=0
docker exec "${HELPER_CONTAINER}" test -f /app/test_stage19.py >/dev/null 2>&1 || _s19_exists=$?

if [[ "${_s19_exists}" -ne 0 ]]; then
    warn "test_stage19.py not found in image — skipping Stage 19 regression"
    echo "skipped" > "${PYTEST19_LOG}"
else
    docker exec \
        -e PYTHONPATH=/app \
        -e PYTHONDONTWRITEBYTECODE=1 \
        "${HELPER_CONTAINER}" \
        python -m pytest /app/test_stage19.py \
            -q -p no:cacheprovider --tb=short \
        > "${PYTEST19_LOG}" 2>&1 || PYTEST19_EXIT=$?

    cat "${PYTEST19_LOG}" | tail -10

    if [[ "${PYTEST19_EXIT}" -ne 0 ]]; then
        err "Stage 19 regression FAILED (exit=${PYTEST19_EXIT}) — Stage 20 broke Stage 19?"
        FINAL_EXIT=1
    else
        _pt19=$(grep -oP '\d+(?= passed)' "${PYTEST19_LOG}" | tail -1 || echo "?")
        ok "Stage 19 regression: ${_pt19} passed (Stage 19 unaffected)"
    fi
fi

# ═══════════════════════════════════════════════════════════════════════════════
# Step 9: Generate verification report
# ═══════════════════════════════════════════════════════════════════════════════
sect "Step 9: Generate Verification Report"

REPORT_FILE="${ARTIFACT_DIR}/stage20-verification-report.json"
_report_exit=0
run_python /scripts/verify_stage20_server.py \
    report \
    --artifact-dir /artifacts \
    --git-sha "${GIT_SHA}" \
    --timestamp "${TIMESTAMP}" \
    --output "/artifacts/stage20-verification-report.json" \
    2>&1 | tee "${ARTIFACT_DIR}/report-gen.log" || _report_exit=$?

if [[ -f "${REPORT_FILE}" ]]; then
    # Read verdict without requiring host python3
    _verdict="UNKNOWN"
    grep -q '"verdict": "PASS"' "${REPORT_FILE}" 2>/dev/null && _verdict="PASS" || true
    grep -q '"verdict": "FAIL"' "${REPORT_FILE}" 2>/dev/null && _verdict="FAIL" || true
    info "Report verdict: ${_verdict}"
    info "Report file:    ${REPORT_FILE}"
    [[ "${_verdict}" == "FAIL" ]] && FINAL_EXIT=1 || true
else
    warn "Report JSON not generated (report-gen exit=${_report_exit})"
fi

# ── Markdown summary ───────────────────────────────────────────────────────────
MD_FILE="${ARTIFACT_DIR}/stage20-verification-report.md"
{
    echo "# Stage 20 Verification Report"
    echo ""
    echo "- **gitSha**: ${GIT_SHA}"
    echo "- **timestamp**: ${TIMESTAMP}"
    echo "- **verifyImage**: ${STAGE20_IMAGE}"
    echo "- **containerBased**: true"
    echo "- **hostPythonRequired**: false"
    echo "- **verifyWorkerUrl**: ${VERIFY_WORKER_URL}"
    echo ""
    echo "## Results"
    echo ""
    echo "| Check | Status |"
    echo "|---|---|"
    for _chk in import-check module-check-roles module-check-templates \
                module-check-flags module-check-dedup module-check-quality; do
        _log="${ARTIFACT_DIR}/${_chk}.log"
        if [[ -f "${_log}" ]] && ! grep -q "\[NG\]" "${_log}" 2>/dev/null; then
            echo "| ${_chk} | PASS |"
        elif [[ -f "${_log}" ]]; then
            echo "| ${_chk} | FAIL |"
        else
            echo "| ${_chk} | N/A |"
        fi
    done
    _pt20_val=$(grep -oP '\d+(?= passed)' "${PYTEST20_LOG}" 2>/dev/null | tail -1 || echo "?")
    echo "| pytest Stage 20 | ${_pt20_val} passed |"
    echo "| /v1/typography/health | HTTP ${_typo_status} |"
    echo "| /v1/background/health | HTTP ${_bg_status} |"
    echo ""
    echo "## Production Impact"
    echo ""
    echo "Production containers were **not touched**. All tests ran in isolated containers."
} > "${MD_FILE}" 2>/dev/null || true
info "Markdown report: ${MD_FILE}"

# ═══════════════════════════════════════════════════════════════════════════════
# Step 10: Production container snapshot (after)
# ═══════════════════════════════════════════════════════════════════════════════
sect "Step 10: Production Containers Snapshot (after)"

PROD_AFTER="${ARTIFACT_DIR}/production-after.txt"
docker ps --filter "name=creative" \
    --format "{{.Names}}\t{{.Status}}\t{{.Image}}" \
    | grep -v "${HELPER_CONTAINER}" \
    | grep -v "${WORKER_CONTAINER}" \
    > "${PROD_AFTER}" 2>/dev/null || true

PROD_COUNT_AFTER=$(wc -l < "${PROD_AFTER}" | tr -d ' ')
if [[ "${PROD_COUNT_AFTER}" -eq "${PROD_COUNT_BEFORE}" ]]; then
    ok "Production containers unchanged (${PROD_COUNT_BEFORE} containers)"
else
    warn "Production container count changed: ${PROD_COUNT_BEFORE} → ${PROD_COUNT_AFTER}"
fi

_exited=$(grep -iE "Exited|Restarting" "${PROD_AFTER}" 2>/dev/null || true)
[[ -n "${_exited}" ]] && warn "Some production containers may be unhealthy:" && echo "${_exited}" || true

# ═══════════════════════════════════════════════════════════════════════════════
# Step 11: Final Verdict
# ═══════════════════════════════════════════════════════════════════════════════
sect "Step 11: Final Verdict"

echo ""
echo "  Git SHA:          ${GIT_SHA}"
echo "  Verify image:     ${STAGE20_IMAGE}"
echo "  Worker URL:       ${VERIFY_WORKER_URL}"
echo "  Artifact dir:     ${ARTIFACT_DIR}"
[[ -f "${REPORT_FILE}" ]] && echo "  Report:           ${REPORT_FILE}" || true
echo "  containerBased:   true"
echo "  hostPythonReq:    false"
echo ""

# ── Summary table ──────────────────────────────────────────────────────────────
_pt20_val=$(grep -oP '\d+(?= passed)' "${PYTEST20_LOG}" 2>/dev/null | tail -1 || echo "?")
_pt20_2_val=$(grep -oP '\d+(?= passed)' "${PYTEST20_2_LOG}" 2>/dev/null | tail -1 || echo "?")
_pt19_val=$(grep -oP '\d+(?= passed)' "${PYTEST19_LOG}" 2>/dev/null | tail -1 || echo "?")

printf "  %-30s : %s\n" "Git SHA" "${GIT_SHA}"
printf "  %-30s : %s\n" "Stage 20.1 Typography tests" "${_pt20_val} passed  (exit=${PYTEST20_EXIT})"
printf "  %-30s : %s\n" "Stage 20.2 SFR tests" "${_pt20_2_val} passed  (exit=${STAGE20_2_TEST_EXIT})"
printf "  %-30s : %s\n" "Stage 19 regression" "${_pt19_val} passed  (exit=${PYTEST19_EXIT})"
printf "  %-30s : %s\n" "FINAL_EXIT_CODE" "${FINAL_EXIT}"
echo ""

if [[ "${FINAL_EXIT}" -eq 0 ]]; then
    printf "${GRN}[PASS] Stage 20 verification PASSED${NC}\n"
    printf "       Canonical package root: /app  (background/, typography/)\n"
    printf "       gitSha=%s  containerBased=true  hostPythonRequired=false\n" "${GIT_SHA}"
    printf "\n"
    printf "Next step — production deployment:\n"
    printf "  cd /opt/creative-resizer\n"
    printf "  git pull --ff-only origin master\n"
    printf "  bash scripts/verify_stage20_server.sh\n"
else
    printf "${RED}[FAIL] Stage 20 verification FAILED (FINAL_EXIT=${FINAL_EXIT})${NC}\n"
    printf "       gitSha=%s\n" "${GIT_SHA}"
    printf "       Details: %s\n" "${ARTIFACT_DIR}"
    printf "\n"
    printf "Troubleshooting:\n"
    printf "  cat %s/stage20-2-import-smoke.log\n" "${ARTIFACT_DIR}"
    printf "  cat %s/stage20-pytest.log\n" "${ARTIFACT_DIR}"
    printf "  cat %s/stage20-2-pytest.log\n" "${ARTIFACT_DIR}"
    printf "  cat %s/module-check-smart-fit-guard.log\n" "${ARTIFACT_DIR}"
    printf "  cat %s/helper-import.log\n" "${ARTIFACT_DIR}"
    printf "  cat %s/worker.log\n" "${ARTIFACT_DIR}"
fi

echo ""
exit "${FINAL_EXIT}"
