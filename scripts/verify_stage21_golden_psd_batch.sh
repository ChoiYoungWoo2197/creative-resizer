#!/usr/bin/env bash
# scripts/verify_stage21_golden_psd_batch.sh
#
# Stage 21 — Golden PSD Batch Verification
#
# Runs stage21_golden_batch.py inside the creative-worker container image.
# Supports Dry Run (no AI calls) and Actual AI modes.
# Includes cross-source A→B→A isolation test.
#
# REQUIRES: docker, git
# Host python3 NOT required — all Python runs inside the container.
#
# Usage:
#   bash scripts/verify_stage21_golden_psd_batch.sh              # dry-run
#   bash scripts/verify_stage21_golden_psd_batch.sh --actual-ai  # real AI calls
#   bash scripts/verify_stage21_golden_psd_batch.sh --skip-build
#   bash scripts/verify_stage21_golden_psd_batch.sh --psd-dir /path/to/psds
#
# Exit codes:
#   0  All tests PASS (including A→B→A isolation)
#   1  Quality / role failure
#   2  Missing config / PSD not found
#   3  Provider unavailable
#   4  Cross-source contamination detected
#
# Safety guarantees:
#   - Production containers NEVER restarted or modified
#   - API key NEVER printed, logged, or committed
#   - docker system prune / volume prune NEVER run
#   - git reset --hard / force push NEVER run

set -Eeuo pipefail

# ─── Paths ────────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
WORKER_DIR="${PROJECT_ROOT}/worker"

cd "${PROJECT_ROOT}"

# ─── Arg parsing ──────────────────────────────────────────────────────────────

SKIP_BUILD=false
ACTUAL_AI=false
PSD_DIR=""
OUTPUT_DIR=""
PSD_NAMES=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-build)  SKIP_BUILD=true        ; shift ;;
        --actual-ai)   ACTUAL_AI=true          ; shift ;;
        --psd-dir)     PSD_DIR="$2"            ; shift 2 ;;
        --output-dir)  OUTPUT_DIR="$2"         ; shift 2 ;;
        --psd-names)   PSD_NAMES="$2"          ; shift 2 ;;
        *) echo "[WARN] Unknown arg: $1"       ; shift ;;
    esac
done

# ─── Git SHA ──────────────────────────────────────────────────────────────────

GIT_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")"
echo "[BATCH_VERIFY] git_sha=${GIT_SHA} actual_ai=${ACTUAL_AI}"

# ─── Defaults ─────────────────────────────────────────────────────────────────

CONTAINER_NAME="stage21-golden-batch-${GIT_SHA}-$$"
IMAGE_TAG="creative-worker-stage21-verify:${GIT_SHA}"
HOST_OUTPUT_DIR="${OUTPUT_DIR:-/tmp/stage21-golden-batch-${GIT_SHA}}"
HOST_PSD_DIR="${PSD_DIR:-${PROJECT_ROOT}/worker/test_psd}"

mkdir -p "${HOST_OUTPUT_DIR}"
mkdir -p "${HOST_PSD_DIR}"

# ─── API key handling (actual-ai mode) ────────────────────────────────────────

API_KEY_FILE=""
CLEANUP_KEY_FILE=true

if [[ "${ACTUAL_AI}" == "true" ]]; then
    if [[ -z "${BACKGROUND_AI_API_KEY:-}" ]]; then
        echo "[ERROR] --actual-ai requires BACKGROUND_AI_API_KEY env var." >&2
        exit 2
    fi
    # Write key to temp file — NEVER pass as plain CLI arg or log it
    API_KEY_FILE="$(mktemp /tmp/stage21-golden-api-key.XXXXXX)"
    echo "${BACKGROUND_AI_API_KEY}" > "${API_KEY_FILE}"
    chmod 600 "${API_KEY_FILE}"
    echo "[BATCH_VERIFY] API key written to temp file (not logged)"
fi

# ─── Cleanup on exit ──────────────────────────────────────────────────────────

cleanup() {
    # Remove temp key file regardless of outcome
    if [[ -n "${API_KEY_FILE}" && -f "${API_KEY_FILE}" ]]; then
        rm -f "${API_KEY_FILE}"
        echo "[BATCH_VERIFY] Temp API key file removed."
    fi
    # Remove verify container if still running
    docker rm -f "${CONTAINER_NAME}" 2>/dev/null || true
}
trap cleanup EXIT

# ─── Build verify image ────────────────────────────────────────────────────────

if [[ "${SKIP_BUILD}" == "false" ]]; then
    echo "[BATCH_VERIFY] Building verify image: ${IMAGE_TAG}"
    docker build \
        --no-cache \
        -t "${IMAGE_TAG}" \
        -f "${WORKER_DIR}/Dockerfile" \
        "${WORKER_DIR}"
    echo "[BATCH_VERIFY] Build complete."
else
    echo "[BATCH_VERIFY] Skipping build (--skip-build)."
    # Fall back to most recent creative-worker image if tag not found
    if ! docker image inspect "${IMAGE_TAG}" &>/dev/null; then
        IMAGE_TAG="creative-worker:latest"
        echo "[BATCH_VERIFY] Using fallback image: ${IMAGE_TAG}"
    fi
