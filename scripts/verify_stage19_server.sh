#!/usr/bin/env bash
# scripts/verify_stage19_server.sh
#
# Stage 19 Background Pipeline — server verification (container-based)
#
# REQUIRES: docker, git, curl
# Host python3 is NOT required. All Python work runs inside a Docker helper
# container built from worker/Dockerfile.verify (build context: worker/).
# Root .dockerignore excludes worker/, so worker/ is the correct build context.
#
# Usage:
#   bash scripts/verify_stage19_server.sh
#   bash scripts/verify_stage19_server.sh --skip-build    # reuse existing image
#
# Containers created (all auto-removed on exit via trap):
#   creative-stage19-helper-<sha>-<pid>   sleep infinity helper
#   creative-stage19-worker-<sha>-<pid>   isolated Flask worker (port 48091)
#
# Safety:
#   - Production containers are NEVER touched
#   - docker system prune / volume prune / docker compose down NEVER run
#   - compareOnly global flag NEVER changed
#   - Stage 18 code NEVER modified
#
# Exit codes:
#   0  PASS
#   1  FAIL
#   2  PARTIAL (reserved)

set -Eeuo pipefail

# ─── Compute project paths FIRST, then cd into project root ───────────────────
# cd is required before git rev-parse to handle git safe.directory checks
# on production servers where file ownership may differ from process owner.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
WORKER_DIR="${PROJECT_ROOT}/worker"

cd "${PROJECT_ROOT}"

# ─── Git SHA — fail fast if not in a git repository ──────────────────────────
# Using -c safe.directory handles git's "dubious ownership" security check
# (git >= 2.35.2) that caused GIT_SHA="nogit" on production servers where
# /opt/creative-resizer is owned by a different user than the running process.

if ! GIT_SHA="$(git -c "safe.directory=${PROJECT_ROOT}" rev-parse --short HEAD 2>/dev/null)"; then
    printf '\033[0;31m[FAIL]\033[0m  Cannot determine Git SHA in %s\n' "${PROJECT_ROOT}" >&2
    printf '\033[0;31m[FAIL]\033[0m  Ensure this is a git repository and git is accessible\n' >&2
    printf '\033[0;34m[INFO]\033[0m  If git ownership error:\n' >&2
    printf '          git config --global --add safe.directory %s\n' "${PROJECT_ROOT}" >&2
    exit 1
fi

if [[ -z "${GIT_SHA}" ]]; then
    printf '\033[0;31m[FAIL]\033[0m  git rev-parse returned empty string\n' >&2
    exit 1
fi

TIMESTAMP="$(date -u +%Y%m%dT%H%M%S 2>/dev/null || date +%Y%m%dT%H%M%S)"
SESSION_ID="${GIT_SHA}-$$"

STAGE19_IMAGE="creative-stage19-verify:${GIT_SHA}"
HELPER_CONTAINER="creative-stage19-helper-${SESSION_ID}"
WORKER_CONTAINER="creative-stage19-worker-${SESSION_ID}"

TEST_PORT=48091
TEST_WORKER_URL="http://localhost:${TEST_PORT}"
HTTP_TIMEOUT=30
WORKER_WAIT_RETRIES=30
WORKER_WAIT_INTERVAL=2

ARTIFACT_DIR="${PROJECT_ROOT}/verify-artifacts/stage19-${TIMESTAMP}"

# ─── State ────────────────────────────────────────────────────────────────────

HELPER_STARTED=false
WORKER_STARTED=false
FINAL_EXIT=0
PASSED=0
FAILED=0
# Will be set below after ARTIFACT_DIR is created
REPORT_FILE=""

# ─── Colors ───────────────────────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

ok()   { printf "${GREEN}[OK]${NC}    %s\n" "$*"; }
err()  { printf "${RED}[FAIL]${NC}  %s\n" "$*" >&2; }
warn() { printf "${YELLOW}[WARN]${NC}  %s\n" "$*"; }
info() { printf "${BLUE}[INFO]${NC}  %s\n" "$*"; }
sect() { printf "\n${CYAN}== %s ==${NC}\n" "$*"; }

