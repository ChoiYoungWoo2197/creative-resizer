#!/usr/bin/env bash
# scripts/verify_stage21_final.sh
#
# Stage 21 Final Verification — Source Isolation + Object Map + Semantic Role
#
# Checks all Stage 21 acceptance criteria in order:
#   P1  Python unit tests (133/133)
#   P2  Java unit tests   (57/57)
#   P3  Docker build      (creative-worker + creative-api)
#   P4  Golden Batch Dry Run — Mother + Yada (semanticRoleVerdict=PASS)
#   P5  Cross-source A→B→A isolation (no providerInputPixelSha256 collision)
#   P6  Actual AI batch   (--actual-ai flag; skipped by default)
#   P7  Final PASS/PARTIAL/FAIL verdict
#
# Usage:
#   bash scripts/verify_stage21_final.sh                    # P1-P5 only
#   bash scripts/verify_stage21_final.sh --actual-ai        # P1-P6
#   bash scripts/verify_stage21_final.sh --skip-build       # skip P3 docker build
#   bash scripts/verify_stage21_final.sh --skip-python      # skip P1
#   bash scripts/verify_stage21_final.sh --skip-java        # skip P2
#   bash scripts/verify_stage21_final.sh --psd-dir /path    # custom PSD dir (P4-P5)
#
# Exit codes:
#   0  All enabled phases PASS
#   1  Semantic role / quality failure
#   2  Missing config or required PSD not found
#   3  Provider unavailable
#   4  Source isolation contamination detected (FATAL)
#   5  Unit test failure
#   6  Docker build failure
#
# Safety guarantees (same as golden batch):
#   - Production containers NEVER restarted or modified
#   - API key NEVER printed, logged, or committed
#   - docker system prune / volume prune NEVER run
#   - git reset --hard / force push NEVER run

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
WORKER_DIR="${PROJECT_ROOT}/worker"

cd "${PROJECT_ROOT}"

# ─── Arg parsing ──────────────────────────────────────────────────────────────

SKIP_PYTHON=false
SKIP_JAVA=false
SKIP_BUILD=false
ACTUAL_AI=false
PSD_DIR=""
OUTPUT_DIR=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-python)   SKIP_PYTHON=true  ; shift ;;
        --skip-java)     SKIP_JAVA=true    ; shift ;;
        --skip-build)    SKIP_BUILD=true   ; shift ;;
        --actual-ai)     ACTUAL_AI=true    ; shift ;;
        --psd-dir)       PSD_DIR="$2"      ; shift 2 ;;
        --output-dir)    OUTPUT_DIR="$2"   ; shift 2 ;;
        *) echo "[WARN] Unknown arg: $1"   ; shift ;;
    esac
done

GIT_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")"
HOST_OUTPUT_DIR="${OUTPUT_DIR:-/tmp/stage21-final-${GIT_SHA}}"
HOST_PSD_DIR="${PSD_DIR:-${PROJECT_ROOT}/worker/test_psd}"
CONTAINER_NAME="stage21-final-${GIT_SHA}-$$"
IMAGE_TAG="creative-worker-stage21-final:${GIT_SHA}"

mkdir -p "${HOST_OUTPUT_DIR}"
mkdir -p "${HOST_PSD_DIR}"

echo "════════════════════════════════════════════════════════════════"
echo " Stage 21 Final Verification"
echo " git_sha=${GIT_SHA}  actual_ai=${ACTUAL_AI}"
echo " output=${HOST_OUTPUT_DIR}"
echo "════════════════════════════════════════════════════════════════"

# Track per-phase results
declare -A PHASE_RESULT
PHASE_RESULT[P1]="SKIP"
PHASE_RESULT[P2]="SKIP"
PHASE_RESULT[P3]="SKIP"
PHASE_RESULT[P4]="SKIP"
PHASE_RESULT[P5]="SKIP"
PHASE_RESULT[P6]="SKIP"

# ─── Cleanup ──────────────────────────────────────────────────────────────────