fi

# ─── Resolve PSD names arg ────────────────────────────────────────────────────

PSD_NAMES_ARG=""
if [[ -n "${PSD_NAMES}" ]]; then
    PSD_NAMES_ARG="--psd-names ${PSD_NAMES}"
fi

# ─── Dry-run pass ─────────────────────────────────────────────────────────────

echo ""
echo "[BATCH_VERIFY] ── PHASE 1: Dry Run (FakeBackgroundProvider) ──────────────"

DRY_RUN_OUTPUT="/tmp/stage21-batch-output"
DRY_EXIT=0

docker run \
    --rm \
    --name "${CONTAINER_NAME}-dry" \
    -v "${HOST_PSD_DIR}:/app/test_psd:ro" \
    -v "${HOST_OUTPUT_DIR}:/app/output" \
    -e OUTPUT_DIR=/app/storage/outputs \
    -e AI_WORK_DIR=/app/storage/work \
    -e PSD_OBJECT_ANALYSIS_ENABLED=false \
    -e BACKGROUND_AI_MAX_ATTEMPTS=1 \
    "${IMAGE_TAG}" \
    python3 stage21_golden_batch.py \
        --psd-dir /app/test_psd \
        --output-dir /app/output/dry-run \
        --dry-run \
        ${PSD_NAMES_ARG} \
    || DRY_EXIT=$?

echo "[BATCH_VERIFY] Dry-run exit code: ${DRY_EXIT}"

if [[ ${DRY_EXIT} -eq 4 ]]; then
    echo "[ERROR] Dry-run detected cross-source contamination (exit 4)!" >&2
    echo "[ERROR] Check ${HOST_OUTPUT_DIR}/dry-run/cross-source-isolation-report.json" >&2
    exit 4
fi

if [[ ${DRY_EXIT} -ne 0 && ${DRY_EXIT} -ne 1 ]]; then
    echo "[ERROR] Dry-run failed with exit ${DRY_EXIT} (config/provider error)." >&2
    exit ${DRY_EXIT}
fi

# ─── Actual AI pass (if requested) ────────────────────────────────────────────

ACTUAL_EXIT=0

if [[ "${ACTUAL_AI}" == "true" ]]; then
    echo ""
    echo "[BATCH_VERIFY] ── PHASE 2: Actual AI (ExternalInpaintProvider) ────────"

    API_KEY_CONTENT="$(cat "${API_KEY_FILE}")"

    docker run \
        --rm \
        --name "${CONTAINER_NAME}-ai" \
        -v "${HOST_PSD_DIR}:/app/test_psd:ro" \
        -v "${HOST_OUTPUT_DIR}:/app/output" \
        -e OUTPUT_DIR=/app/storage/outputs \
        -e AI_WORK_DIR=/app/storage/work \
        -e PSD_OBJECT_ANALYSIS_ENABLED=false \
        -e BACKGROUND_AI_MAX_ATTEMPTS=1 \
        -e BACKGROUND_AI_API_KEY="${API_KEY_CONTENT}" \
        -e BACKGROUND_AI_ENABLED=true \
        "${IMAGE_TAG}" \
        python3 stage21_golden_batch.py \
            --psd-dir /app/test_psd \
            --output-dir /app/output/actual-ai \
            --actual-ai \
            ${PSD_NAMES_ARG} \
        || ACTUAL_EXIT=$?

    unset API_KEY_CONTENT  # clear from shell immediately

    echo "[BATCH_VERIFY] Actual AI exit code: ${ACTUAL_EXIT}"

    if [[ ${ACTUAL_EXIT} -eq 4 ]]; then
        echo "[ERROR] Actual AI detected cross-source contamination (exit 4)!" >&2
        echo "[ERROR] Check ${HOST_OUTPUT_DIR}/actual-ai/cross-source-isolation-report.json" >&2
        exit 4
    fi
else
    echo ""
    echo "[BATCH_VERIFY] Skipping actual AI phase (--actual-ai not specified)."
fi

# ─── Final summary ─────────────────────────────────────────────────────────────

echo ""
echo "[BATCH_VERIFY] ══════════════════════════════════════════════════════════"
echo "[BATCH_VERIFY] Dry-run exit:    ${DRY_EXIT}"
if [[ "${ACTUAL_AI}" == "true" ]]; then
    echo "[BATCH_VERIFY] Actual-AI exit:  ${ACTUAL_EXIT}"
fi
echo "[BATCH_VERIFY] Output dir:      ${HOST_OUTPUT_DIR}"

# Return worst exit code
FINAL_EXIT=0
[[ ${DRY_EXIT}    -gt ${FINAL_EXIT} ]] && FINAL_EXIT=${DRY_EXIT}
[[ ${ACTUAL_EXIT} -gt ${FINAL_EXIT} ]] && FINAL_EXIT=${ACTUAL_EXIT}

echo "[BATCH_VERIFY] Final exit code: ${FINAL_EXIT}"
exit ${FINAL_EXIT}