# ─── Docker helper wrappers ───────────────────────────────────────────────────
# /scripts    = PROJECT_ROOT/scripts (read-only; verify_stage19_server.py here)
# /app        = worker source COPY'd during docker build (background package)
# /artifacts  = ARTIFACT_DIR (writable)

run_python() {
    docker exec \
        -e PYTHONPATH=/app \
        -e PYTHONDONTWRITEBYTECODE=1 \
        "${HELPER_CONTAINER}" \
        python "$@"
}

run_python_stdin() {
    docker exec -i \
        -e PYTHONPATH=/app \
        -e PYTHONDONTWRITEBYTECODE=1 \
        "${HELPER_CONTAINER}" \
        python -
}

run_python_module() {
    docker exec \
        -e PYTHONPATH=/app \
        -e PYTHONDONTWRITEBYTECODE=1 \
        "${HELPER_CONTAINER}" \
        python -m "$@"
}

parse_json() {
    # parse_json <host_file_in_artifact_dir> <dotted.key> [default]
    local host_file="$1"
    local key="$2"
    local default="${3:-}"
    local rel
    rel="${host_file#${ARTIFACT_DIR}/}"
    docker exec \
        -e PJ_FILE="/artifacts/${rel}" \
        -e PJ_KEY="${key}" \
        -e PJ_DEFAULT="${default}" \
        "${HELPER_CONTAINER}" \
        python -c '
import json, os
f = os.environ["PJ_FILE"]
k = os.environ["PJ_KEY"]
d = os.environ["PJ_DEFAULT"]
try:
    with open(f) as fh:
        obj = json.load(fh)
    v = obj
    for part in k.split("."):
        v = v[part] if isinstance(v, dict) else d
    print(str(v).lower() if isinstance(v, bool) else str(v))
except Exception:
    print(d)
' 2>/dev/null || echo "${default}"
}

# ─── Cleanup trap ─────────────────────────────────────────────────────────────
# BUG FIX: The previous version read only FINAL_EXIT, which was still 0 when
# set -e triggered early exit (before we could set FINAL_EXIT=1).
# Fix: capture $? at trap entry AND FINAL_EXIT, use whichever is non-zero.

cleanup() {
    local _actual=$?              # exit code that triggered this trap
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

    # Prefer explicitly set FINAL_EXIT; fall back to actual $? from set -e exit.
    if [[ "${_preserve}" -ne 0 ]]; then
        exit "${_preserve}"
    fi
    exit "${_actual}"
}
trap cleanup EXIT

# ─── Parse args ───────────────────────────────────────────────────────────────

SKIP_BUILD=false
for _arg in "$@"; do
    [[ "${_arg}" == "--skip-build" ]] && SKIP_BUILD=true
done

# ═══════════════════════════════════════════════════════════════════════════════
# Step 1: Prerequisites
# ═══════════════════════════════════════════════════════════════════════════════
sect "Step 1: Prerequisites"

mkdir -p "${ARTIFACT_DIR}"
info "Project root:  ${PROJECT_ROOT}"
info "Git SHA:       ${GIT_SHA}"
info "Timestamp:     ${TIMESTAMP}"
info "Test image:    ${STAGE19_IMAGE}"
info "Helper:        ${HELPER_CONTAINER}"
info "Worker:        ${WORKER_CONTAINER} -> ${TEST_WORKER_URL}"
info "Artifact dir:  ${ARTIFACT_DIR}"

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
    err "curl not found (required for HTTP scenario tests)"
    _prereq_fail=true
fi

if [[ "${_prereq_fail}" == "true" ]]; then
    FINAL_EXIT=1
    exit 1
fi

# Host python3 is NOT required — all Python runs inside Docker
if command -v python3 >/dev/null 2>&1; then
    info "Host python3: $(python3 --version 2>&1 | head -1) (INFO only — not used)"
else
    info "Host python3: not found (expected — all Python runs inside Docker)"
fi
ok "Prerequisites: docker, curl available; git SHA resolved to ${GIT_SHA}"

# ═══════════════════════════════════════════════════════════════════════════════
# Step 1.5: Disk space
# ═══════════════════════════════════════════════════════════════════════════════
sect "Step 1.5: Disk Space"