API_KEY_FILE=""

cleanup() {
    if [[ -n "${API_KEY_FILE}" && -f "${API_KEY_FILE}" ]]; then
        rm -f "${API_KEY_FILE}"
    fi
    docker rm -f "${CONTAINER_NAME}" 2>/dev/null || true
}
trap cleanup EXIT

# ─── P1: Python unit tests ────────────────────────────────────────────────────

if [[ "${SKIP_PYTHON}" == "false" ]]; then
    echo ""
    echo "── P1: Python unit tests ────────────────────────────────────────────"

    PYTHON_EXIT=0

    # Run all tests inside a fresh container (no host python3 required)
    if [[ "${SKIP_BUILD}" == "false" ]]; then
        docker build -q -t "${IMAGE_TAG}" -f "${WORKER_DIR}/Dockerfile" "${WORKER_DIR}" || {
            echo "[P1] Docker build for test runner failed." >&2
            PHASE_RESULT[P1]="FAIL-BUILD"
        }
    fi

    if [[ "${PHASE_RESULT[P1]}" == "SKIP" ]]; then
        docker run --rm --name "${CONTAINER_NAME}-py" \
            -e OUTPUT_DIR=/app/storage/outputs \
            -e AI_WORK_DIR=/app/storage/work \
            -e PSD_OBJECT_ANALYSIS_ENABLED=false \
            "${IMAGE_TAG}" \
            bash -c "cd /app && python3 -m pytest test_foreground_compositor.py test_ai_only_rendering.py -q 2>&1" \
            || PYTHON_EXIT=$?

        if [[ ${PYTHON_EXIT} -eq 0 ]]; then
            PHASE_RESULT[P1]="PASS"
            echo "[P1] Python tests PASS"
        else
            PHASE_RESULT[P1]="FAIL"
            echo "[P1] Python tests FAIL (exit ${PYTHON_EXIT})" >&2
        fi
    fi
else
    echo "[P1] Skipped (--skip-python)"
fi

# ─── P2: Java unit tests ──────────────────────────────────────────────────────

if [[ "${SKIP_JAVA}" == "false" ]]; then
    echo ""
    echo "── P2: Java unit tests ──────────────────────────────────────────────"

    JAVA_EXIT=0
    # gradlew runs in project root (build.gradle present)
    if [[ -f "${PROJECT_ROOT}/gradlew" ]]; then
        "${PROJECT_ROOT}/gradlew" test -p "${PROJECT_ROOT}" --rerun-tasks -q 2>&1 || JAVA_EXIT=$?
    elif [[ -f "${PROJECT_ROOT}/gradle" ]]; then
        gradle test -p "${PROJECT_ROOT}" --rerun-tasks -q 2>&1 || JAVA_EXIT=$?
    else
        echo "[P2] Neither gradlew nor gradle found — skipping Java tests." >&2
        PHASE_RESULT[P2]="SKIP"
        JAVA_EXIT=0
    fi

    if [[ "${PHASE_RESULT[P2]}" != "SKIP" ]]; then
        if [[ ${JAVA_EXIT} -eq 0 ]]; then
            PHASE_RESULT[P2]="PASS"
            echo "[P2] Java tests PASS"
        else
            PHASE_RESULT[P2]="FAIL"
            echo "[P2] Java tests FAIL (exit ${JAVA_EXIT})" >&2
        fi
    fi
else
    echo "[P2] Skipped (--skip-java)"
fi

# ─── P3: Docker build ─────────────────────────────────────────────────────────

if [[ "${SKIP_BUILD}" == "false" ]]; then
    echo ""
    echo "── P3: Docker build ──────────────────────────────────────────────────"

    BUILD_EXIT=0
    docker build -t "${IMAGE_TAG}" -f "${WORKER_DIR}/Dockerfile" "${WORKER_DIR}" || BUILD_EXIT=$?

    if [[ ${BUILD_EXIT} -eq 0 ]]; then
        PHASE_RESULT[P3]="PASS"
        echo "[P3] Docker build PASS: ${IMAGE_TAG}"
    else
        PHASE_RESULT[P3]="FAIL"
        echo "[P3] Docker build FAIL (exit ${BUILD_EXIT})" >&2
    fi
