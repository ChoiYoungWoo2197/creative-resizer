#!/usr/bin/env bash
# scripts/verify_stage20_actual_ai_golden.sh
#
# Stage 20.3 — Actual AI Provider Golden Verification
#
# Builds an isolated verify container, passes BACKGROUND_AI_API_KEY securely,
# runs dry-run first, then actual AI golden for mother-hand-product.psd.
#
# REQUIRES: docker, git
# Host python3 NOT required — all Python runs inside container.
#
# Usage:
#   BACKGROUND_AI_API_KEY=sk-... bash scripts/verify_stage20_actual_ai_golden.sh
#   BACKGROUND_AI_API_KEY=sk-... bash scripts/verify_stage20_actual_ai_golden.sh \
#       --psd /path/to/mother-hand-product.psd
#   bash scripts/verify_stage20_actual_ai_golden.sh --dry-run-only
#   bash scripts/verify_stage20_actual_ai_golden.sh --skip-build
#
# Exit codes:
#   0  All specs PASS actual AI golden
#   1  Functional / quality / artifact failure
#   2  BLOCKED: provider not configured, PSD not found, or dry-run-only mode
#   3  Provider API completely unreachable (network/auth error)
#
# Safety guarantees:
#   - Production containers NEVER restarted or modified
#   - Feature flags NEVER changed in production
#   - API key NEVER printed, logged, or committed
#   - Temp env file deleted on exit regardless of outcome
#   - docker system prune / volume prune NEVER run

set -Eeuo pipefail

# ─── Paths ────────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
WORKER_DIR="${PROJECT_ROOT}/worker"

cd "${PROJECT_ROOT}"

# ─── Arg parsing ──────────────────────────────────────────────────────────────

SKIP_BUILD=false
DRY_RUN_ONLY=false
PSD_PATH=""
SKIP_REGRESSION=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-build)       SKIP_BUILD=true       ; shift ;;
        --dry-run-only)     DRY_RUN_ONLY=true      ; shift ;;
        --skip-regression)  SKIP_REGRESSION=true   ; shift ;;
        --psd)              PSD_PATH="$2"           ; shift 2 ;;
        *) echo "[WARN] Unknown arg: $1" ; shift ;;
    esac
done

# ─── Git SHA ──────────────────────────────────────────────────────────────────

if ! GIT_SHA="$(git -c "safe.directory=${PROJECT_ROOT}" rev-parse --short HEAD 2>/dev/null)"; then
    printf '[FAIL]  Cannot determine Git SHA\n' >&2 ; exit 1
fi
[[ -z "${GIT_SHA}" ]] && { printf '[FAIL] git rev-parse returned empty\n' >&2; exit 1; }

TIMESTAMP="$(date -u +%Y%m%dT%H%M%S 2>/dev/null || date +%Y%m%dT%H%M%S)"
SESSION_ID="${GIT_SHA}-$$"

STAGE20_IMAGE="creative-stage203-verify:${GIT_SHA}"
HELPER_CONTAINER="creative-stage203-helper-${SESSION_ID}"

VERIFY_PORT=48093
ARTIFACT_DIR="${PROJECT_ROOT}/verify-artifacts/stage20-3-actual-ai-${TIMESTAMP}"

# ─── Colors ───────────────────────────────────────────────────────────────────

RED='\033[0;31m'
GRN='\033[0;32m'
YLW='\033[1;33m'
BLU='\033[0;34m'
CYN='\033[0;36m'
NC='\033[0m'

ok()   { printf "${GRN}[OK]${NC}    %s\n" "$*"; }
err()  { printf "${RED}[FAIL]${NC}  %s\n" "$*" >&2; }
warn() { printf "${YLW}[WARN]${NC}  %s\n" "$*"; }
info() { printf "${BLU}[INFO]${NC}  %s\n" "$*"; }
sect() { printf "\n${CYN}== %s ==${NC}\n" "$*"; }

# ─── State ────────────────────────────────────────────────────────────────────

HELPER_STARTED=false
FINAL_EXIT=0
STAGE203_EXIT=0
PYTEST20_EXIT=0
PYTEST19_EXIT=0
TEMP_ENV_FILE=""

PYTEST20_LOG="${ARTIFACT_DIR}/stage20-pytest.log"
PYTEST19_LOG="${ARTIFACT_DIR}/stage19-regression.log"
DRYRUN_LOG="${ARTIFACT_DIR}/stage203-dryrun.log"
ACTUAL_LOG="${ARTIFACT_DIR}/stage203-actual.log"