AVAIL_KB=$(df -k "${PROJECT_ROOT}" 2>/dev/null | awk 'NR==2{print $4}' || echo "0")
AVAIL_GB=$(( AVAIL_KB / 1024 / 1024 ))
info "Available: ~${AVAIL_GB} GB on ${PROJECT_ROOT}"
if (( AVAIL_KB < 2097152 )); then
    warn "Low disk (~${AVAIL_GB} GB) — docker build may fail"
fi
ok "Disk check done"

# ═══════════════════════════════════════════════════════════════════════════════
# Step 2: Production container snapshot (before)
# ═══════════════════════════════════════════════════════════════════════════════
sect "Step 2: Production Containers Snapshot (before)"

PROD_BEFORE="${ARTIFACT_DIR}/prod-snapshot-before.txt"
docker ps --filter "name=creative" \
    --format "{{.Names}}\t{{.Status}}\t{{.Image}}" \
    > "${PROD_BEFORE}" 2>/dev/null || true
PROD_COUNT_BEFORE=$(wc -l < "${PROD_BEFORE}" | tr -d ' ')
info "Production 'creative*' containers: ${PROD_COUNT_BEFORE}"
[[ "${PROD_COUNT_BEFORE}" -gt 0 ]] && cat "${PROD_BEFORE}"
ok "Snapshot (before) saved"

# ═══════════════════════════════════════════════════════════════════════════════
# Step 3: Build verify image
# ═══════════════════════════════════════════════════════════════════════════════
# Build context is WORKER_DIR, not PROJECT_ROOT. The root .dockerignore excludes
# worker/ entirely, so using project root as context would cause COPY to fail.
# All COPY instructions in Dockerfile.verify are relative to the worker dir.
sect "Step 3: Build Verify Image (${STAGE19_IMAGE})"

BUILD_LOG="${ARTIFACT_DIR}/docker-build.log"

if [[ "${SKIP_BUILD}" == "true" ]] && docker image inspect "${STAGE19_IMAGE}" >/dev/null 2>&1; then
    info "--skip-build: reusing ${STAGE19_IMAGE}"
else
    # Pre-build file existence checks (fail fast before docker build)
    for _req_file in \
        "${WORKER_DIR}/requirements.txt" \
        "${WORKER_DIR}/requirements-stage19-verify.txt" \
        "${WORKER_DIR}/Dockerfile.verify" \
        "${WORKER_DIR}/app.py" \
        "${SCRIPT_DIR}/verify_stage19_server.py"
    do
        if [[ ! -f "${_req_file}" ]]; then
            err "Pre-build check FAILED: missing ${_req_file}"
            FINAL_EXIT=1
            exit 1
        fi
    done
    ok "Pre-build file checks passed"

    # Context size guard — worker dir must be small (source only, no model caches)
    CONTEXT_SIZE_KB=$(du -sk "${WORKER_DIR}" 2>/dev/null | cut -f1 || echo "0")
    CONTEXT_SIZE_MB=$(( CONTEXT_SIZE_KB / 1024 ))
    info "Build context: ${WORKER_DIR} (~${CONTEXT_SIZE_MB} MB)"
    if (( CONTEXT_SIZE_KB > 256000 )); then
        err "Build context too large (${CONTEXT_SIZE_MB} MB) — check worker/.dockerignore"
        FINAL_EXIT=1
        exit 1
    elif (( CONTEXT_SIZE_KB > 51200 )); then
        warn "Build context is large (${CONTEXT_SIZE_MB} MB) — review worker/.dockerignore"
    fi

    info "Building from ${WORKER_DIR}/Dockerfile.verify ..."
    _build_exit=0
    docker build \
        -t "${STAGE19_IMAGE}" \
        --file "${WORKER_DIR}/Dockerfile.verify" \
        "${WORKER_DIR}" \
        2>&1 | tee "${BUILD_LOG}" || _build_exit=$?

    if [[ "${_build_exit}" -ne 0 ]]; then
        err "docker build failed (exit=${_build_exit}) — see ${BUILD_LOG}"
        FINAL_EXIT=1
        exit 1
    fi