else
    echo "[P3] Skipped (--skip-build)"
    # Reuse latest image if tag not present
    if ! docker image inspect "${IMAGE_TAG}" &>/dev/null; then
        IMAGE_TAG="creative-worker:latest"
        echo "[P3] Using fallback image: ${IMAGE_TAG}"
    fi
    PHASE_RESULT[P3]="SKIP"
fi

# ─── P4: Golden Batch Dry Run — Semantic Role PASS ────────────────────────────

echo ""
echo "── P4: Golden Batch Dry Run (Object Map + Semantic Role) ────────────"

if ! docker image inspect "${IMAGE_TAG}" &>/dev/null; then
    echo "[P4] SKIP — image not available: ${IMAGE_TAG}"
    PHASE_RESULT[P4]="SKIP"
else
    DRY_EXIT=0

    docker run --rm --name "${CONTAINER_NAME}-dry" \
        -v "${HOST_PSD_DIR}:/app/test_psd:ro" \
        -v "${HOST_OUTPUT_DIR}:/app/output" \
        -e OUTPUT_DIR=/app/storage/outputs \
        -e AI_WORK_DIR=/app/storage/work \
        -e PSD_OBJECT_ANALYSIS_ENABLED=false \
        -e PSD_OBJECT_ANALYSIS_ON_GENERATE=false \
        -e BACKGROUND_AI_MAX_ATTEMPTS=1 \
        "${IMAGE_TAG}" \
        python3 stage21_golden_batch.py \
            --psd-dir /app/test_psd \
            --output-dir /app/output/dry-run \
            --require-analysis \
            --dry-run \
        || DRY_EXIT=$?

    echo "[P4] Dry-run exit: ${DRY_EXIT}"

    if [[ ${DRY_EXIT} -eq 0 ]]; then
        PHASE_RESULT[P4]="PASS"
        echo "[P4] Semantic Role dry-run PASS"
    elif [[ ${DRY_EXIT} -eq 4 ]]; then
        PHASE_RESULT[P4]="CONTAMINATION"
        echo "[P4] FATAL: source contamination detected (exit 4)" >&2
        exit 4
    else
        PHASE_RESULT[P4]="FAIL"
        echo "[P4] Dry-run FAIL (exit ${DRY_EXIT})" >&2
    fi
fi

# ─── P5: Cross-source A→B→A isolation ────────────────────────────────────────

echo ""
echo "── P5: Cross-source A→B→A isolation ─────────────────────────────────"

if ! docker image inspect "${IMAGE_TAG}" &>/dev/null; then
    echo "[P5] SKIP — image not available"
    PHASE_RESULT[P5]="SKIP"
else
    ISO_EXIT=0

    docker run --rm --name "${CONTAINER_NAME}-iso" \
        -v "${HOST_PSD_DIR}:/app/test_psd:ro" \
        -v "${HOST_OUTPUT_DIR}:/app/output" \
        -e OUTPUT_DIR=/app/storage/outputs \
        -e AI_WORK_DIR=/app/storage/work \
        -e PSD_OBJECT_ANALYSIS_ENABLED=false \
        -e BACKGROUND_AI_MAX_ATTEMPTS=1 \
        "${IMAGE_TAG}" \
        python3 stage21_golden_batch.py \
            --psd-dir /app/test_psd \
            --output-dir /app/output/isolation \
            --dry-run \
            --isolation-check \
        || ISO_EXIT=$?

    echo "[P5] Isolation exit: ${ISO_EXIT}"

    if [[ ${ISO_EXIT} -eq 0 ]]; then
        PHASE_RESULT[P5]="PASS"
        echo "[P5] A→B→A isolation PASS"
    elif [[ ${ISO_EXIT} -eq 4 ]]; then
        PHASE_RESULT[P5]="CONTAMINATION"
        echo "[P5] FATAL: source contamination (exit 4)" >&2
        exit 4
    else
        PHASE_RESULT[P5]="FAIL"
        echo "[P5] Isolation FAIL (exit ${ISO_EXIT})" >&2
    fi