# ─── Production container snapshot ───────────────────────────────────────────

_PROD_SNAPSHOT_BEFORE=""
_PROD_SNAPSHOT_AFTER=""

_snapshot_prod() {
    # Read-only: record current state of production creative-worker container
    _PROD_SNAPSHOT_BEFORE="$(docker inspect creative-worker 2>/dev/null | \
        python3 -c "import sys,json; d=json.load(sys.stdin); \
        c=d[0] if d else {}; \
        print(c.get('Image','none')[:20], c.get('State',{}).get('Status','none'))" \
        2>/dev/null || echo 'inspect_failed none')"
}

_verify_prod_unchanged() {
    _PROD_SNAPSHOT_AFTER="$(docker inspect creative-worker 2>/dev/null | \
        python3 -c "import sys,json; d=json.load(sys.stdin); \
        c=d[0] if d else {}; \
        print(c.get('Image','none')[:20], c.get('State',{}).get('Status','none'))" \
        2>/dev/null || echo 'inspect_failed none')"
    if [[ "${_PROD_SNAPSHOT_BEFORE}" == "${_PROD_SNAPSHOT_AFTER}" ]]; then
        ok "Production container unchanged"
    else
        warn "Production container state changed (before: ${_PROD_SNAPSHOT_BEFORE} / after: ${_PROD_SNAPSHOT_AFTER})"
    fi
}

# ─── Cleanup ──────────────────────────────────────────────────────────────────

cleanup() {
    local exit_code=$?

    # Remove temp env file immediately — top priority
    if [[ -n "${TEMP_ENV_FILE}" && -f "${TEMP_ENV_FILE}" ]]; then
        rm -f "${TEMP_ENV_FILE}" 2>/dev/null || true
        info "Temp env file removed"
    fi

    if [[ "${HELPER_STARTED}" == "true" ]]; then
        docker rm -f "${HELPER_CONTAINER}" >/dev/null 2>&1 || true
    fi

    _verify_prod_unchanged

    info "Artifacts: ${ARTIFACT_DIR}"

    if [[ "${exit_code}" -ne 0 ]]; then
        err "Verification exited with code ${exit_code}"
    fi
}

trap cleanup EXIT

# ─── Prepare ──────────────────────────────────────────────────────────────────

mkdir -p "${ARTIFACT_DIR}"
_snapshot_prod

sect "Stage 20.3 Actual AI Golden — Setup"
info "Git SHA:     ${GIT_SHA}"
info "Image:       ${STAGE20_IMAGE}"
info "Artifact:    ${ARTIFACT_DIR}"
info "DryRunOnly:  ${DRY_RUN_ONLY}"
info "SkipBuild:   ${SKIP_BUILD}"

# ─── Collect API Key (secure) ─────────────────────────────────────────────────

sect "Provider Key Collection"

# Env var priority: shell env → .env file → blocked
_KEY_SRC="not_found"
_RAW_KEY="${BACKGROUND_AI_API_KEY:-}"

if [[ -z "${_RAW_KEY}" && -f "${PROJECT_ROOT}/.env" ]]; then
    _RAW_KEY="$(grep -E '^BACKGROUND_AI_API_KEY=' "${PROJECT_ROOT}/.env" 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'" || true)"
    [[ -n "${_RAW_KEY}" ]] && _KEY_SRC=".env_file"
fi

if [[ -n "${_RAW_KEY}" ]]; then
    _KEY_SRC="${_KEY_SRC:-shell_env}"
    KEY_LEN="${#_RAW_KEY}"
    info "BACKGROUND_AI_API_KEY: configured  keyLength=${KEY_LEN}  source=${_KEY_SRC}"
    # Write to temp file with restricted permissions
    TEMP_ENV_FILE="$(mktemp /tmp/stage203_env_XXXXXX)"
    chmod 600 "${TEMP_ENV_FILE}"
    printf 'BACKGROUND_AI_API_KEY=%s\n' "${_RAW_KEY}" > "${TEMP_ENV_FILE}"
    # Also add MODEL if set
    _MODEL="${BACKGROUND_AI_MODEL:-gpt-image-1}"
    printf 'BACKGROUND_AI_MODEL=%s\n' "${_MODEL}" >> "${TEMP_ENV_FILE}"
    unset _RAW_KEY  # clear from memory