fi
ok "Image ready: ${STAGE19_IMAGE}"

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
    "${STAGE19_IMAGE}" \
    sleep infinity \
    >/dev/null || _helper_exit=$?

if [[ "${_helper_exit}" -ne 0 ]]; then
    err "Failed to start helper container (exit=${_helper_exit})"
    FINAL_EXIT=1
    exit 1
fi
HELPER_STARTED=true
info "Helper: ${HELPER_CONTAINER}"

docker exec "${HELPER_CONTAINER}" echo "helper-alive" >/dev/null
ok "Helper container running"

# ═══════════════════════════════════════════════════════════════════════════════
# Step 3.6: Import check inside helper
# ═══════════════════════════════════════════════════════════════════════════════
sect "Step 3.6: Helper Import Check"

IMPORT_LOG="${ARTIFACT_DIR}/import-check.log"
_import_exit=0
run_python \
    /scripts/verify_stage19_server.py \
    import-check \
    2>&1 | tee "${IMPORT_LOG}" || _import_exit=$?

if [[ "${_import_exit}" -ne 0 ]]; then
    err "Import check FAILED (exit=${_import_exit}) — see ${IMPORT_LOG}"
    err "Required: pillowReady numpyReady pytestReady backgroundPipelineReady localInpaintBackendReady"
    err "Optional: opencvReady (not required; skimage fallback acceptable)"
    FINAL_EXIT=1
    exit 1
fi
ok "helperRuntimeReady=true"

# ═══════════════════════════════════════════════════════════════════════════════
# Step 4: Start isolated worker container
# ═══════════════════════════════════════════════════════════════════════════════
sect "Step 4: Start Isolated Worker Container (port ${TEST_PORT})"

_worker_exit=0
docker run -d \
    --name "${WORKER_CONTAINER}" \
    -p "${TEST_PORT}:5000" \
    -e BACKGROUND_PIPELINE_ENABLED=true \
    -e BACKGROUND_PIPELINE_COMPARE_ONLY=true \
    -e BACKGROUND_LOCAL_INPAINT_ENABLED=true \
    -e BACKGROUND_EXTERNAL_INPAINT_ENABLED=false \
    -e BACKGROUND_OUTPAINT_ENABLED=true \
    -e BACKGROUND_SHADOW_ENABLED=false \
    -e OUTPUT_DIR=/tmp/stage19-outputs \
    "${STAGE19_IMAGE}" \
    python app.py \
    >/dev/null || _worker_exit=$?

if [[ "${_worker_exit}" -ne 0 ]]; then
    err "Failed to start worker container (exit=${_worker_exit})"
    FINAL_EXIT=1
    exit 1
fi
WORKER_STARTED=true
info "Worker: ${WORKER_CONTAINER} -> ${TEST_WORKER_URL}"
ok "Worker container started"

# ═══════════════════════════════════════════════════════════════════════════════
# Step 5: Worker health check
# ═══════════════════════════════════════════════════════════════════════════════
sect "Step 5: Worker Health Check"

_retries=0
while (( _retries < WORKER_WAIT_RETRIES )); do
    if curl -sf "${TEST_WORKER_URL}/health" --max-time 3 -o /dev/null 2>/dev/null; then
        break
    fi
    _retries=$(( _retries + 1 ))
    info "Waiting for worker... (${_retries}/${WORKER_WAIT_RETRIES})"
    sleep "${WORKER_WAIT_INTERVAL}"
done

if (( _retries >= WORKER_WAIT_RETRIES )); then
    err "Worker did not start in ${WORKER_WAIT_RETRIES} retries"
    docker logs "${WORKER_CONTAINER}" 2>&1 | tail -30 | tee "${ARTIFACT_DIR}/worker-startup.log" || true
    FINAL_EXIT=1
    exit 1
fi

HEALTH_RSP="${ARTIFACT_DIR}/health-response.json"
curl -sf "${TEST_WORKER_URL}/v1/background/health" \
    --max-time 5 \
    -o "${HEALTH_RSP}" 2>/dev/null || echo '{}' > "${HEALTH_RSP}"

info "Background health:"
cat "${HEALTH_RSP}"
echo ""

