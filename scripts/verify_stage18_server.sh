#!/usr/bin/env bash
# ==========================================================================
# verify_stage18_server.sh
# Stage 18 운영서버 검증 — 외부 GroundingDINO + SAM 2 실제 모델 검증
#
# 사용법 (프로젝트 루트 또는 scripts/ 어디서든 실행 가능):
#   cd /opt/creative-resizer
#   bash scripts/verify_stage18_server.sh
#
# 안전 원칙:
#   - 운영 컨테이너 (creative-nginx/api/worker) 건드리지 않음
#   - 운영 이미지 latest 태그 덮어쓰기 금지
#   - compareOnly=true 강제 유지 (기존 결과 교체 금지)
#   - 테스트 컨테이너/네트워크는 trap으로 반드시 정리
#   - docker compose down 금지
#
# Exit code:
#   0 = PASS
#   2 = PARTIAL
#   1 = FAIL
# ==========================================================================
set -Eeuo pipefail

# ── 0-A: 경로 확정 ────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

# ── 0-B: 상수 ─────────────────────────────────────────────────────────────────
GIT_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo "nogit")"
TIMESTAMP="$(date '+%Y%m%d_%H%M%S')"

ARTIFACT_DIR="${PROJECT_ROOT}/test-artifacts/stage18/${TIMESTAMP}"
EXEC_LOG="${ARTIFACT_DIR}/execution.log"

SEG_IMAGE="creative-segmentation-ai-stage18-test:${GIT_SHA}"
NETWORK_NAME="creative-stage18-test-${GIT_SHA}"
CONTAINER_NAME="creative-segmentation-stage18-${GIT_SHA}"
TEST_PORT=48090                   # localhost:48090 — 외부 미노출
SERVICE_URL="http://localhost:${TEST_PORT}"

# 모델 캐시 — 운영 segmentation 서비스와 공유하지 않는 별도 경로
MODEL_CACHE_DIR="${PROJECT_ROOT}/model-cache/stage18"

SAMPLE_BASE="${PROJECT_ROOT}/test-assets/stage18"

HEALTH_RETRY_MAX=120              # 120 × 5s = 10분 (초기 모델 다운로드 고려)
HEALTH_RETRY_INTERVAL=5

# ── 0-C: 아티팩트 디렉토리 준비 ───────────────────────────────────────────────
mkdir -p "${ARTIFACT_DIR}"
touch "${EXEC_LOG}"

# ── 0-D: 색상·로그 함수 ───────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; NC='\033[0m'