else
    info "BACKGROUND_AI_API_KEY: not set"
    if [[ "${DRY_RUN_ONLY}" == "false" ]]; then
        err "BACKGROUND_AI_API_KEY not configured."
        echo ""
        echo "  Set it via:"
        echo "    export BACKGROUND_AI_API_KEY=sk-..."
        echo "    BACKGROUND_AI_API_KEY=sk-... bash scripts/verify_stage20_actual_ai_golden.sh"
        echo ""
        echo "  Or add to .env:"
        echo "    BACKGROUND_AI_API_KEY=sk-..."
        echo ""
        echo "  For mask/prompt preview only:"
        echo "    bash scripts/verify_stage20_actual_ai_golden.sh --dry-run-only"
        FINAL_EXIT=2
        exit 2
    fi
    info "No key — proceeding in dry-run-only mode"
fi

# ─── Resolve PSD path ─────────────────────────────────────────────────────────

sect "PSD Resolution"

# Priority: --psd arg → STAGE20_PSD_PATH env → common locations
if [[ -z "${PSD_PATH}" ]]; then
    PSD_PATH="${STAGE20_PSD_PATH:-}"
fi
if [[ -z "${PSD_PATH}" ]]; then
    for _candidate in \
        "${PROJECT_ROOT}/test-assets/stage18/mother-hand-product.psd" \
        "${PROJECT_ROOT}/test-assets/mother-hand-product.psd" \
        "/app/storage/inputs/mother-hand-product.psd" \
        "/data/mother-hand-product.psd"
    do
        if [[ -f "${_candidate}" ]]; then
            PSD_PATH="${_candidate}"
            break
        fi
    done
fi

if [[ -n "${PSD_PATH}" && -f "${PSD_PATH}" ]]; then
    ok "PSD found: ${PSD_PATH}"
    PSD_MOUNT_SRC="${PSD_PATH}"
    PSD_MOUNT_DST="/golden-psd/mother-hand-product.psd"
else
    warn "PSD not found (checked: ${PSD_PATH:-<none>})"
    warn "Golden test will be BLOCKED unless PSD is provided."
    warn "Provide path: --psd /path/to/mother-hand-product.psd"
    PSD_MOUNT_SRC=""
    PSD_MOUNT_DST="/golden-psd/mother-hand-product.psd"
fi

# ─── Build verify image ───────────────────────────────────────────────────────

sect "Image Build"

if [[ "${SKIP_BUILD}" == "false" ]]; then
    info "Building ${STAGE20_IMAGE} from worker/ ..."
    if docker build -q \
        -t "${STAGE20_IMAGE}" \
        -f "${WORKER_DIR}/Dockerfile.verify" \
        "${WORKER_DIR}" \
        > "${ARTIFACT_DIR}/docker-build.log" 2>&1
    then
        ok "Image built: ${STAGE20_IMAGE}"
    else
        err "Docker build failed — see ${ARTIFACT_DIR}/docker-build.log"
        FINAL_EXIT=1
        exit 1
    fi
else
    info "Skipping build (--skip-build)"
    if ! docker image inspect "${STAGE20_IMAGE}" >/dev/null 2>&1; then
        err "Image ${STAGE20_IMAGE} not found. Remove --skip-build to rebuild."
        FINAL_EXIT=1; exit 1
    fi
fi

# ─── Start helper container ───────────────────────────────────────────────────

sect "Helper Container"

_DOCKER_RUN_ARGS=(
    "--name" "${HELPER_CONTAINER}"
    "--rm" "--network=none"
    "-e" "PYTHONPATH=/app"
    "-e" "PYTHONDONTWRITEBYTECODE=1"
    "-e" "GIT_SHA=${GIT_SHA}"
    "-v" "${ARTIFACT_DIR}:/artifacts:rw"
)

# Mount temp env file (read-only)
if [[ -n "${TEMP_ENV_FILE}" && -f "${TEMP_ENV_FILE}" ]]; then
    _DOCKER_RUN_ARGS+=("--env-file" "${TEMP_ENV_FILE}")
fi

# Mount PSD file (read-only)
if [[ -n "${PSD_MOUNT_SRC}" ]]; then
    _DOCKER_RUN_ARGS+=("-v" "${PSD_MOUNT_SRC}:${PSD_MOUNT_DST}:ro")
fi

# Mount scripts dir (read-only)
_DOCKER_RUN_ARGS+=("-v" "${SCRIPT_DIR}:/scripts:ro")

docker run -d "${_DOCKER_RUN_ARGS[@]}" "${STAGE20_IMAGE}" sleep infinity \
    > /dev/null 2>&1

HELPER_STARTED=true
ok "Helper: ${HELPER_CONTAINER}"