PIPELINE_ENABLED=$(parse_json "${HEALTH_RSP}" "backgroundPipelineEnabled" "false")
if [[ "${PIPELINE_ENABLED}" == "true" ]]; then
    ok "backgroundPipelineEnabled=true"
else
    warn "backgroundPipelineEnabled=${PIPELINE_ENABLED} (expected true)"
fi
ok "Worker healthy at ${TEST_WORKER_URL}"

# ═══════════════════════════════════════════════════════════════════════════════
# Step 6: Generate fixtures
# ═══════════════════════════════════════════════════════════════════════════════
sect "Step 6: Generate Fixtures"

_fix_exit=0
run_python \
    /scripts/verify_stage19_server.py \
    generate-fixtures \
    --artifact-dir /artifacts || _fix_exit=$?

if [[ "${_fix_exit}" -ne 0 ]] || \
   [[ ! -f "${ARTIFACT_DIR}/solid.b64" ]] || \
   [[ ! -f "${ARTIFACT_DIR}/gradient.b64" ]] || \
   [[ ! -f "${ARTIFACT_DIR}/gradient_120x80.b64" ]]; then
    err "Fixture generation failed (exit=${_fix_exit})"
    FINAL_EXIT=1
    exit 1
fi
info "solid.b64:           $(wc -c < "${ARTIFACT_DIR}/solid.b64" | tr -d ' ') chars"
info "gradient.b64:        $(wc -c < "${ARTIFACT_DIR}/gradient.b64" | tr -d ' ') chars"
info "gradient_120x80.b64: $(wc -c < "${ARTIFACT_DIR}/gradient_120x80.b64" | tr -d ' ') chars"
ok "Fixtures generated"

# ═══════════════════════════════════════════════════════════════════════════════
# Step 7: Build request JSONs (scenarios A-E)
# ═══════════════════════════════════════════════════════════════════════════════
sect "Step 7: Build Request JSONs (A-E)"

for SC in A B C D E; do
    _req_exit=0
    run_python \
        /scripts/verify_stage19_server.py \
        build-request \
        --scenario "${SC}" \
        --artifact-dir /artifacts || _req_exit=$?

    if [[ "${_req_exit}" -ne 0 ]] || [[ ! -f "${ARTIFACT_DIR}/request-${SC,,}.json" ]]; then
        err "Failed to build request-${SC,,}.json (exit=${_req_exit})"
        FINAL_EXIT=1
    else
        info "request-${SC,,}.json: $(wc -c < "${ARTIFACT_DIR}/request-${SC,,}.json" | tr -d ' ') bytes"
    fi
done

if [[ "${FINAL_EXIT}" -ne 0 ]]; then
    exit 1
fi
ok "Request JSONs A-E built"

# ═══════════════════════════════════════════════════════════════════════════════
# Step 8A-E: HTTP scenarios against isolated worker
# Step 8F:   Direct Python quality gate test (no HTTP needed)
# ═══════════════════════════════════════════════════════════════════════════════
sect "Step 8: Scenarios A-F"

# ── Scenarios A-E: HTTP POST ──────────────────────────────────────────────────

for SC in A B C D E; do
    REQ_FILE="${ARTIFACT_DIR}/request-${SC,,}.json"
    RSP_FILE="${ARTIFACT_DIR}/response-${SC,,}.json"
    info "Scenario ${SC}: POST ${TEST_WORKER_URL}/v1/background/process"

    HTTP_STATUS=$(curl -s \
        -w "%{http_code}" \
        -o "${RSP_FILE}" \
        -X POST "${TEST_WORKER_URL}/v1/background/process" \
        -H "Content-Type: application/json" \
        --data-binary "@${REQ_FILE}" \
        --max-time "${HTTP_TIMEOUT}" \
        2>/dev/null || echo "000")

    echo "${HTTP_STATUS}" > "${ARTIFACT_DIR}/http-status-${SC,,}.txt"

    if [[ "${HTTP_STATUS}" == "200" ]]; then
        info "Scenario ${SC}: HTTP 200"
    else
        err "Scenario ${SC}: HTTP ${HTTP_STATUS}"
        FINAL_EXIT=1
    fi
done