info()    { echo -e "${BLUE}[INFO]${NC}  $*"    | tee -a "${EXEC_LOG}"; }
ok()      { echo -e "${GREEN}[OK]${NC}    $*"   | tee -a "${EXEC_LOG}"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"   | tee -a "${EXEC_LOG}"; }
err()     { echo -e "${RED}[FAIL]${NC}  $*"     | tee -a "${EXEC_LOG}"; }
section() { echo -e "\n${CYAN}══ $* ══${NC}"    | tee -a "${EXEC_LOG}"; }

# ── 0-E: 상태 추적 변수 ────────────────────────────────────────────────────────
CONTAINER_STARTED=false
NETWORK_CREATED=false
FINAL_EXIT=2                      # default: PARTIAL
VERDICT="PARTIAL"
declare -a VERDICT_REASONS

# ── 0-F: Cleanup trap ─────────────────────────────────────────────────────────
cleanup() {
    local rc=$?
    set +e
    echo "" | tee -a "${EXEC_LOG}"
    section "Cleanup (trap)"
    if [[ "${CONTAINER_STARTED}" == "true" ]]; then
        docker stop "${CONTAINER_NAME}"  2>/dev/null | tee -a "${EXEC_LOG}" || true
        docker rm -f "${CONTAINER_NAME}" 2>/dev/null | tee -a "${EXEC_LOG}" || true
        info "테스트 컨테이너 정리: ${CONTAINER_NAME}"
    fi
    if [[ "${NETWORK_CREATED}" == "true" ]]; then
        docker network rm "${NETWORK_NAME}" 2>/dev/null | tee -a "${EXEC_LOG}" || true
        info "테스트 네트워크 정리: ${NETWORK_NAME}"
    fi
    # 잔존 확인
    if docker ps -a --format "{{.Names}}" 2>/dev/null | grep -q "${CONTAINER_NAME}"; then
        docker rm -f "${CONTAINER_NAME}" 2>/dev/null || true
    fi
    if docker network ls --format "{{.Name}}" 2>/dev/null | grep -q "${NETWORK_NAME}"; then
        docker network rm "${NETWORK_NAME}" 2>/dev/null || true
    fi
    info "Cleanup 완료"
    exit "${rc}"
}
trap cleanup EXIT INT TERM

# ==========================================================================
# Step 1: 사전 환경 검사
# ==========================================================================
section "Step 1: 사전 환경 검사"

{
    echo "verify_stage18_server.sh started: ${TIMESTAMP}"
    echo "Project root: ${PROJECT_ROOT}"
    echo "Git SHA:      ${GIT_SHA}"
    echo "Host:         $(hostname)"
} >> "${EXEC_LOG}"

# Git
if ! git rev-parse --git-dir >/dev/null 2>&1; then
    err "Git 저장소 없음"; VERDICT="FAIL"; FINAL_EXIT=1; exit 1
fi
ok "Git: ${GIT_SHA}"

# 필수 디렉토리
for d in "services/segmentation-ai" "worker"; do
    if [[ -d "${PROJECT_ROOT}/${d}" ]]; then ok "Dir OK: ${d}"; else
        err "Dir 없음: ${d}"; VERDICT="FAIL"; FINAL_EXIT=1; exit 1
    fi
done

# Docker
if ! docker info >/dev/null 2>&1; then
    err "Docker daemon 응답 없음"; VERDICT="FAIL"; FINAL_EXIT=1; exit 1
fi
DOCKER_VERSION=$(docker version --format '{{.Server.Version}}' 2>/dev/null || echo "unknown")
ok "Docker: ${DOCKER_VERSION}"

# python3
PYTHON_CMD=""
for py in python3 python; do
    if command -v "${py}" >/dev/null 2>&1 && "${py}" -c "import sys; assert sys.version_info >= (3,8)" 2>/dev/null; then
        PYTHON_CMD="${py}"
        ok "Python: $(${py} --version 2>&1)"
        break
    fi
done
if [[ -z "${PYTHON_CMD}" ]]; then
    warn "python3 없음 — JSON 파싱·이미지 분석 단계 제한"
fi

# CUDA (빠른 확인)
CUDA_AVAILABLE=false
CUDA_DEVICE="N/A"
if command -v nvidia-smi >/dev/null 2>&1; then
    CUDA_DEVICE=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 || echo "")
    if [[ -n "${CUDA_DEVICE}" ]]; then
        CUDA_AVAILABLE=true
        ok "CUDA: ${CUDA_DEVICE}"
    fi
fi
info "CUDA 사용 가능: ${CUDA_AVAILABLE}"

GPU_FLAGS=""
if [[ "${CUDA_AVAILABLE}" == "true" ]]; then
    GPU_FLAGS="--gpus all"
fi

# ==========================================================================
# Step 2: 운영 컨테이너 스냅샷 (before)
# ==========================================================================
section "Step 2: 운영 컨테이너 스냅샷 (before)"

docker ps --format "table {{.Names}}\t{{.Image}}\t{{.Status}}" \
    > "${ARTIFACT_DIR}/docker-before.txt" 2>&1
cat "${ARTIFACT_DIR}/docker-before.txt" | tee -a "${EXEC_LOG}"

PROD_CONTAINERS_BEFORE=$(docker ps --format "{{.Names}}" 2>/dev/null || echo "")
for cname in creative-nginx creative-api creative-worker; do
    if echo "${PROD_CONTAINERS_BEFORE}" | grep -q "^${cname}$"; then
        ok "운영 컨테이너 실행 중: ${cname}"
    else
        warn "운영 컨테이너 없음 (정상일 수도): ${cname}"
    fi
done

# ==========================================================================
# Step 3: 테스트 샘플 탐색
# ==========================================================================
section "Step 3: 테스트 샘플 탐색"

SAMPLE_PATH=""
SAMPLE_STATUS="FILE_MISSING"
mkdir -p "${SAMPLE_BASE}"

# 우선순위 탐색
for ext in png psd jpg jpeg; do
    candidate="${SAMPLE_BASE}/mother-hand-product.${ext}"
    if [[ -f "${candidate}" ]]; then
        SAMPLE_PATH="${candidate}"
        SAMPLE_STATUS="FOUND"
        ok "샘플 발견: ${candidate} ($(du -sh "${candidate}" | cut -f1))"
        break
    fi
done