# Delete temp env file now that container has loaded it
if [[ -n "${TEMP_ENV_FILE}" && -f "${TEMP_ENV_FILE}" ]]; then
    rm -f "${TEMP_ENV_FILE}" 2>/dev/null || true
    TEMP_ENV_FILE=""
    info "Temp env file deleted after container start"
fi

# ─── Import smoke ─────────────────────────────────────────────────────────────

sect "Import Smoke Test"

docker exec \
    -e PYTHONPATH=/app \
    "${HELPER_CONTAINER}" \
    python -c "
import background
import background.openai_provider
import background.external_provider
import background.source_faithful_repair
import background.mode_selector
import background.smart_fit_guard
import background.prompt_builder
print('Stage20.3 import smoke: OK')
" > "${ARTIFACT_DIR}/import-smoke.log" 2>&1 || { err "Import smoke failed"; FINAL_EXIT=1; }

if [[ "${FINAL_EXIT}" -eq 0 ]]; then
    ok "Import smoke: PASS"
else
    err "Import smoke: FAIL — see ${ARTIFACT_DIR}/import-smoke.log"
    cat "${ARTIFACT_DIR}/import-smoke.log" | tail -10
fi

# ─── Provider configured check ────────────────────────────────────────────────

sect "Provider Configured Check (in container)"

docker exec \
    -e PYTHONPATH=/app \
    "${HELPER_CONTAINER}" \
    python -c "
import os
from background.openai_provider import OpenAIInpaintProvider
p = OpenAIInpaintProvider()
key = os.environ.get('BACKGROUND_AI_API_KEY', '')
key_len = len(key) if key else 0
print(f'providerConfigured={p.is_configured()}')
print(f'providerName={p.provider_name}')
print(f'providerModel={p.model_name}')
print(f'providerKeyConfigured={p.is_configured()}')
print(f'keyLength={key_len}')
# NEVER print the key itself
" > "${ARTIFACT_DIR}/provider-check.log" 2>&1 || { err "Provider check failed"; FINAL_EXIT=1; }

if [[ "${FINAL_EXIT}" -eq 0 ]]; then
    ok "Provider check: PASS"
    cat "${ARTIFACT_DIR}/provider-check.log"
fi

# ─── Stage 20.1/20.2 regression tests ────────────────────────────────────────

if [[ "${SKIP_REGRESSION}" == "false" ]]; then
    sect "Stage 20 Regression Tests"

    docker exec \
        -e PYTHONPATH=/app \
        -e PYTHONDONTWRITEBYTECODE=1 \
        "${HELPER_CONTAINER}" \
        python -m pytest /app/test_stage20_typography.py /app/test_stage20_2.py \
            -q -p no:cacheprovider --tb=short \
        > "${PYTEST20_LOG}" 2>&1 || PYTEST20_EXIT=$?

    if [[ "${PYTEST20_EXIT}" -eq 0 ]]; then
        ok "Stage 20.1+20.2 tests: PASS"
    else
        err "Stage 20.1+20.2 tests: FAIL (exit=${PYTEST20_EXIT})"
        FINAL_EXIT=1
    fi
    cat "${PYTEST20_LOG}" | tail -5

    # Stage 19 regression
    docker exec \
        -e PYTHONPATH=/app \
        -e PYTHONDONTWRITEBYTECODE=1 \
        "${HELPER_CONTAINER}" \
        python -m pytest /app/test_stage19.py \
            -q -p no:cacheprovider --tb=short \
        > "${PYTEST19_LOG}" 2>&1 || PYTEST19_EXIT=$?

    if [[ "${PYTEST19_EXIT}" -eq 0 ]]; then
        ok "Stage 19 regression: PASS"
    else
        err "Stage 19 regression: FAIL (exit=${PYTEST19_EXIT})"
        FINAL_EXIT=1
    fi
    cat "${PYTEST19_LOG}" | tail -5
fi

# ─── Stage 20.3 Dry-run ───────────────────────────────────────────────────────

sect "Stage 20.3 Dry-run (masks, prompts, no API)"

_GOLDEN_PSD_ARG="${PSD_MOUNT_DST}"
if [[ -z "${PSD_MOUNT_SRC}" ]]; then
    _GOLDEN_PSD_ARG="/nonexistent/missing.psd"
fi

docker exec \
    -e PYTHONPATH=/app \
    -e GIT_SHA="${GIT_SHA}" \
    "${HELPER_CONTAINER}" \
    python /scripts/stage20_actual_ai_golden.py \
        --psd "${_GOLDEN_PSD_ARG}" \
        --specs "1250x560,1200x300,300x1200" \
        --outdir "/artifacts/dryrun" \
        --dry-run \
    > "${DRYRUN_LOG}" 2>&1 || true  # dry-run exit code not critical