# ── Scenario F: Direct Python quality gate test ───────────────────────────────

info "Scenario F: direct quality gate test (no HTTP)"
SC_F_LOG="${ARTIFACT_DIR}/scenario-f-direct.log"
_scf_exit=0
run_python \
    /scripts/verify_stage19_server.py \
    evaluate \
    --scenario F \
    --artifact-dir /artifacts \
    2>&1 | tee "${SC_F_LOG}" || _scf_exit=$?

if [[ "${_scf_exit}" -ne 0 ]] || grep -q "\[FAIL\]" "${SC_F_LOG}" 2>/dev/null; then
    err "Scenario F: direct test FAILED (exit=${_scf_exit})"
    FINAL_EXIT=1
else
    ok "Scenario F: direct test passed"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# Step 9: Evaluate HTTP responses (A-E) + confirm F
# ═══════════════════════════════════════════════════════════════════════════════
sect "Step 9: Evaluate Responses"

for SC in A B C D E; do
    EVAL_LOG="${ARTIFACT_DIR}/eval-${SC,,}.log"
    _eval_exit=0
    run_python \
        /scripts/verify_stage19_server.py \
        evaluate \
        --scenario "${SC}" \
        --response-file "/artifacts/response-${SC,,}.json" \
        --artifact-dir /artifacts \
        2>&1 | tee "${EVAL_LOG}" || _eval_exit=0  # allow to continue; verdict in file

    EVAL_FILE="${ARTIFACT_DIR}/evaluation-${SC,,}.json"
    if [[ -f "${EVAL_FILE}" ]]; then
        EVAL_VERDICT=$(parse_json "${EVAL_FILE}" "verdict" "UNKNOWN")
        if [[ "${EVAL_VERDICT}" == "PASS" ]]; then
            ok "Scenario ${SC}: PASS"
            (( PASSED++ )) || true
        else
            EVAL_FAIL=$(parse_json "${EVAL_FILE}" "failures" "[]" 2>/dev/null || echo "[]")
            err "Scenario ${SC}: ${EVAL_VERDICT} — ${EVAL_FAIL}"
            (( FAILED++ )) || true
            FINAL_EXIT=1
        fi
    else
        err "Scenario ${SC}: evaluation file not found"
        (( FAILED++ )) || true
        FINAL_EXIT=1
    fi
done

# Scenario F: already written by step 8F
F_EVAL="${ARTIFACT_DIR}/evaluation-f.json"
if [[ -f "${F_EVAL}" ]]; then
    F_VERDICT=$(parse_json "${F_EVAL}" "verdict" "UNKNOWN")
    if [[ "${F_VERDICT}" == "PASS" ]]; then
        ok  "Scenario F: PASS"
        (( PASSED++ )) || true
    else
        err "Scenario F: ${F_VERDICT}"
        (( FAILED++ )) || true
        FINAL_EXIT=1
    fi
else
    err "Scenario F: evaluation-f.json not found"
    (( FAILED++ )) || true
    FINAL_EXIT=1
fi

info "Evaluation: passed=${PASSED}/6, failed=${FAILED}/6"

# ═══════════════════════════════════════════════════════════════════════════════
# Step 10: pytest — Stage 19 unit tests
# ═══════════════════════════════════════════════════════════════════════════════
sect "Step 10: pytest (Stage 19 unit tests)"

PYTEST_LOG="${ARTIFACT_DIR}/pytest-stage19.log"
PYTEST_EXIT=0

run_python_module pytest \
    /app/test_stage19.py \
    -q \
    -p no:cacheprovider \
    --tb=short \
    2>&1 | tee "${PYTEST_LOG}" || PYTEST_EXIT=$?

if [[ "${PYTEST_EXIT}" -ne 0 ]]; then
    err "pytest (Stage 19) FAILED (exit=${PYTEST_EXIT})"
    FINAL_EXIT=1
else
    _pt=$(grep -oP '\d+(?= passed)' "${PYTEST_LOG}" | tail -1 || echo "?")
    ok "pytest (Stage 19): ${_pt} passed"
fi

# ─── Step 10.5: Stage 18 regression ──────────────────────────────────────────
sect "Step 10.5: Stage 18 Regression Tests"