# 대체 샘플 (디렉토리 내 아무 이미지)
if [[ -z "${SAMPLE_PATH}" ]]; then
    first_img=$(find "${SAMPLE_BASE}" -maxdepth 1 -type f \
        \( -name "*.png" -o -name "*.jpg" -o -name "*.jpeg" -o -name "*.psd" \) 2>/dev/null | head -1 || echo "")
    if [[ -n "${first_img}" ]]; then
        SAMPLE_PATH="${first_img}"
        SAMPLE_STATUS="FOUND_ALTERNATIVE"
        warn "mother-hand-product 없음 — 대체 샘플 사용: $(basename "${first_img}")"
    fi
fi

if [[ "${SAMPLE_STATUS}" == "FILE_MISSING" ]]; then
    warn "샘플 없음 (FILE_MISSING) — Docker/model 검증은 계속 진행"
    VERDICT_REASONS+=("핵심 샘플 FILE_MISSING — handLeakRisk 미검증")
fi

# ==========================================================================
# Step 4: 테스트 이미지 빌드
# ==========================================================================
section "Step 4: 테스트 이미지 빌드 (${SEG_IMAGE})"

BUILD_LOG="${ARTIFACT_DIR}/build-segmentation-ai.log"
BUILD_SUCCESS=false

if docker build \
    -t "${SEG_IMAGE}" \
    "${PROJECT_ROOT}/services/segmentation-ai" \
    > "${BUILD_LOG}" 2>&1; then
    BUILD_SUCCESS=true
    ok "이미지 빌드 성공: ${SEG_IMAGE}"
else
    err "이미지 빌드 실패"
    tail -20 "${BUILD_LOG}" | tee -a "${EXEC_LOG}"
    VERDICT="FAIL"; FINAL_EXIT=1; exit 1
fi

# GroundingDINO / SAM2 패키지 존재 확인
for pkg in transformers sam2; do
    if docker run --rm "${SEG_IMAGE}" python3 -c "import ${pkg}; print('OK')" 2>/dev/null | grep -q "OK"; then
        ok "패키지 존재: ${pkg}"
    else
        warn "패키지 확인 실패: ${pkg}"
    fi
done

# ==========================================================================
# Step 5: 테스트 네트워크 + 컨테이너 시작
# ==========================================================================
section "Step 5: 테스트 네트워크·컨테이너 시작"

mkdir -p "${MODEL_CACHE_DIR}"
docker network create "${NETWORK_NAME}" >/dev/null 2>&1 && NETWORK_CREATED=true
ok "테스트 네트워크: ${NETWORK_NAME}"

info "컨테이너 시작: ${CONTAINER_NAME} (port=127.0.0.1:${TEST_PORT}, PRELOAD_MODELS=true)"
docker run -d \
    --name "${CONTAINER_NAME}" \
    --network "${NETWORK_NAME}" \
    -p "127.0.0.1:${TEST_PORT}:8090" \
    -v "${MODEL_CACHE_DIR}:/models" \
    ${GPU_FLAGS} \
    -e PRELOAD_MODELS=true \
    -e CREATIVE_SEGMENTATION_DEVICE=auto \
    -e CREATIVE_EXTERNAL_SEGMENTATION_ENABLED=true \
    -e CREATIVE_EXTERNAL_SEGMENTATION_COMPARE_ONLY=true \
    -e CREATIVE_SEGMENTATION_MAX_IMAGE_SIDE=1280 \
    -e CREATIVE_SEGMENTATION_MIN_CONFIDENCE=0.25 \
    -e CREATIVE_SEGMENTATION_MASK_SCORE_THRESHOLD=70 \
    "${SEG_IMAGE}" \
    > /dev/null 2>&1
CONTAINER_STARTED=true
ok "컨테이너 시작됨 (ID=$(docker ps -q -f name="${CONTAINER_NAME}"))"

# ==========================================================================
# Step 6: Health check (최대 ${HEALTH_RETRY_MAX} × ${HEALTH_RETRY_INTERVAL}s)
# ==========================================================================
section "Step 6: Health check (최대 $((HEALTH_RETRY_MAX * HEALTH_RETRY_INTERVAL))s)"

HEALTH_JSON="${ARTIFACT_DIR}/health.json"
HEALTH_OK=false
LAST_STATUS="(pending)"