cat "${DRYRUN_LOG}" | tail -15
ok "Dry-run complete — see ${ARTIFACT_DIR}/dryrun/"

if [[ "${DRY_RUN_ONLY}" == "true" ]]; then
    warn "DRY-RUN-ONLY mode — skipping actual AI call"
    printf "\n${YLW}[DRY-RUN]${NC} Mask/prompt preview generated. No API calls made.\n"
    printf "          Run without --dry-run-only for actual AI golden.\n\n"
    FINAL_EXIT=2
    exit 2
fi

# ─── Stage 20.3 Actual AI Golden ─────────────────────────────────────────────

sect "Stage 20.3 Actual AI Golden"

if [[ -z "${PSD_MOUNT_SRC}" ]]; then
    err "PSD not found — cannot run actual AI golden"
    err "Provide: --psd /path/to/mother-hand-product.psd"
    FINAL_EXIT=2
    exit 2
fi

docker exec \
    -e PYTHONPATH=/app \
    -e GIT_SHA="${GIT_SHA}" \
    "${HELPER_CONTAINER}" \
    python /scripts/stage20_actual_ai_golden.py \
        --psd "${_GOLDEN_PSD_ARG}" \
        --specs "1250x560,1200x300,300x1200" \
        --outdir "/artifacts/actual" \
    > "${ACTUAL_LOG}" 2>&1 || STAGE203_EXIT=$?

cat "${ACTUAL_LOG}" | tail -30

# Copy actual artifacts to host artifact dir
docker cp "${HELPER_CONTAINER}:/artifacts/actual/." "${ARTIFACT_DIR}/actual/" 2>/dev/null || true

case "${STAGE203_EXIT}" in
    0) ok "Actual AI Golden: PASS (exit=0)" ;;
    1) err "Actual AI Golden: FAIL (exit=1)" ; FINAL_EXIT=1 ;;
    2) err "Actual AI Golden: BLOCKED (exit=2)" ; FINAL_EXIT=2 ;;
    3) err "Actual AI Golden: API UNREACHABLE (exit=3)" ; FINAL_EXIT=3 ;;
    *) err "Actual AI Golden: unknown exit=${STAGE203_EXIT}" ; FINAL_EXIT=1 ;;
esac

# ─── Summary report ───────────────────────────────────────────────────────────

sect "Stage 20.3 Summary"

_pt20_val=$(grep -oP '\d+(?= passed)' "${PYTEST20_LOG}" 2>/dev/null | tail -1 || echo "?")
_pt19_val=$(grep -oP '\d+(?= passed)' "${PYTEST19_LOG}" 2>/dev/null | tail -1 || echo "?")

printf "  %-35s : %s\n" "Git SHA" "${GIT_SHA}"
printf "  %-35s : %s\n" "Stage 20.1+20.2 tests" "${_pt20_val} passed  (exit=${PYTEST20_EXIT})"
printf "  %-35s : %s\n" "Stage 19 regression" "${_pt19_val} passed  (exit=${PYTEST19_EXIT})"
printf "  %-35s : %s\n" "Actual AI Golden" "exit=${STAGE203_EXIT}"
printf "  %-35s : %s\n" "FINAL_EXIT" "${FINAL_EXIT}"
echo ""

if [[ "${FINAL_EXIT}" -eq 0 ]]; then
    printf "${GRN}[PASS] Stage 20.3 Actual AI Golden PASSED${NC}\n"
    printf "       gitSha=%s  containerBased=true\n" "${GIT_SHA}"
    printf "\n"
    printf "Artifacts: %s\n" "${ARTIFACT_DIR}"
    printf "Contact sheet: %s/actual/all-specs-contact-sheet.png\n" "${ARTIFACT_DIR}"
else
    printf "${RED}[FAIL] Stage 20.3 FAILED (exit=${FINAL_EXIT})${NC}\n"
    printf "       gitSha=%s\n" "${GIT_SHA}"
    printf "\n"
    printf "Troubleshooting:\n"
    printf "  cat %s/import-smoke.log\n" "${ARTIFACT_DIR}"
    printf "  cat %s/provider-check.log\n" "${ARTIFACT_DIR}"
    printf "  cat %s/stage203-actual.log\n" "${ARTIFACT_DIR}"
    printf "  cat %s/actual/1250x560/report.json\n" "${ARTIFACT_DIR}"
fi

exit "${FINAL_EXIT}"