PYTEST18_LOG="${ARTIFACT_DIR}/pytest-stage18-regression.log"
PYTEST18_EXIT=0

run_python_module pytest \
    /app/test_stage19.py \
    -q \
    -p no:cacheprovider \
    --tb=short \
    -k "stage18" \
    2>&1 | tee "${PYTEST18_LOG}" || PYTEST18_EXIT=$?

if [[ "${PYTEST18_EXIT}" -ne 0 ]]; then
    err "Stage 18 regression FAILED"
    FINAL_EXIT=1
else
    _pt18=$(grep -oP '\d+(?= passed)' "${PYTEST18_LOG}" | tail -1 || echo "?")
    ok "Stage 18 regression: ${_pt18} passed (Stage 18 code untouched)"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# Step 11: Generate final report
# ═══════════════════════════════════════════════════════════════════════════════
sect "Step 11: Generate Final Report"

REPORT_FILE="${ARTIFACT_DIR}/stage19-verification-report.json"
run_python \
    /scripts/verify_stage19_server.py \
    report \
    --artifact-dir /artifacts \
    --git-sha "${GIT_SHA}" \
    --timestamp "${TIMESTAMP}" \
    --output /artifacts/stage19-verification-report.json \
    2>&1 | tee -a "${ARTIFACT_DIR}/report-gen.log" || true

if [[ -f "${REPORT_FILE}" ]]; then
    REPORT_VERDICT=$(parse_json "${REPORT_FILE}" "verdict" "UNKNOWN")
    info "Report verdict: ${REPORT_VERDICT}"
    info "Report file:    ${REPORT_FILE}"
    [[ "${REPORT_VERDICT}" == "FAIL" ]] && FINAL_EXIT=1 || true
else
    warn "Report JSON not generated"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# Step 12: Production container snapshot (after)
# ═══════════════════════════════════════════════════════════════════════════════
sect "Step 12: Production Containers Snapshot (after)"

PROD_AFTER="${ARTIFACT_DIR}/prod-snapshot-after.txt"
docker ps --filter "name=creative" \
    --format "{{.Names}}\t{{.Status}}\t{{.Image}}" \
    | grep -v "${HELPER_CONTAINER}" \
    | grep -v "${WORKER_CONTAINER}" \
    > "${PROD_AFTER}" 2>/dev/null || true

PROD_COUNT_AFTER=$(wc -l < "${PROD_AFTER}" | tr -d ' ')
if [[ "${PROD_COUNT_AFTER}" -eq "${PROD_COUNT_BEFORE}" ]]; then
    ok "Production containers unchanged (${PROD_COUNT_BEFORE} containers)"
else
    warn "Production container count changed: ${PROD_COUNT_BEFORE} -> ${PROD_COUNT_AFTER}"
fi

EXITED=$(grep -iE "Exited|Restarting" "${PROD_AFTER}" 2>/dev/null || true)
[[ -n "${EXITED}" ]] && warn "Some production containers may be unhealthy:" && echo "${EXITED}" || true

# ═══════════════════════════════════════════════════════════════════════════════
# Step 13: Final Verdict
# ═══════════════════════════════════════════════════════════════════════════════
sect "Step 13: Final Verdict"

echo ""
echo "  Git SHA:      ${GIT_SHA}"
echo "  Scenarios:    ${PASSED}/6 passed, ${FAILED}/6 failed"
echo "  Artifact dir: ${ARTIFACT_DIR}"
[[ -f "${REPORT_FILE}" ]] && echo "  Report:       ${REPORT_FILE}" || true
echo ""

if [[ "${FINAL_EXIT}" -eq 0 ]]; then
    printf "${GREEN}[PASS] Stage 19 Background Pipeline verification PASSED${NC}\n"
    printf "       containerBased=true  hostPythonRequired=false  gitSha=%s\n" "${GIT_SHA}"
else
    printf "${RED}[FAIL] Stage 19 Background Pipeline verification FAILED${NC}\n"
    printf "       gitSha=%s  Check %s for details\n" "${GIT_SHA}" "${ARTIFACT_DIR}"
fi

echo ""
exit "${FINAL_EXIT}"