for i in $(seq 1 "${HEALTH_RETRY_MAX}"); do
    http_code=$(curl -s -o "${HEALTH_JSON}" -w "%{http_code}" \
        --max-time 8 \
        "${SERVICE_URL}/health" 2>/dev/null || echo "000")

    if [[ "${http_code}" == "200" ]] && [[ -f "${HEALTH_JSON}" ]]; then
        if [[ -n "${PYTHON_CMD}" ]]; then
            LAST_STATUS=$(${PYTHON_CMD} -c "
import json,sys
try:
    d=json.load(open('${HEALTH_JSON}'))
    print(d.get('status','?'))
except: print('parse_error')
" 2>/dev/null || echo "?")
        else
            LAST_STATUS=$(grep -o '"status":"[^"]*"' "${HEALTH_JSON}" 2>/dev/null | head -1 | cut -d'"' -f4 || echo "?")
        fi

        if [[ "${LAST_STATUS}" == "ok" ]]; then
            HEALTH_OK=true
            ok "Health OK (시도 ${i}/${HEALTH_RETRY_MAX})"
            break
        else
            # 매 6번째(30s)마다 로그 출력
            if (( i % 6 == 0 )); then
                info "Health: status=${LAST_STATUS} — 모델 로딩 중 (${i}/${HEALTH_RETRY_MAX})"
                docker logs "${CONTAINER_NAME}" 2>&1 | tail -3 | tee -a "${EXEC_LOG}" || true
            fi
        fi
    else
        if (( i % 6 == 0 )); then
            info "HTTP ${http_code} (${i}/${HEALTH_RETRY_MAX})"
        fi
    fi
    sleep "${HEALTH_RETRY_INTERVAL}"
done

if [[ "${HEALTH_OK}" != "true" ]]; then
    err "Health check 실패 (최대 시도 초과, last_status=${LAST_STATUS})"
    docker logs "${CONTAINER_NAME}" 2>&1 | tail -30 | tee -a "${EXEC_LOG}" || true
    VERDICT="FAIL"; FINAL_EXIT=1; exit 1
fi

cat "${HEALTH_JSON}" >> "${EXEC_LOG}"

# ==========================================================================
# Step 7: 모델 정보 파싱
# ==========================================================================
section "Step 7: 모델 정보 파싱"

parse_json() {
    local file="$1" field="$2" default="${3:-N/A}"
    if [[ -n "${PYTHON_CMD}" ]]; then
        ${PYTHON_CMD} -c "
import json
try:
    d=json.load(open('${file}'))
    v=d.get('${field}')
    print(v if v is not None else '${default}')
except: print('${default}')
" 2>/dev/null || echo "${default}"
    else
        grep -o "\"${field}\":[^,}]*" "${file}" 2>/dev/null | head -1 | sed 's/.*: *//;s/[",]//g' || echo "${default}"
    fi
}

REAL_INFERENCE=$(parse_json "${HEALTH_JSON}" "realInferenceAvailable" "false")
GDINO_MODEL_ID=$(parse_json "${HEALTH_JSON}" "groundingDinoModelId")
SAM2_MODEL_ID=$(parse_json "${HEALTH_JSON}" "sam2ModelId")
SEG_DEVICE=$(parse_json "${HEALTH_JSON}" "device")
BBOX_FALLBACK_ENABLED=$(parse_json "${HEALTH_JSON}" "bboxFallbackEnabled")

info "realInferenceAvailable: ${REAL_INFERENCE}"
info "groundingDinoModelId:   ${GDINO_MODEL_ID}"
info "sam2ModelId:            ${SAM2_MODEL_ID}"
info "device:                 ${SEG_DEVICE}"
info "bboxFallbackEnabled:    ${BBOX_FALLBACK_ENABLED}"

REAL_INFERENCE_OK=false
if [[ "${REAL_INFERENCE}" == "true" || "${REAL_INFERENCE}" == "True" ]]; then
    ok "실제 GDINO + SAM2 추론 경로 확인"
    REAL_INFERENCE_OK=true
else
    warn "실제 추론 불가 — bbox fallback 모드"
    VERDICT_REASONS+=("externalModelRealInference=false (bbox fallback)")
fi

# ==========================================================================
# Step 8: 샘플 검증
# ==========================================================================
section "Step 8: 어머니 손+제품 샘플 검증"

GDINO_DETECTED="N/A"
SAM2_GENERATED="N/A"
BBOX_FALLBACK_USED="N/A"
EXT_MASK_SCORE="N/A"
HAND_LEAK="N/A"
BG_LEAK="N/A"
MASK_SOURCE="N/A"
PRODUCT_COMPLETENESS="N/A"
EDGE_SHARPNESS="N/A"
SEG_VERDICT="FILE_MISSING"
SEGMENT_JSON="${ARTIFACT_DIR}/segmentation.json"

if [[ -n "${SAMPLE_PATH}" ]]; then
    info "샘플 검증: ${SAMPLE_PATH}"

    # PSD → PNG 변환 시도
    IMAGE_PATH="${SAMPLE_PATH}"
    if [[ "${SAMPLE_PATH}" == *.psd ]]; then
        PNG_TMP="${ARTIFACT_DIR}/sample_converted.png"
        if [[ -n "${PYTHON_CMD}" ]] && \
           ${PYTHON_CMD} -c "from psd_tools import PSDImage; img=PSDImage.open('${SAMPLE_PATH}'); img.composite().save('${PNG_TMP}')" 2>/dev/null; then
            IMAGE_PATH="${PNG_TMP}"
            ok "PSD → PNG 변환 완료: ${PNG_TMP}"
        else
            warn "PSD 변환 실패 (psd-tools 없음) — 원본 파일로 업로드 시도"
        fi
    fi

    # 원본 복사
    cp "${IMAGE_PATH}" "${ARTIFACT_DIR}/original.png" 2>/dev/null || true

    # 프롬프트 파일 생성 (quoting 문제 방지)
    PROMPTS_FILE=$(mktemp /tmp/stage18_prompts_XXXXXX.json)
    cat > "${PROMPTS_FILE}" << 'PROMPTS_EOF'
[
  {"role":"product","texts":["cosmetic tube","skincare product","cosmetic product","cream tube","product bottle","cosmetic bottle","serum bottle"]},
  {"role":"hand","texts":["hand","hands","finger","fingers"]},
  {"role":"person","texts":["person","woman","model"]}
]
PROMPTS_EOF

    # /v1/segment 호출
    info "POST /v1/segment ..."
    HTTP_SEG=$(curl -s \
        -o "${SEGMENT_JSON}" \
        -w "%{http_code}" \
        --max-time 180 \
        -X POST "${SERVICE_URL}/v1/segment" \
        -F "image=@${IMAGE_PATH}" \
        -F "prompts=<${PROMPTS_FILE}" \
        -F "requestId=stage18-server-${TIMESTAMP}" \
        -F "sourceType=stage18-mother-hand" \
        2>/dev/null || echo "000")
    rm -f "${PROMPTS_FILE}"

    if [[ "${HTTP_SEG}" == "200" ]]; then
        ok "Segment 응답: HTTP 200"

        # Python helper 호출 (분석 + debug 이미지 생성)
        if [[ -n "${PYTHON_CMD}" ]] && [[ -f "${SCRIPT_DIR}/verify_stage18_server.py" ]]; then
            ${PYTHON_CMD} "${SCRIPT_DIR}/verify_stage18_server.py" \
                --segment-json "${SEGMENT_JSON}" \
                --health-json "${HEALTH_JSON}" \
                --image-path "${ARTIFACT_DIR}/original.png" \
                --output-dir "${ARTIFACT_DIR}" \
                2>>"${EXEC_LOG}" | tee "${ARTIFACT_DIR}/python-analysis.txt" | tee -a "${EXEC_LOG}" || true

            # 분석 결과 파싱
            ANALYSIS_JSON="${ARTIFACT_DIR}/stage18-server-report.json"
            if [[ -f "${ANALYSIS_JSON}" ]]; then
                GDINO_DETECTED=$(parse_json "${ANALYSIS_JSON}" "groundingDinoDetected" "false")
                SAM2_GENERATED=$(parse_json "${ANALYSIS_JSON}" "sam2MaskGenerated" "false")
                BBOX_FALLBACK_USED=$(parse_json "${ANALYSIS_JSON}" "bboxFallbackUsed" "false")
                EXT_MASK_SCORE=$(parse_json "${ANALYSIS_JSON}" "externalMaskScore" "0")
                HAND_LEAK=$(parse_json "${ANALYSIS_JSON}" "handLeakRisk" "N/A")
                BG_LEAK=$(parse_json "${ANALYSIS_JSON}" "backgroundLeakRisk" "N/A")
                MASK_SOURCE=$(parse_json "${ANALYSIS_JSON}" "maskSource")
                PRODUCT_COMPLETENESS=$(parse_json "${ANALYSIS_JSON}" "productCompleteness")
                EDGE_SHARPNESS=$(parse_json "${ANALYSIS_JSON}" "edgeSharpness" "N/A")
                SEG_VERDICT=$(parse_json "${ANALYSIS_JSON}" "segmentationVerdict" "N/A")
            fi
        else
            # Python 없을 때 기본 파싱
            if [[ -n "${PYTHON_CMD}" ]]; then
                DET_COUNT=$(${PYTHON_CMD} -c "
import json
d=json.load(open('${SEGMENT_JSON}'))
dets=d.get('detections',[])
prods=[x for x in dets if x.get('role')=='product']
print(len(prods))
" 2>/dev/null || echo "0")
                [[ "${DET_COUNT}" -gt 0 ]] && GDINO_DETECTED="true" || GDINO_DETECTED="false"
                warn_field=$(${PYTHON_CMD} -c "
import json
d=json.load(open('${SEGMENT_JSON}'))
w=d.get('warnings',[])
print('true' if 'sam2_unavailable_bbox_mask_used' in w else 'false')
" 2>/dev/null || echo "false")
                BBOX_FALLBACK_USED="${warn_field}"
                [[ "${GDINO_DETECTED}" == "true" && "${BBOX_FALLBACK_USED}" == "false" ]] && SAM2_GENERATED="true" || SAM2_GENERATED="false"
            fi
        fi

    else
        err "Segment 호출 실패: HTTP ${HTTP_SEG}"
        SEG_VERDICT="FAIL_HTTP_${HTTP_SEG}"
        VERDICT_REASONS+=("segment HTTP ${HTTP_SEG}")
    fi
else
    info "샘플 없음 — Segmentation 검증 스킵"
fi

# 결과 출력
info "groundingDinoDetected:  ${GDINO_DETECTED}"
info "sam2MaskGenerated:      ${SAM2_GENERATED}"
info "bboxFallbackUsed:       ${BBOX_FALLBACK_USED}"
info "externalMaskScore:      ${EXT_MASK_SCORE}"
info "handLeakRisk:           ${HAND_LEAK}"
info "backgroundLeakRisk:     ${BG_LEAK}"
info "maskSource:             ${MASK_SOURCE}"
info "productCompleteness:    ${PRODUCT_COMPLETENESS}"
info "edgeSharpness:          ${EDGE_SHARPNESS}"

# ==========================================================================
# Step 9: Artifact 목록 확인
# ==========================================================================
section "Step 9: Artifact 목록 확인"

find "${ARTIFACT_DIR}" -maxdepth 1 -type f \
    -printf "%f\t%s bytes\n" 2>/dev/null \
    | sort | tee -a "${EXEC_LOG}" \
    || ls -la "${ARTIFACT_DIR}" 2>/dev/null | tee -a "${EXEC_LOG}"

# ==========================================================================
# Step 10: 컨테이너 정리 + 운영 환경 검사
# ==========================================================================
section "Step 10: 컨테이너 정리 및 운영 영향 검사"

# 정리 (trap도 실행하지만 명시적으로 먼저)
docker stop "${CONTAINER_NAME}" 2>/dev/null | tee -a "${EXEC_LOG}" || true
docker rm -f "${CONTAINER_NAME}" 2>/dev/null | tee -a "${EXEC_LOG}" || true
CONTAINER_STARTED=false

docker network rm "${NETWORK_NAME}" 2>/dev/null | tee -a "${EXEC_LOG}" || true
NETWORK_CREATED=false

sleep 2

# After 스냅샷
docker ps --format "table {{.Names}}\t{{.Image}}\t{{.Status}}" \
    > "${ARTIFACT_DIR}/docker-after.txt" 2>&1
cat "${ARTIFACT_DIR}/docker-after.txt" | tee -a "${EXEC_LOG}"

PROD_CONTAINERS_AFTER=$(docker ps --format "{{.Names}}" 2>/dev/null || echo "")
OPS_IMPACT=false

# before에 있던 컨테이너가 after에도 있는지 확인
while IFS= read -r cname; do
    [[ -z "${cname}" ]] && continue
    if ! echo "${PROD_CONTAINERS_AFTER}" | grep -q "^${cname}$"; then
        err "운영 컨테이너 사라짐: ${cname} — OPS IMPACT!"
        OPS_IMPACT=true
        VERDICT_REASONS+=("운영 컨테이너 ${cname} 소실")
    fi
done <<< "${PROD_CONTAINERS_BEFORE}"

# 테스트 컨테이너 잔존 확인
if docker ps -a --format "{{.Names}}" 2>/dev/null | grep -q "${CONTAINER_NAME}"; then
    err "테스트 컨테이너 잔존: ${CONTAINER_NAME}"
    docker rm -f "${CONTAINER_NAME}" 2>/dev/null || true
    OPS_IMPACT=true
fi
if docker network ls --format "{{.Name}}" 2>/dev/null | grep -q "${NETWORK_NAME}"; then
    err "테스트 네트워크 잔존: ${NETWORK_NAME}"
    docker network rm "${NETWORK_NAME}" 2>/dev/null || true
    OPS_IMPACT=true
fi

if [[ "${OPS_IMPACT}" == "false" ]]; then
    ok "운영 환경 영향 없음"
else
    VERDICT_REASONS+=("운영 영향 감지 — 자동 복구 시도됨")
fi

# ==========================================================================
# Step 11: 최종 판정
# ==========================================================================
section "Step 11: 최종 판정"

if [[ "${OPS_IMPACT}" == "true" ]]; then
    VERDICT="FAIL"; FINAL_EXIT=1
    VERDICT_REASONS+=("운영 환경 영향 → FAIL")
elif [[ "${HEALTH_OK}" != "true" ]]; then
    VERDICT="FAIL"; FINAL_EXIT=1
elif [[ "${SAMPLE_STATUS}" == "FILE_MISSING" ]]; then
    # 샘플 없어도 모델 로드 성공이면 PARTIAL
    if [[ "${REAL_INFERENCE_OK}" == "true" ]]; then
        VERDICT="PARTIAL"; FINAL_EXIT=2
    else
        VERDICT="PARTIAL"; FINAL_EXIT=2
    fi
elif [[ "${GDINO_DETECTED}" == "false" ]]; then
    VERDICT="FAIL"; FINAL_EXIT=1
    VERDICT_REASONS+=("GroundingDINO 탐지 실패")
elif [[ "${BBOX_FALLBACK_USED}" == "true" ]]; then
    VERDICT="PARTIAL"; FINAL_EXIT=2
    VERDICT_REASONS+=("SAM2 bbox fallback 사용")
else
    # 점수 기반 판정
    score_int=0
    if [[ -n "${PYTHON_CMD}" ]] && [[ "${EXT_MASK_SCORE}" != "N/A" ]]; then
        score_int=$(${PYTHON_CMD} -c "print(int(float('${EXT_MASK_SCORE}')))" 2>/dev/null || echo 0)
    fi
    if [[ "${score_int}" -ge 70 ]]; then
        VERDICT="PASS"; FINAL_EXIT=0
    else
        VERDICT="PARTIAL"; FINAL_EXIT=2
        VERDICT_REASONS+=("externalMaskScore=${EXT_MASK_SCORE} < 70")
    fi
fi

# ==========================================================================
# Step 12: 보고서 생성
# ==========================================================================
section "Step 12: 보고서 생성"

# Markdown 보고서
REPORT_MD="${ARTIFACT_DIR}/stage18-server-report.md"
{
printf "# Stage 18 운영서버 검증 보고서\n\n"
printf "- **실행일시**: %s\n" "${TIMESTAMP}"
printf "- **Git SHA**: %s\n" "${GIT_SHA}"
printf "- **검증 경로**: %s\n\n" "${PROJECT_ROOT}"

printf "## 환경\n\n"
printf "| 항목 | 결과 |\n|---|---|\n"
printf "| Docker 버전 | %s |\n" "${DOCKER_VERSION}"
printf "| CUDA 사용 가능 | %s |\n" "${CUDA_AVAILABLE}"
printf "| CUDA 디바이스 | %s |\n" "${CUDA_DEVICE}"
printf "| 테스트 이미지 | %s |\n" "${SEG_IMAGE}"
printf "| 샘플 상태 | %s |\n\n" "${SAMPLE_STATUS}"

printf "## 모델 정보\n\n"
printf "| field | actual |\n|---|---|\n"
printf "| groundingDinoModelId | %s |\n" "${GDINO_MODEL_ID}"
printf "| sam2ModelId | %s |\n" "${SAM2_MODEL_ID}"
printf "| selectedDevice | %s |\n" "${SEG_DEVICE}"
printf "| cudaAvailable | %s |\n" "${CUDA_AVAILABLE}"
printf "| externalModelRealInference | %s |\n" "${REAL_INFERENCE}"
printf "| bboxFallbackEnabled | %s |\n\n" "${BBOX_FALLBACK_ENABLED}"

printf "## 샘플 검증\n\n"
printf "| field | expected | actual |\n|---|---|---|\n"
printf "| externalModelRealInference | true | %s |\n" "${REAL_INFERENCE}"
printf "| groundingDinoDetected | true | %s |\n" "${GDINO_DETECTED}"
printf "| sam2MaskGenerated | true | %s |\n" "${SAM2_GENERATED}"
printf "| bboxFallbackUsed | false | %s |\n" "${BBOX_FALLBACK_USED}"
printf "| externalMaskScore | >=70 | %s |\n" "${EXT_MASK_SCORE}"
printf "| handLeakRisk | low | %s |\n" "${HAND_LEAK}"
printf "| backgroundLeakRisk | low | %s |\n" "${BG_LEAK}"
printf "| productCompleteness | pass | %s |\n" "${PRODUCT_COMPLETENESS}"
printf "| maskSource | real_sam2 | %s |\n" "${MASK_SOURCE}"
printf "| edgeSharpness | - | %s |\n\n" "${EDGE_SHARPNESS}"

printf "## 운영 환경 영향\n\n"
printf "| 항목 | 결과 |\n|---|---|\n"
printf "| 운영 컨테이너 영향 | %s |\n" "${OPS_IMPACT}"
printf "| 이미지 빌드 성공 | %s |\n" "${BUILD_SUCCESS}"
printf "| Health check 성공 | %s |\n" "${HEALTH_OK}"
printf "| 테스트 컨테이너 정리 | true |\n\n"

printf "## Artifact 목록\n\n"
find "${ARTIFACT_DIR}" -maxdepth 1 -type f -printf "| %f | %s bytes |\n" 2>/dev/null \
    | sort || true

printf "\n## 판정 이유\n\n"
for r in "${VERDICT_REASONS[@]+"${VERDICT_REASONS[@]}"}"; do
    printf "- %s\n" "${r}"
done

printf "\n---\n\n"
printf "## 최종 판정: **%s** (exit %d)\n\n" "${VERDICT}" "${FINAL_EXIT}"

printf "## 최종 5줄 요약\n\n"
printf "- 운영서버 SSH: 스크립트 직접 실행 (SSH 없음)\n"
printf "- 실제 GroundingDINO/SAM2: %s\n" "${REAL_INFERENCE}"
printf "- 어머니 손/제품 분리: %s\n" "${SAMPLE_STATUS}"
printf "- 운영 영향: %s\n" "${OPS_IMPACT}"
printf "- Stage 19 진행 가능: %s\n" "$([ "${VERDICT}" = "PASS" ] && echo YES || echo NO)"
} > "${REPORT_MD}"

ok "Markdown 보고서: ${REPORT_MD}"

# ==========================================================================
# Step 13: 최종 출력
# ==========================================================================
section "Step 13: 최종 결과"

echo "" | tee -a "${EXEC_LOG}"
echo "═══════════════════════════════════════════════════════════" | tee -a "${EXEC_LOG}"
case "${VERDICT}" in
    PASS)    echo -e "${GREEN}  Stage 18 최종 판정: PASS (exit 0)${NC}"    | tee -a "${EXEC_LOG}" ;;
    PARTIAL) echo -e "${YELLOW}  Stage 18 최종 판정: PARTIAL (exit 2)${NC}" | tee -a "${EXEC_LOG}" ;;
    FAIL)    echo -e "${RED}  Stage 18 최종 판정: FAIL (exit 1)${NC}"     | tee -a "${EXEC_LOG}" ;;