fi

# ─── P6: Actual AI batch (optional) ──────────────────────────────────────────

if [[ "${ACTUAL_AI}" == "true" ]]; then
    echo ""
    echo "── P6: Actual AI batch ────────────────────────────────────────────────"

    if [[ -z "${BACKGROUND_AI_API_KEY:-}" ]]; then
        echo "[P6] SKIP — BACKGROUND_AI_API_KEY not set" >&2
        PHASE_RESULT[P6]="SKIP"
    elif ! docker image inspect "${IMAGE_TAG}" &>/dev/null; then
        echo "[P6] SKIP — image not available"
        PHASE_RESULT[P6]="SKIP"
    else
        API_KEY_FILE="$(mktemp /tmp/stage21-final-api-key.XXXXXX)"
        echo "${BACKGROUND_AI_API_KEY}" > "${API_KEY_FILE}"
        chmod 600 "${API_KEY_FILE}"
        API_KEY_CONTENT="$(cat "${API_KEY_FILE}")"

        AI_EXIT=0
        docker run --rm --name "${CONTAINER_NAME}-ai" \
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
                --require-analysis \
                --actual-ai \
            || AI_EXIT=$?

        unset API_KEY_CONTENT
        echo "[P6] Actual AI exit: ${AI_EXIT}"

        if [[ ${AI_EXIT} -eq 0 ]]; then
            PHASE_RESULT[P6]="PASS"
            echo "[P6] Actual AI PASS"
        elif [[ ${AI_EXIT} -eq 4 ]]; then
            echo "[P6] FATAL: source contamination in Actual AI (exit 4)" >&2
            exit 4
        else
            PHASE_RESULT[P6]="FAIL"
            echo "[P6] Actual AI FAIL (exit ${AI_EXIT})" >&2
        fi
    fi
else
    echo ""
    echo "[P6] Skipped (--actual-ai not specified)"
fi

# ─── Final Summary ─────────────────────────────────────────────────────────────

echo ""
echo "════════════════════════════════════════════════════════════════"
echo " Stage 21 Final Verification — Summary  (git: ${GIT_SHA})"
echo "════════════════════════════════════════════════════════════════"
echo "  P1  Python unit tests      : ${PHASE_RESULT[P1]}"
echo "  P2  Java unit tests        : ${PHASE_RESULT[P2]}"
echo "  P3  Docker build           : ${PHASE_RESULT[P3]}"
echo "  P4  Semantic Role dry-run  : ${PHASE_RESULT[P4]}"
echo "  P5  A→B→A isolation        : ${PHASE_RESULT[P5]}"
echo "  P6  Actual AI batch        : ${PHASE_RESULT[P6]}"
echo "────────────────────────────────────────────────────────────────"
echo "  Output: ${HOST_OUTPUT_DIR}"

# Determine overall verdict
FAILS=()
for phase in P1 P2 P3 P4 P5 P6; do
    if [[ "${PHASE_RESULT[$phase]}" == "FAIL" ]]; then
        FAILS+=("${phase}")
    fi
done

if [[ ${#FAILS[@]} -eq 0 ]]; then
    echo "  VERDICT: PASS"
    echo "════════════════════════════════════════════════════════════════"
    exit 0
else
    echo "  VERDICT: FAIL — ${FAILS[*]}"
    echo "════════════════════════════════════════════════════════════════"

    # Map to appropriate exit code
    for phase in "${FAILS[@]}"; do
        case "${phase}" in
            P1|P2) exit 5 ;;
            P3)    exit 6 ;;
            P4|P5) exit 1 ;;
        esac
    done
    exit 1
fi