esac
echo "" | tee -a "${EXEC_LOG}"
echo "  Artifact: ${ARTIFACT_DIR}" | tee -a "${EXEC_LOG}"
echo "  Report:   ${REPORT_MD}"    | tee -a "${EXEC_LOG}"
echo "" | tee -a "${EXEC_LOG}"
if [[ "${#VERDICT_REASONS[@]}" -gt 0 ]]; then
    echo "  판정 이유:" | tee -a "${EXEC_LOG}"
    for r in "${VERDICT_REASONS[@]}"; do
        echo "    - ${r}" | tee -a "${EXEC_LOG}"
    done
fi
echo "  externalModelRealInference: ${REAL_INFERENCE}" | tee -a "${EXEC_LOG}"
echo "  groundingDinoDetected:      ${GDINO_DETECTED}" | tee -a "${EXEC_LOG}"
echo "  sam2MaskGenerated:          ${SAM2_GENERATED}" | tee -a "${EXEC_LOG}"
echo "  bboxFallbackUsed:           ${BBOX_FALLBACK_USED}" | tee -a "${EXEC_LOG}"
echo "  externalMaskScore:          ${EXT_MASK_SCORE}" | tee -a "${EXEC_LOG}"
echo "  handLeakRisk:               ${HAND_LEAK}" | tee -a "${EXEC_LOG}"
echo "  backgroundLeakRisk:         ${BG_LEAK}" | tee -a "${EXEC_LOG}"
echo "  selectedMaskSource:         native (compareOnly=true)" | tee -a "${EXEC_LOG}"
echo "  Stage 19 진행 가능:         $([ "${VERDICT}" = "PASS" ] && echo YES || echo NO)" | tee -a "${EXEC_LOG}"
echo "═══════════════════════════════════════════════════════════" | tee -a "${EXEC_LOG}"

exit "${FINAL_EXIT}"
