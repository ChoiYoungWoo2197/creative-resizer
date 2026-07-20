#!/usr/bin/env bash
# ==========================================================================
# verify_stage18_server.sh
# Stage 18 운영서버 검증 — 외부 GroundingDINO + SAM 2 실제 모델 검증
#
# 사용법 (프로젝트 루트 또는 scripts/ 어디서든 실행 가능):
#   cd /opt/creative-resizer
#   bash scripts/verify_stage18_server.sh
#
# 환경변수:
#   STAGE18_MIN_FREE_DISK_MB  최소 여유 공간 (기본 8192MB)
#
# 안전 원칙:
#   - 운영 컨테이너 (creative-nginx/api/worker) 건드리지 않음
#   - 운영 이미지 latest 태그 덮어쓰기 금지
#   - compareOnly=true 강제 유지 (기존 결과 교체 금지)
#   - 테스트 컨테이너/네트워크는 trap으로 반드시 정리
#   - docker compose down 금지
#   - docker system prune -a / docker volume prune 금지
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

# 모델 캐시 — 재실행 간 재사용되는 Named volume (삭제하지 않음)
HF_CACHE_VOLUME="creative-stage18-hf-cache"

SAMPLE_BASE="${PROJECT_ROOT}/test-assets/stage18"

HEALTH_RETRY_MAX=90               # 90 × 5s = 7.5분 (모델은 사전 다운로드 후 시작)
HEALTH_RETRY_INTERVAL=5

# 디스크 최소 여유 공간 (MB)
STAGE18_MIN_FREE_DISK_MB="${STAGE18_MIN_FREE_DISK_MB:-8192}"

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

# 디스크 / 패키지 검사 결과 (Step 1.5 / 4.5에서 설정)
ROOT_FREE_MB="N/A"
DOCKER_FREE_MB="N/A"
TORCH_VERSION="N/A"
TORCHVISION_VERSION="N/A"
SAM2_PKG_VERSION="N/A"
NVIDIA_PKG_COUNT="N/A"
TORCH_CUDA_AVAIL="N/A"
TORCH_VARIANT="cpu"
PSD_TOOLS_VERSION="N/A"
PSD_TOOLS_OK=false
# Stage 18.2: flattenMethod 결과
FLATTEN_METHOD="N/A"
# 모델 캐시 상태 (Step 4.7-4.9에서 설정)
GDINO_CACHE_READY=false
SAM2_CACHE_READY=false
GDINO_DOWNLOAD_OK=false
SAM2_DOWNLOAD_OK=false
# SAM2 초기화 smoke (Step 4.6)
SAM2_INIT_OK=false
SAM2_INIT_STATUS="not_run"
SAM2_CONFIG_USED="N/A"
SAM2_SMOKE_MASK="N/A"
# /ready strict check (Step 6.5)
READY_OK=false
READY_HTTP="N/A"
READY_JSON=""
# Strict 검증 정책 (true: SAM2 bbox fallback = FAIL)
STAGE18_REQUIRE_REAL_INFERENCE="${STAGE18_REQUIRE_REAL_INFERENCE:-true}"

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

# host python3 — 없어도 계속 (Python helper는 컨테이너 내부에서 실행)
PYTHON_CMD=""
for py in python3 python; do
    if command -v "${py}" >/dev/null 2>&1 && "${py}" -c "import sys; assert sys.version_info >= (3,8)" 2>/dev/null; then
        PYTHON_CMD="${py}"
        ok "Host Python: $(${py} --version 2>&1) (parse_json 가속)"
        break
    fi
done
if [[ -z "${PYTHON_CMD}" ]]; then
    info "host python3 없음 — parse_json은 grep/sed 사용 (Python helper는 컨테이너 내부 실행)"
fi

# CUDA 감지
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

# TORCH_VARIANT 자동 결정
TORCH_VARIANT="cpu"
if [[ "${CUDA_AVAILABLE}" == "true" ]]; then
    TORCH_VARIANT="gpu"
    info "CUDA 감지: TORCH_VARIANT=gpu"
else
    info "CPU 서버: TORCH_VARIANT=cpu 강제"
fi

GPU_FLAGS=""
if [[ "${CUDA_AVAILABLE}" == "true" ]]; then
    GPU_FLAGS="--gpus all"
fi

# ==========================================================================
# Step 1.5: 디스크 여유 공간 검사
# ==========================================================================
section "Step 1.5: 디스크 여유 공간 검사 (최소 ${STAGE18_MIN_FREE_DISK_MB}MB)"

ROOT_FREE_KB=$(df -Pk / | awk 'NR==2{print $4}')
ROOT_FREE_MB=$(( ROOT_FREE_KB / 1024 ))

if df -Pk /var/lib/docker >/dev/null 2>&1; then
    DOCKER_FREE_KB=$(df -Pk /var/lib/docker | awk 'NR==2{print $4}')
    DOCKER_FREE_MB=$(( DOCKER_FREE_KB / 1024 ))
else
    DOCKER_FREE_MB="${ROOT_FREE_MB}"
fi

DISK_INFO=$(docker system df 2>/dev/null || echo "")
DOCKER_IMAGES_SIZE=$(echo "${DISK_INFO}" | awk '/Images/{print $4}' 2>/dev/null || echo "N/A")
DOCKER_BUILD_CACHE=$(echo "${DISK_INFO}" | awk '/Build Cache/{print $4}' 2>/dev/null || echo "N/A")

info "루트 여유:          ${ROOT_FREE_MB}MB"
info "Docker 여유:        ${DOCKER_FREE_MB}MB"
info "Docker 이미지 합계: ${DOCKER_IMAGES_SIZE}"
info "Docker 빌드 캐시:   ${DOCKER_BUILD_CACHE}"

MIN_FREE=${STAGE18_MIN_FREE_DISK_MB}
DISK_OK=true
if [[ "${ROOT_FREE_MB}" -lt "${MIN_FREE}" || "${DOCKER_FREE_MB}" -lt "${MIN_FREE}" ]]; then
    err "디스크 여유 공간 부족: root=${ROOT_FREE_MB}MB docker=${DOCKER_FREE_MB}MB (최소 ${MIN_FREE}MB 필요)"
    warn "권장 정리 명령 (수동 실행 — 자동 실행 안 함):"
    warn "  docker image prune -f"
    warn "  docker builder prune -f --filter 'until=24h'"
    warn "절대 실행 금지: docker system prune -a / docker volume prune"
    VERDICT_REASONS+=("INSUFFICIENT_DISK: root=${ROOT_FREE_MB}MB docker=${DOCKER_FREE_MB}MB")
    VERDICT="PARTIAL"; FINAL_EXIT=2
    DISK_OK=false
fi

if [[ "${DISK_OK}" == "false" ]]; then
    # 보고서만 남기고 종료
    section "디스크 부족으로 조기 종료"
    cat > "${ARTIFACT_DIR}/stage18-server-report.json" <<EOF
{
  "verdict": "PARTIAL",
  "reason": "INSUFFICIENT_DISK",
  "rootFreeMb": ${ROOT_FREE_MB},
  "dockerFreeMb": ${DOCKER_FREE_MB},
  "minRequiredMb": ${MIN_FREE},
  "timestamp": "${TIMESTAMP}",
  "gitSha": "${GIT_SHA}"
}
EOF
    exit 2
fi
ok "디스크 여유 공간 충분: root=${ROOT_FREE_MB}MB docker=${DOCKER_FREE_MB}MB"

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
section "Step 4: 테스트 이미지 빌드 (${SEG_IMAGE}, TORCH_VARIANT=${TORCH_VARIANT})"

BUILD_LOG="${ARTIFACT_DIR}/build-segmentation-ai.log"
BUILD_SUCCESS=false

info "TORCH_VARIANT=${TORCH_VARIANT} 로 빌드 (CPU: nvidia 패키지 제외)"
if docker build \
    --build-arg TORCH_VARIANT="${TORCH_VARIANT}" \
    -t "${SEG_IMAGE}" \
    "${PROJECT_ROOT}/services/segmentation-ai" \
    > "${BUILD_LOG}" 2>&1; then
    BUILD_SUCCESS=true
    ok "이미지 빌드 성공: ${SEG_IMAGE}"
else
    err "이미지 빌드 실패"
    tail -30 "${BUILD_LOG}" | tee -a "${EXEC_LOG}"
    VERDICT="FAIL"; FINAL_EXIT=1; exit 1
fi

# ==========================================================================
# Step 4.5: 설치 패키지 검증 (nvidia/cuda 없음 확인)
# ==========================================================================
section "Step 4.5: 설치 패키지 검증"

PKG_LIST="${ARTIFACT_DIR}/pip-list.txt"

# pip list 실패 시 명확한 오류 출력 (set -e 영향 방어)
if docker run --rm "${SEG_IMAGE}" pip list > "${PKG_LIST}" 2>&1; then
    ok "pip 패키지 목록 조회 성공"
else
    err "pip 패키지 목록 조회 실패"
    cat "${PKG_LIST}" | tee -a "${EXEC_LOG}"
    VERDICT="FAIL"; FINAL_EXIT=1; exit 1
fi

TORCH_VERSION=$(awk '/^[Tt]orch /{print $2}' "${PKG_LIST}" | head -1)
TORCHVISION_VERSION=$(awk '/^[Tt]orch[Vv]ision /{print $2}' "${PKG_LIST}" | head -1)
SAM2_PKG_VERSION=$(awk '/^sam2 /{print $2}' "${PKG_LIST}" | head -1)
TORCH_VERSION="${TORCH_VERSION:-N/A}"
TORCHVISION_VERSION="${TORCHVISION_VERSION:-N/A}"
SAM2_PKG_VERSION="${SAM2_PKG_VERSION:-N/A}"

# grep은 미일치 시 exit 1 반환 → set -Eeuo pipefail 환경에서 스크립트 중단됨.
# awk 방식으로 교체: 미일치 시 0을 반환하며 항상 exit 0.
NVIDIA_PKG_COUNT=$(
    awk '
        BEGIN { IGNORECASE = 1; count = 0 }
        $1 ~ /^(nvidia-|cuda-toolkit$|triton$)/ { count++ }
        END { print count }
    ' "${PKG_LIST}"
)
NVIDIA_PKG_COUNT="${NVIDIA_PKG_COUNT:-0}"

info "torch 버전:            ${TORCH_VERSION}"
info "torchvision 버전:      ${TORCHVISION_VERSION}"
info "sam2 버전:             ${SAM2_PKG_VERSION}"
info "nvidia/cuda 패키지 수: ${NVIDIA_PKG_COUNT}"

# 필수 패키지 존재 여부 확인
if [[ "${TORCH_VERSION}" == "N/A" ]]; then
    err "torch 패키지가 pip list에 없음"
    VERDICT_REASONS+=("torch 패키지 없음")
    VERDICT="FAIL"; FINAL_EXIT=1; exit 1
fi

if [[ "${NVIDIA_PKG_COUNT}" -gt 0 ]]; then
    err "GPU 패키지 발견 (${NVIDIA_PKG_COUNT}개) — CPU 이미지에 허용 안 됨:"
    # 미일치 시 종료 방지를 위해 || true
    awk 'BEGIN{IGNORECASE=1} $1~/^(nvidia-|cuda-toolkit$|triton$)/{print}' "${PKG_LIST}" \
        | tee -a "${EXEC_LOG}" || true
    VERDICT_REASONS+=("GPU 패키지 ${NVIDIA_PKG_COUNT}개 발견 → CPU 빌드 실패")
    VERDICT="FAIL"; FINAL_EXIT=1; exit 1
fi
ok "nvidia/cuda 패키지 없음 (CPU-only 확인)"

# torch CUDA 상태 검증 (컨테이너 내부)
TORCH_CUDA_CHECK="${ARTIFACT_DIR}/torch-cuda-check.txt"
docker run --rm "${SEG_IMAGE}" python3 -c "
import torch, torchvision
print('torchVersion=', torch.__version__)
print('torchvisionVersion=', torchvision.__version__)
print('torchCudaVersion=', torch.version.cuda)
print('cudaAvailable=', torch.cuda.is_available())
" > "${TORCH_CUDA_CHECK}" 2>&1 || echo "(torch check failed)" > "${TORCH_CUDA_CHECK}"
cat "${TORCH_CUDA_CHECK}" | tee -a "${EXEC_LOG}"

TORCH_CUDA_AVAIL=$(grep 'cudaAvailable' "${TORCH_CUDA_CHECK}" 2>/dev/null | awk -F'= ' '{print $2}' | tr -d ' ' || echo "N/A")
if [[ "${TORCH_CUDA_AVAIL}" == "False" ]]; then
    ok "torch.cuda.is_available()=False (CPU-only 확인)"
else
    warn "CUDA 상태 예외: cudaAvailable=${TORCH_CUDA_AVAIL} (CPU 서버 기대값: False)"
    VERDICT_REASONS+=("torch.cuda.is_available()=${TORCH_CUDA_AVAIL} (기대: False)")
fi

# SAM 2 import 검증 (컨테이너 내부)
if docker run --rm "${SEG_IMAGE}" python3 -c "import sam2; print('sam2 import OK')" 2>/dev/null | grep -q "sam2 import OK"; then
    ok "sam2 import 성공 (버전: ${SAM2_PKG_VERSION})"
else
    err "sam2 import 실패"
    VERDICT_REASONS+=("sam2 import 실패")
    VERDICT="FAIL"; FINAL_EXIT=1; exit 1
fi

# psd-tools import 검증 (컨테이너 내부, Stage 18.2)
PSD_TOOLS_CHECK="${ARTIFACT_DIR}/psd-tools-check.txt"
if docker run --rm "${SEG_IMAGE}" python3 -c "
import psd_tools
from psd_tools import PSDImage
import importlib.metadata
version = importlib.metadata.version('psd-tools')
print('psdToolsVersion=' + version)
print('psd_tools import OK')
" > "${PSD_TOOLS_CHECK}" 2>&1; then
    PSD_TOOLS_VERSION=$(grep 'psdToolsVersion=' "${PSD_TOOLS_CHECK}" | cut -d= -f2 || echo "N/A")
    PSD_TOOLS_VERSION="${PSD_TOOLS_VERSION:-N/A}"
    PSD_TOOLS_OK=true
    ok "psd-tools import 성공 (버전: ${PSD_TOOLS_VERSION})"
else
    err "psd-tools import 실패 — requirements-cpu.txt에 psd-tools==1.9.31 선언 확인 필요"
    cat "${PSD_TOOLS_CHECK}" | tee -a "${EXEC_LOG}"
    VERDICT_REASONS+=("psd-tools import 실패 (Stage 18.2 요건)")
    VERDICT="FAIL"; FINAL_EXIT=1; exit 1
fi

# 패키지 검증 요약
info "═══ 패키지 검증 요약 ═══"
printf '  %-30s %-20s %s\n' "항목" "기대" "실제" | tee -a "${EXEC_LOG}"
printf '  %-30s %-20s %s\n' "torch CPU wheel" "2.5.1" "${TORCH_VERSION}" | tee -a "${EXEC_LOG}"
printf '  %-30s %-20s %s\n' "torchvision CPU wheel" "0.20.1" "${TORCHVISION_VERSION}" | tee -a "${EXEC_LOG}"
printf '  %-30s %-20s %s\n' "sam2 import" "success" "OK" | tee -a "${EXEC_LOG}"
printf '  %-30s %-20s %s\n' "psd-tools import" "1.9.31" "${PSD_TOOLS_VERSION}" | tee -a "${EXEC_LOG}"
printf '  %-30s %-20s %s\n' "nvidia 패키지 수" "0" "${NVIDIA_PKG_COUNT}" | tee -a "${EXEC_LOG}"
printf '  %-30s %-20s %s\n' "cudaAvailable" "False" "${TORCH_CUDA_AVAIL}" | tee -a "${EXEC_LOG}"

# ==========================================================================
# Step 4.7: Named volume 생성 (재실행 시 재사용)
# ==========================================================================
section "Step 4.7: Named volume '${HF_CACHE_VOLUME}' 준비"

if docker volume inspect "${HF_CACHE_VOLUME}" >/dev/null 2>&1; then
    ok "Named volume 기존재: ${HF_CACHE_VOLUME}"
else
    docker volume create "${HF_CACHE_VOLUME}" >/dev/null
    ok "Named volume 생성: ${HF_CACHE_VOLUME}"
fi

# ==========================================================================
# Step 4.8: GDINO 모델 사전 다운로드
# ==========================================================================
section "Step 4.8: GroundingDINO 모델 사전 다운로드"

GDINO_DOWNLOAD_LOG="${ARTIFACT_DIR}/gdino-download.log"
info "GDINO 다운로드 시작 (IDEA-Research/grounding-dino-tiny, safetensors only)"
info "캐시 볼륨: ${HF_CACHE_VOLUME} → /models/huggingface/hub"

docker run --rm \
    -v "${HF_CACHE_VOLUME}:/models" \
    -e HF_HUB_DISABLE_XET=1 \
    -e HF_HOME=/models/huggingface \
    -e HF_HUB_CACHE=/models/huggingface/hub \
    -e TRANSFORMERS_CACHE=/models/huggingface/transformers \
    -e HF_HUB_DOWNLOAD_TIMEOUT=1200 \
    -e HF_HUB_ETAG_TIMEOUT=60 \
    -e HF_HUB_DISABLE_TELEMETRY=1 \
    "${SEG_IMAGE}" \
    python3 -c "
import os, sys
os.environ['HF_HUB_DISABLE_XET'] = '1'
os.environ['HF_HOME'] = '/models/huggingface'
os.environ['HF_HUB_CACHE'] = '/models/huggingface/hub'
os.makedirs('/models/huggingface/hub', exist_ok=True)
from huggingface_hub import snapshot_download
print('GDINO snapshot_download 시작...', flush=True)
path = snapshot_download(
    repo_id='IDEA-Research/grounding-dino-tiny',
    cache_dir='/models/huggingface/hub',
    allow_patterns=['*.json', '*.txt', 'model.safetensors'],
    ignore_patterns=['pytorch_model.bin', '*.msgpack', 'flax_model.msgpack'],
)
print('GDINO 다운로드 완료:', path, flush=True)
" 2>&1 | tee "${GDINO_DOWNLOAD_LOG}" | tee -a "${EXEC_LOG}"

GDINO_DOWNLOAD_OK=false
if grep -q "GDINO 다운로드 완료" "${GDINO_DOWNLOAD_LOG}" 2>/dev/null; then
    GDINO_DOWNLOAD_OK=true
    ok "GDINO 사전 다운로드 성공"
else
    warn "GDINO 사전 다운로드 실패 또는 타임아웃 — 서비스 시작 시 재시도"
    VERDICT_REASONS+=("GDINO 사전 다운로드 실패 — lazy load로 대체")
fi

# ==========================================================================
# Step 4.9: SAM2 체크포인트 사전 다운로드
# ==========================================================================
section "Step 4.9: SAM2 체크포인트 사전 다운로드"

SAM2_DOWNLOAD_LOG="${ARTIFACT_DIR}/sam2-download.log"
SAM2_URL="https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_tiny.pt"
info "SAM2 체크포인트 다운로드: ${SAM2_URL}"

docker run --rm \
    -v "${HF_CACHE_VOLUME}:/models" \
    -e MODELS_DIR=/models \
    "${SEG_IMAGE}" \
    python3 -c "
import os, sys, time, urllib.request
url = '${SAM2_URL}'
out = '/models/sam2/sam2.1_hiera_tiny.pt'
os.makedirs('/models/sam2', exist_ok=True)
# 이미 있고 100MB 이상이면 스킵
if os.path.exists(out) and os.path.getsize(out) > 100_000_000:
    print('SAM2 체크포인트 이미 존재 (캐시 hit):', out, flush=True)
    sys.exit(0)
for attempt in range(1, 4):
    try:
        print(f'SAM2 다운로드 시도 {attempt}/3...', flush=True)
        tmp = out + '.tmp'
        urllib.request.urlretrieve(url, tmp)
        os.replace(tmp, out)
        size_mb = os.path.getsize(out) / 1_048_576
        print(f'SAM2 다운로드 완료: {out} ({size_mb:.1f}MB)', flush=True)
        break
    except Exception as e:
        print(f'SAM2 다운로드 오류 (attempt={attempt}): {e}', flush=True)
        if os.path.exists(out + '.tmp'):
            os.remove(out + '.tmp')
        if attempt < 3:
            time.sleep(5 * attempt)
else:
    print('SAM2 다운로드 실패 3회', flush=True)
    sys.exit(1)
" 2>&1 | tee "${SAM2_DOWNLOAD_LOG}" | tee -a "${EXEC_LOG}"

SAM2_DOWNLOAD_OK=false
if grep -qE "(SAM2 다운로드 완료|이미 존재)" "${SAM2_DOWNLOAD_LOG}" 2>/dev/null; then
    SAM2_DOWNLOAD_OK=true
    ok "SAM2 체크포인트 준비 완료"
else
    warn "SAM2 체크포인트 다운로드 실패 — 서비스 시작 시 재시도"
    VERDICT_REASONS+=("SAM2 다운로드 실패 — lazy load로 대체")
fi

# ==========================================================================
# Step 4.95: 캐시 유효성 검증
# ==========================================================================
section "Step 4.95: 캐시 유효성 검증"

CACHE_CHECK="${ARTIFACT_DIR}/cache-check.txt"
docker run --rm \
    -v "${HF_CACHE_VOLUME}:/models" \
    "${SEG_IMAGE}" \
    python3 -c "
import os
hub = '/models/huggingface/hub'
sam2 = '/models/sam2/sam2.1_hiera_tiny.pt'
# GDINO 캐시 확인
gdino_ok = False
repo_slug = 'IDEA-Research--grounding-dino-tiny'
model_dir = os.path.join(hub, f'models--{repo_slug}')
if os.path.isdir(model_dir):
    for root, dirs, files in os.walk(model_dir):
        if 'model.safetensors' in files:
            gdino_ok = True
            break
print('groundingDinoCacheReady=', gdino_ok)
# SAM2 캐시 확인
sam2_ok = os.path.exists(sam2) and os.path.getsize(sam2) > 100_000_000
sam2_size = os.path.getsize(sam2) / 1_048_576 if os.path.exists(sam2) else 0
print('sam2CacheReady=', sam2_ok)
print('sam2CheckpointSizeMb=', round(sam2_size, 1))
" 2>&1 | tee "${CACHE_CHECK}" | tee -a "${EXEC_LOG}"

GDINO_CACHE_READY=$(grep 'groundingDinoCacheReady=' "${CACHE_CHECK}" 2>/dev/null | awk -F'= ' '{print $2}' | tr -d ' ' || echo "False")
SAM2_CACHE_READY=$(grep 'sam2CacheReady=' "${CACHE_CHECK}" 2>/dev/null | awk -F'= ' '{print $2}' | tr -d ' ' || echo "False")

[[ "${GDINO_CACHE_READY}" == "True" ]] && ok "GDINO 캐시: 준비 완료" || warn "GDINO 캐시: 불완전 (서비스 시작 시 다운로드)"
[[ "${SAM2_CACHE_READY}" == "True" ]] && ok "SAM2 캐시: 준비 완료" || warn "SAM2 캐시: 없음 (서비스 시작 시 다운로드)"

# ==========================================================================
# Step 4.96: SAM2 초기화 smoke test (checkpoint 다운로드 이후)
# ==========================================================================
section "Step 4.96: SAM2 초기화 smoke test (Hydra + build_sam2 + 256×256 mask)"

SAM2_INIT_LOG="${ARTIFACT_DIR}/sam2-init-check.txt"
SAM2_INIT_TB="${ARTIFACT_DIR}/sam2-init-traceback.txt"
SAM2_INIT_SCRIPT="${ARTIFACT_DIR}/sam2_init_check.py"

cat > "${SAM2_INIT_SCRIPT}" <<'SAM2PY'
#!/usr/bin/env python3
"""SAM2 초기화 smoke test — verify_stage18_server.sh Step 4.96."""
import os, sys, traceback as _tb, importlib.metadata, inspect

ARTIFACT_DIR = os.environ.get("ARTIFACT_DIR", "/artifacts")
CKPT = os.environ.get("SAM2_CHECKPOINT", "/models/sam2/sam2.1_hiera_tiny.pt")
CFG  = os.environ.get("SAM2_CONFIG", "configs/sam2.1/sam2.1_hiera_t.yaml")
TB_FILE = os.path.join(ARTIFACT_DIR, "sam2-init-traceback.txt")
os.makedirs(ARTIFACT_DIR, exist_ok=True)

def log(key, value):
    print(f"{key}={value}", flush=True)

# 1. sam2 import + 패키지 감사
log("step", "1_import")
try:
    import sam2
    from sam2.build_sam import build_sam2
    from sam2.sam2_image_predictor import SAM2ImagePredictor
    log("sam2Import", "true")
    try:
        ver = importlib.metadata.version("sam2")
    except Exception:
        ver = "unknown"
    log("sam2DistVersion", ver)
    src = inspect.getsource(build_sam2)
    log("buildSam2HasHydraInit", str("initialize_config_module" in src))
except ImportError as e:
    log("sam2Import", "false")
    log("importError", str(e))
    with open(TB_FILE, "w") as f:
        _tb.print_exc(file=f)
    sys.exit(1)

# 2. Checkpoint 검증
log("step", "2_checkpoint")
ckpt_exists = os.path.isfile(CKPT)
ckpt_size   = os.path.getsize(CKPT) if ckpt_exists else 0
ckpt_ok     = ckpt_exists and ckpt_size > 100_000_000
log("sam2CheckpointPath", CKPT)
log("sam2CheckpointExists", str(ckpt_exists))
log("sam2CheckpointSizeBytes", str(ckpt_size))
log("sam2CheckpointReady", str(ckpt_ok))
if not ckpt_ok:
    log("sam2ModelBuild", "false")
    log("reason", f"checkpoint_not_ready:exists={ckpt_exists}:size={ckpt_size}")
    sys.exit(1)

# 3. Hydra 초기화 (명시적, 단일 config)
log("step", "3_hydra_init")
try:
    from hydra.core.global_hydra import GlobalHydra
    from hydra import initialize_config_module
    hydra_before = GlobalHydra.instance().is_initialized()
    log("hydraInitializedBefore", str(hydra_before))
    hydra_by_service = False
    if not hydra_before:
        initialize_config_module(
            config_module="sam2",
            version_base="1.2",
            job_name="sam2_smoke_check",
        )
        hydra_by_service = True
    log("hydraInitializedByService", str(hydra_by_service))
    log("hydraInitializedAfter", str(GlobalHydra.instance().is_initialized()))
    log("sam2ConfigName", CFG)
except Exception as e:
    log("hydraInitError", f"{type(e).__name__}: {str(e)[:200]}")
    with open(TB_FILE, "w") as f:
        _tb.print_exc(file=f)
    sys.exit(1)

# 4. build_sam2 (단일 config, GlobalHydra.clear 금지)
log("step", "4_build_sam2")
try:
    sam2_model = build_sam2(CFG, CKPT, device="cpu")
    log("sam2ModelBuild", "true")
    log("sam2ConfigUsed", CFG)
except Exception as e:
    log("sam2ModelBuild", "false")
    log("buildError", f"{type(e).__name__}: {str(e)[:200]}")
    with open(TB_FILE, "a") as f:
        f.write("\n=== build_sam2 ===\n")
        _tb.print_exc(file=f)
    sys.exit(1)

# 5. Predictor
log("step", "5_predictor")
try:
    pred = SAM2ImagePredictor(sam2_model)
    log("sam2PredictorReady", "true")
except Exception as e:
    log("sam2PredictorReady", "false")
    log("predictorError", f"{type(e).__name__}: {str(e)[:200]}")
    with open(TB_FILE, "a") as f:
        f.write("\n=== SAM2ImagePredictor ===\n")
        _tb.print_exc(file=f)
    sys.exit(1)

# 6~9. 256×256 smoke mask — CPU에서 torch.inference_mode (autocast 금지)
log("step", "6_smoke_mask")
try:
    import numpy as np
    import torch
    img = np.zeros((256, 256, 3), dtype=np.uint8)
    img[64:192, 64:192] = [200, 100, 50]
    pred.set_image(img)
    with torch.inference_mode():
        masks, scores, _ = pred.predict(
            box=np.array([20, 20, 200, 200], dtype=np.float32),
            multimask_output=False,
        )
    mask_ok = masks is not None and len(masks) > 0
    log("sam2SmokeMaskGenerated", str(mask_ok))
    if mask_ok:
        log("sam2SmokeMaskArea", str(int(masks[0].sum())))
        log("sam2SmokeShape", str(masks.shape))
    log("sam2RealInference", str(mask_ok))
except Exception as e:
    log("sam2SmokeMaskGenerated", "false")
    log("smokeError", f"{type(e).__name__}: {str(e)[:200]}")
    with open(TB_FILE, "a") as f:
        f.write("\n=== smoke mask ===\n")
        _tb.print_exc(file=f)
    sys.exit(1)

log("sam2InitCheck", "PASS")
SAM2PY

info "SAM2 smoke test 실행 중 (단일 config: configs/sam2.1/sam2.1_hiera_t.yaml)"
docker run --rm \
    -v "${HF_CACHE_VOLUME}:/models" \
    -v "${ARTIFACT_DIR}:/artifacts:rw" \
    -v "${SAM2_INIT_SCRIPT}:/scripts/sam2_init_check.py:ro" \
    -e SAM2_CHECKPOINT=/models/sam2/sam2.1_hiera_tiny.pt \
    -e SAM2_CONFIG=configs/sam2.1/sam2.1_hiera_t.yaml \
    -e ARTIFACT_DIR=/artifacts \
    "${SEG_IMAGE}" \
    python3 /scripts/sam2_init_check.py \
    2>&1 | tee "${SAM2_INIT_LOG}" | tee -a "${EXEC_LOG}" || true

# 결과 파싱
SAM2_INIT_OK=false
SAM2_INIT_STATUS="FAIL"
SAM2_CONFIG_USED="N/A"
SAM2_SMOKE_MASK="false"

if grep -q "sam2InitCheck=PASS" "${SAM2_INIT_LOG}" 2>/dev/null; then
    SAM2_INIT_OK=true
    SAM2_INIT_STATUS="PASS"
    ok "SAM2 초기화 smoke test PASS"
else
    warn "SAM2 초기화 smoke test FAIL"
    if [[ -f "${SAM2_INIT_TB}" ]]; then
        info "SAM2 traceback:"
        cat "${SAM2_INIT_TB}" | tee -a "${EXEC_LOG}" || true
    fi
    VERDICT_REASONS+=("SAM2 smoke init FAIL — traceback: ${SAM2_INIT_TB}")
fi

SAM2_CONFIG_USED=$(grep 'sam2ConfigUsed=' "${SAM2_INIT_LOG}" 2>/dev/null \
    | tail -1 | cut -d'=' -f2- || echo "N/A")
SAM2_SMOKE_MASK=$(grep 'sam2SmokeMaskGenerated=' "${SAM2_INIT_LOG}" 2>/dev/null \
    | tail -1 | cut -d'=' -f2- || echo "false")
HYDRA_BEFORE=$(grep 'hydraInitializedBefore=' "${SAM2_INIT_LOG}" 2>/dev/null \
    | tail -1 | cut -d'=' -f2- || echo "N/A")
HYDRA_BY_SVC=$(grep 'hydraInitializedByService=' "${SAM2_INIT_LOG}" 2>/dev/null \
    | tail -1 | cut -d'=' -f2- || echo "N/A")
HYDRA_AFTER=$(grep 'hydraInitializedAfter=' "${SAM2_INIT_LOG}" 2>/dev/null \
    | tail -1 | cut -d'=' -f2- || echo "N/A")
SAM2_MASK_AREA=$(grep 'sam2SmokeMaskArea=' "${SAM2_INIT_LOG}" 2>/dev/null \
    | tail -1 | cut -d'=' -f2- || echo "N/A")

info "sam2ConfigUsed:          ${SAM2_CONFIG_USED}"
info "hydraInitializedBefore:  ${HYDRA_BEFORE}"
info "hydraInitializedByService: ${HYDRA_BY_SVC}"
info "hydraInitializedAfter:   ${HYDRA_AFTER}"
info "sam2SmokeMaskGenerated:  ${SAM2_SMOKE_MASK}"
info "sam2SmokeMaskArea:       ${SAM2_MASK_AREA}"

# ==========================================================================
# Step 5: 테스트 네트워크 + 컨테이너 시작
# ==========================================================================
section "Step 5: 테스트 네트워크·컨테이너 시작"

docker network create "${NETWORK_NAME}" >/dev/null 2>&1 && NETWORK_CREATED=true
ok "테스트 네트워크: ${NETWORK_NAME}"

info "컨테이너 시작: ${CONTAINER_NAME} (port=127.0.0.1:${TEST_PORT}, PRELOAD_MODELS=true)"
docker run -d \
    --name "${CONTAINER_NAME}" \
    --network "${NETWORK_NAME}" \
    -p "127.0.0.1:${TEST_PORT}:8090" \
    -v "${HF_CACHE_VOLUME}:/models" \
    ${GPU_FLAGS} \
    -e PRELOAD_MODELS=true \
    -e CREATIVE_SEGMENTATION_DEVICE=auto \
    -e CREATIVE_EXTERNAL_SEGMENTATION_ENABLED=true \
    -e CREATIVE_EXTERNAL_SEGMENTATION_COMPARE_ONLY=true \
    -e CREATIVE_SEGMENTATION_MAX_IMAGE_SIDE=1280 \
    -e CREATIVE_SEGMENTATION_MIN_CONFIDENCE=0.25 \
    -e CREATIVE_SEGMENTATION_MASK_SCORE_THRESHOLD=70 \
    -e HF_HUB_DISABLE_XET=1 \
    -e HF_HOME=/models/huggingface \
    -e HF_HUB_CACHE=/models/huggingface/hub \
    -e TRANSFORMERS_CACHE=/models/huggingface/transformers \
    -e HF_HUB_DOWNLOAD_TIMEOUT=1200 \
    -e HF_HUB_ETAG_TIMEOUT=60 \
    -e HF_HUB_DISABLE_TELEMETRY=1 \
    -e SAM2_CONFIG=configs/sam2.1/sam2.1_hiera_t.yaml \
    -e SAM2_CHECKPOINT=/models/sam2/sam2.1_hiera_tiny.pt \
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

_parse_health_status() {
    local file="$1"
    if [[ -n "${PYTHON_CMD}" ]]; then
        ${PYTHON_CMD} -c "
import json,sys
try:
    d=json.load(open('${file}'))
    print(d.get('status','?'))
except: print('parse_error')
" 2>/dev/null || echo "?"
    else
        grep -o '"status":"[^"]*"' "${file}" 2>/dev/null | head -1 | cut -d'"' -f4 || echo "?"
    fi
}

for i in $(seq 1 "${HEALTH_RETRY_MAX}"); do
    http_code=$(curl -s -o "${HEALTH_JSON}" -w "%{http_code}" \
        --max-time 8 \
        "${SERVICE_URL}/health" 2>/dev/null || echo "000")

    if [[ "${http_code}" == "200" ]]; then
        # 200: READY
        HEALTH_OK=true
        ok "Health OK (시도 ${i}/${HEALTH_RETRY_MAX})"
        break
    elif [[ "${http_code}" == "503" ]] && [[ -f "${HEALTH_JSON}" ]]; then
        LAST_STATUS=$(_parse_health_status "${HEALTH_JSON}")
        if [[ "${LAST_STATUS}" == "error" ]]; then
            # 모델 로드 실패 — 대기해도 바뀌지 않음
            err "모델 로드 실패 (status=error) — 대기 중단"
            docker logs "${CONTAINER_NAME}" 2>&1 | tail -30 | tee -a "${EXEC_LOG}" || true
            VERDICT="FAIL"; FINAL_EXIT=1; exit 1
        fi
        # status=loading 또는 not_started: 계속 대기
        if (( i % 6 == 0 )); then
            info "Health 503 status=${LAST_STATUS} — 모델 로딩 중 (${i}/${HEALTH_RETRY_MAX})"
            docker logs "${CONTAINER_NAME}" 2>&1 | tail -3 | tee -a "${EXEC_LOG}" || true
        fi
    else
        if (( i % 6 == 0 )); then
            info "HTTP ${http_code} (${i}/${HEALTH_RETRY_MAX}) — 서비스 기동 대기"
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
# Step 6.5: /ready strict check (GDINO AND SAM2 모두 필요)
# ==========================================================================
section "Step 6.5: /ready strict endpoint check"

READY_JSON_FILE="${ARTIFACT_DIR}/ready.json"
READY_URL="${SERVICE_URL}/ready"
info "Strict readiness URL: ${READY_URL}"
READY_HTTP="$(
    curl -sS \
        --connect-timeout 10 \
        --max-time 30 \
        -o "${READY_JSON_FILE}" \
        -w "%{http_code}" \
        "${READY_URL}" \
        2>>"${EXEC_LOG}"
)" || READY_HTTP="000"
READY_HTTP="${READY_HTTP:-000}"
READY_JSON=$(cat "${READY_JSON_FILE}" 2>/dev/null || echo "{}")

info "HTTP /ready: ${READY_HTTP}"
info "$(cat "${READY_JSON_FILE}" 2>/dev/null | head -5 || true)"

_parse_ready_field() {
    local field="$1" default="${2:-}"
    if [[ -n "${PYTHON_CMD}" ]]; then
        ${PYTHON_CMD} -c "
import json, sys
try:
    d=json.load(open('${READY_JSON_FILE}'))
    v=d.get('${field}')
    print(str(v).lower() if isinstance(v,bool) else (v if v is not None else '${default}'))
except: print('${default}')
" 2>/dev/null || echo "${default}"
    else
        grep -o "\"${field}\":[^,}]*" "${READY_JSON_FILE}" 2>/dev/null \
            | head -1 | sed 's/.*: *//;s/[",]//g' || echo "${default}"
    fi
}

READY_OK=false
if [[ "${READY_HTTP}" == "200" ]]; then
    READY_OK=true
    ok "/ready HTTP 200 — GDINO AND SAM2 실제 추론 모두 준비"
else
    SAM2_READY_VAL=$(_parse_ready_field "sam2Ok"             "false")
    GDINO_READY_VAL=$(_parse_ready_field "groundingDinoOk"   "false")
    SAM2_ERR_TYPE=$(_parse_ready_field   "sam2LoadErrorType"    "")
    SAM2_ERR_MSG=$(_parse_ready_field    "sam2LoadErrorMessage" "")
    warn "/ready HTTP ${READY_HTTP} — gdino=${GDINO_READY_VAL} sam2=${SAM2_READY_VAL}"
    if [[ -n "${SAM2_ERR_TYPE}" ]]; then
        warn "SAM2 init error: type=${SAM2_ERR_TYPE} msg=${SAM2_ERR_MSG}"
    fi
    if [[ "${STAGE18_REQUIRE_REAL_INFERENCE}" == "true" ]]; then
        err "STAGE18_REQUIRE_REAL_INFERENCE=true: /ready!=200 → VERDICT=FAIL"
        VERDICT="FAIL"; FINAL_EXIT=1
        VERDICT_REASONS+=("/ready=${READY_HTTP} SAM2 실제 추론 불가 (strict mode)")
    else
        VERDICT_REASONS+=("/ready=${READY_HTTP} (sam2=${SAM2_READY_VAL}, gdino=${GDINO_READY_VAL}) — bbox fallback 허용")
    fi
fi

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

    # Stage 18.3: PSD 원본을 그대로 API 컨테이너에 전송
    # (컨테이너 내부 psd-tools가 처리 — 호스트 변환 불필요)
    IMAGE_PATH="${SAMPLE_PATH}"
    if [[ "${SAMPLE_PATH}" == *.psd ]]; then
        # 원본 PSD artifact 저장
        cp "${SAMPLE_PATH}" "${ARTIFACT_DIR}/original-input.psd" 2>/dev/null || true
        ok "원본 PSD artifact 저장: original-input.psd"
        info "PSD 원본을 API 컨테이너에 직접 전송 (컨테이너 내 psd-tools가 처리)"
    fi

    # 원본 복사 (verify_stage18_server.py --image-path 인자용)
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

    # /v1/segment 호출 (PSD는 컨테이너 내부 psd-tools가 처리)
    info "POST /v1/segment ..."
    if [[ "${SAMPLE_PATH}" == *.psd ]]; then
        SEG_MIME="image/vnd.adobe.photoshop"
    else
        SEG_MIME="application/octet-stream"
    fi
    HTTP_SEG=$(curl -s \
        -o "${SEGMENT_JSON}" \
        -w "%{http_code}" \
        --max-time 180 \
        -X POST "${SERVICE_URL}/v1/segment" \
        -F "image=@${IMAGE_PATH};type=${SEG_MIME}" \
        -F "prompts=<${PROMPTS_FILE}" \
        -F "requestId=stage18-server-${TIMESTAMP}" \
        -F "sourceType=stage18-mother-hand" \
        2>/dev/null || echo "000")
    rm -f "${PROMPTS_FILE}"

    if [[ "${HTTP_SEG}" == "200" ]]; then
        ok "Segment 응답: HTTP 200"

        # Python helper를 컨테이너 내부에서 실행 (host python3 불필요)
        if [[ -f "${SCRIPT_DIR}/verify_stage18_server.py" ]]; then
            info "Python helper: 컨테이너 내부 실행"
            docker run --rm \
                -v "${ARTIFACT_DIR}:/artifacts:rw" \
                -v "${SCRIPT_DIR}/verify_stage18_server.py:/scripts/verify.py:ro" \
                "${SEG_IMAGE}" \
                python3 /scripts/verify.py \
                    --segment-json /artifacts/segmentation.json \
                    --health-json /artifacts/health.json \
                    --image-path /artifacts/original.png \
                    --output-dir /artifacts \
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
                FLATTEN_METHOD=$(parse_json "${ANALYSIS_JSON}" "flattenMethod" "unknown")
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
info "flattenMethod:          ${FLATTEN_METHOD}"
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

# Stage 18.3: PSD flatten artifact 필수 확인
FLATTEN_ARTIFACTS_OK=true
if [[ "${SAMPLE_PATH}" == *.psd ]]; then
    for fname in "original-input.psd" "flattened-input.png" "flatten-metadata.json"; do
        if [[ -f "${ARTIFACT_DIR}/${fname}" ]]; then
            ok "flatten artifact 존재: ${fname}"
        else
            warn "flatten artifact 누락: ${fname}"
            FLATTEN_ARTIFACTS_OK=false
        fi
    done
    if [[ "${FLATTEN_ARTIFACTS_OK}" == "true" ]]; then
        ok "PSD flatten 3개 artifact 모두 존재"
    fi
fi

# score-comparison.json 확인
if [[ -f "${ARTIFACT_DIR}/score-comparison.json" ]]; then
    ok "score-comparison.json 생성됨"
else
    info "score-comparison.json 없음 (샘플 없거나 미생성)"
fi

# ==========================================================================
# Step 10: 컨테이너 정리 + 운영 환경 검사
# ==========================================================================
section "Step 10: 컨테이너 정리 및 운영 영향 검사"

# ── 컨테이너 로그 저장 (정리 전에 반드시 보존) ───────────────────────────────
CONTAINER_LOG_FILE="${ARTIFACT_DIR}/segmentation-service.log"
info "컨테이너 로그 저장: ${CONTAINER_LOG_FILE}"
docker logs "${CONTAINER_NAME}" > "${CONTAINER_LOG_FILE}" 2>&1 || true
ok "컨테이너 로그 저장 완료 ($(wc -l < "${CONTAINER_LOG_FILE}" 2>/dev/null || echo '?')줄)"

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
    VERDICT="PARTIAL"; FINAL_EXIT=2
elif [[ "${GDINO_DETECTED}" == "false" ]]; then
    VERDICT="FAIL"; FINAL_EXIT=1
    VERDICT_REASONS+=("GroundingDINO 탐지 실패")
elif [[ "${READY_OK}" == "false" && "${STAGE18_REQUIRE_REAL_INFERENCE}" == "true" ]]; then
    # /ready != 200 이고 strict mode → FAIL (Step 6.5에서 이미 설정됐을 수도 있지만 재확인)
    VERDICT="FAIL"; FINAL_EXIT=1
    VERDICT_REASONS+=("/ready 미통과: SAM2 실제 추론 불가 (STAGE18_REQUIRE_REAL_INFERENCE=true)")
elif [[ "${BBOX_FALLBACK_USED}" == "true" ]]; then
    if [[ "${STAGE18_REQUIRE_REAL_INFERENCE}" == "true" ]]; then
        VERDICT="FAIL"; FINAL_EXIT=1
        VERDICT_REASONS+=("SAM2 bbox fallback 사용 — strict mode에서 FAIL")
    else
        VERDICT="PARTIAL"; FINAL_EXIT=2
        VERDICT_REASONS+=("SAM2 bbox fallback 사용 (compareOnly 허용)")
    fi
else
    # 점수 기반 판정 (host python 없어도 bash printf로 처리)
    score_int=0
    if [[ "${EXT_MASK_SCORE}" != "N/A" ]]; then
        if [[ -n "${PYTHON_CMD}" ]]; then
            score_int=$(${PYTHON_CMD} -c "print(int(float('${EXT_MASK_SCORE}')))" 2>/dev/null || echo 0)
        else
            score_int=$(printf "%.0f" "${EXT_MASK_SCORE}" 2>/dev/null || echo 0)
        fi
    fi
    if [[ "${score_int}" -ge 70 ]]; then
        # Stage 18.3: PSD는 psd_tools_composite 또는 psd_tools_merged이어야 PASS
        # psd_tools_merged: psd-tools 열기 성공 + Pillow 병합 데이터 (acceptable)
        # pillow_psd_fallback: psd-tools 완전 실패 → PARTIAL
        PSD_FLATTEN_OK=true
        if [[ "${SAMPLE_PATH}" == *.psd ]]; then
            FM="${FLATTEN_METHOD:-unknown}"
            if [[ "${FM}" != "psd_tools_composite" && "${FM}" != "psd_tools_merged" ]]; then
                PSD_FLATTEN_OK=false
                VERDICT_REASONS+=("PSD flattenMethod=${FM} (기대: psd_tools_composite|psd_tools_merged)")
                warn "PSD composite 미완성 — flattenMethod=${FM} → PARTIAL"
            fi
        fi
        # flatten artifact 누락 시 PARTIAL
        if [[ "${SAMPLE_PATH}" == *.psd ]] && [[ "${FLATTEN_ARTIFACTS_OK:-true}" == "false" ]]; then
            PSD_FLATTEN_OK=false
            VERDICT_REASONS+=("PSD flatten artifact 누락")
        fi
        if [[ "${PSD_FLATTEN_OK}" == "true" ]]; then
            VERDICT="PASS"; FINAL_EXIT=0
        else
            VERDICT="PARTIAL"; FINAL_EXIT=2
        fi
    else
        VERDICT="PARTIAL"; FINAL_EXIT=2
        VERDICT_REASONS+=("externalMaskScore=${EXT_MASK_SCORE} < 70")
    fi
fi

# ==========================================================================
# Step 12: 보고서 생성
# ==========================================================================
section "Step 12: 보고서 생성"

REPORT_MD="${ARTIFACT_DIR}/stage18-server-report.md"
{
printf "# Stage 18 운영서버 검증 보고서\n\n"
printf -- "- **실행일시**: %s\n" "${TIMESTAMP}"
printf -- "- **Git SHA**: %s\n" "${GIT_SHA}"
printf -- "- **검증 경로**: %s\n\n" "${PROJECT_ROOT}"

printf "## 환경\n\n"
printf "| 항목 | 결과 |\n|---|---|\n"
printf "| Docker 버전 | %s |\n" "${DOCKER_VERSION}"
printf "| CUDA 사용 가능 | %s |\n" "${CUDA_AVAILABLE}"
printf "| CUDA 디바이스 | %s |\n" "${CUDA_DEVICE}"
printf "| TORCH_VARIANT | %s |\n" "${TORCH_VARIANT}"
printf "| 테스트 이미지 | %s |\n" "${SEG_IMAGE}"
printf "| 샘플 상태 | %s |\n\n" "${SAMPLE_STATUS}"

printf "## 디스크 / 패키지 검사\n\n"
printf "| 항목 | 기대 | 실제 |\n|---|---|---|\n"
printf "| 루트 여유 (MB) | >=%s | %s |\n" "${STAGE18_MIN_FREE_DISK_MB}" "${ROOT_FREE_MB}"
printf "| Docker 여유 (MB) | >=%s | %s |\n" "${STAGE18_MIN_FREE_DISK_MB}" "${DOCKER_FREE_MB}"
printf "| torch 버전 | 2.5.1 (CPU) | %s |\n" "${TORCH_VERSION}"
printf "| torchvision 버전 | 0.20.1 (CPU) | %s |\n" "${TORCHVISION_VERSION}"
printf "| sam2 버전 | any | %s |\n" "${SAM2_PKG_VERSION}"
printf "| psd-tools 버전 | 1.9.31 | %s |\n" "${PSD_TOOLS_VERSION}"
printf "| psd-tools import | OK | %s |\n" "$([ "${PSD_TOOLS_OK}" == "true" ] && echo OK || echo FAIL)"
printf "| nvidia 패키지 수 | 0 | %s |\n" "${NVIDIA_PKG_COUNT}"
printf "| cudaAvailable | False | %s |\n\n" "${TORCH_CUDA_AVAIL}"

printf "## 모델 사전 다운로드\n\n"
printf "| 항목 | 결과 |\n|---|---|\n"
printf "| HF 캐시 볼륨 | %s |\n" "${HF_CACHE_VOLUME}"
printf "| GDINO 다운로드 성공 | %s |\n" "${GDINO_DOWNLOAD_OK}"
printf "| SAM2 다운로드 성공 | %s |\n" "${SAM2_DOWNLOAD_OK}"
printf "| GDINO 캐시 준비 | %s |\n" "${GDINO_CACHE_READY}"
printf "| SAM2 캐시 준비 | %s |\n\n" "${SAM2_CACHE_READY}"

printf "## 모델 정보\n\n"
printf "| field | actual |\n|---|---|\n"
printf "| groundingDinoModelId | %s |\n" "${GDINO_MODEL_ID}"
printf "| sam2ModelId | %s |\n" "${SAM2_MODEL_ID}"
printf "| selectedDevice | %s |\n" "${SEG_DEVICE}"
printf "| cudaAvailable | %s |\n" "${CUDA_AVAILABLE}"
printf "| externalModelRealInference | %s |\n" "${REAL_INFERENCE}"
printf "| bboxFallbackEnabled | %s |\n\n" "${BBOX_FALLBACK_ENABLED}"

printf "## SAM2 초기화 Smoke Test (Step 4.6)\n\n"
printf "| 항목 | 결과 |\n|---|---|\n"
printf "| sam2InitStatus | %s |\n" "${SAM2_INIT_STATUS:-not_run}"
printf "| sam2ConfigUsed | %s |\n" "${SAM2_CONFIG_USED:-N/A}"
printf "| sam2SmokeMaskGenerated | %s |\n" "${SAM2_SMOKE_MASK:-N/A}"
printf "| sam2InitLog | %s |\n\n" "${ARTIFACT_DIR}/sam2-init-check.txt"

printf "## /ready Strict Check (Step 6.5)\n\n"
printf "| 항목 | 기대 | 실제 |\n|---|---|---|\n"
printf "| HTTP status | 200 | %s |\n" "${READY_HTTP:-N/A}"
printf "| READY_OK | true | %s |\n" "${READY_OK:-false}"
printf "| STAGE18_REQUIRE_REAL_INFERENCE | - | %s |\n\n" "${STAGE18_REQUIRE_REAL_INFERENCE}"

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
printf "| edgeSharpness | - | %s |\n" "${EDGE_SHARPNESS}"
printf "| flattenMethod | psd_tools_composite&#124;psd_tools_merged | %s |\n" "${FLATTEN_METHOD}"
printf "| flatten artifacts | 3개 존재 | %s |\n\n" "${FLATTEN_ARTIFACTS_OK:-N/A}"

printf "## 마스크 소스 분리 (Stage 18.3)\n\n"
printf "| 항목 | 값 |\n|---|---|\n"
printf "| bestEvaluatedMaskSource | %s |\n" "$(parse_json "${ANALYSIS_JSON:-/dev/null}" "bestEvaluatedMaskSource" "N/A")"
printf "| externalMaskEligible | %s |\n" "$(parse_json "${ANALYSIS_JSON:-/dev/null}" "externalMaskEligible" "N/A")"
printf "| appliedMaskSource | %s |\n" "$(parse_json "${ANALYSIS_JSON:-/dev/null}" "appliedMaskSource" "native")"
printf "| maskApplicationMode | %s |\n" "$(parse_json "${ANALYSIS_JSON:-/dev/null}" "maskApplicationMode" "compare_only")"
printf "| applicationBlockedReason | %s |\n\n" "$(parse_json "${ANALYSIS_JSON:-/dev/null}" "applicationBlockedReason" "compare_only_enabled")"

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
    printf -- "- %s\n" "${r}"
done

printf "\n---\n\n"
printf "## 최종 판정: **%s** (exit %d)\n\n" "${VERDICT}" "${FINAL_EXIT}"

printf "## 최종 5줄 요약\n\n"
printf -- "- GroundingDINO: %s\n"  "${GDINO_MODEL_ID:-N/A}"
printf -- "- SAM2 초기화: %s\n"    "${SAM2_INIT_STATUS:-not_run}"
printf -- "- /ready 판정: %s\n"    "${READY_OK:-false}"
printf -- "- nvidia 패키지: %s개 (기대: 0)\n" "${NVIDIA_PKG_COUNT}"
printf -- "- 서버 재검증 준비: %s\n" "$([ "${VERDICT}" = "PASS" ] && echo "Stage 19 진행 가능" || echo "PARTIAL — Stage 19 대기")"

printf "\n## 권장 정리 명령 (필요 시 수동 실행)\n\n"
printf '```bash\n'
printf "docker image prune -f\n"
printf "docker builder prune -f --filter 'until=24h'\n"
printf "# 절대 실행 금지: docker system prune -a / docker volume prune\n"
printf '```\n'
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
echo "  TORCH_VARIANT:              ${TORCH_VARIANT}"                          | tee -a "${EXEC_LOG}"
echo "  torch 버전:                 ${TORCH_VERSION}"                          | tee -a "${EXEC_LOG}"
echo "  nvidia 패키지 수:           ${NVIDIA_PKG_COUNT} (기대: 0)"             | tee -a "${EXEC_LOG}"
echo "  cudaAvailable:              ${TORCH_CUDA_AVAIL} (기대: False)"         | tee -a "${EXEC_LOG}"
echo "  HF 캐시 볼륨:               ${HF_CACHE_VOLUME}"                        | tee -a "${EXEC_LOG}"
echo "  GDINO 캐시 준비:            ${GDINO_CACHE_READY}"                      | tee -a "${EXEC_LOG}"
echo "  SAM2 캐시 준비:             ${SAM2_CACHE_READY}"                       | tee -a "${EXEC_LOG}"
echo "  externalModelRealInference: ${REAL_INFERENCE}"                         | tee -a "${EXEC_LOG}"
echo "  groundingDinoDetected:      ${GDINO_DETECTED}"                         | tee -a "${EXEC_LOG}"
echo "  sam2MaskGenerated:          ${SAM2_GENERATED}"                         | tee -a "${EXEC_LOG}"
echo "  bboxFallbackUsed:           ${BBOX_FALLBACK_USED}"                     | tee -a "${EXEC_LOG}"
echo "  externalMaskScore:          ${EXT_MASK_SCORE}"                         | tee -a "${EXEC_LOG}"
echo "  handLeakRisk:               ${HAND_LEAK}"                              | tee -a "${EXEC_LOG}"
echo "  backgroundLeakRisk:         ${BG_LEAK}"                                | tee -a "${EXEC_LOG}"
echo "  selectedMaskSource:         native (compareOnly=true)"                 | tee -a "${EXEC_LOG}"
echo "  Stage 19 진행 가능:         $([ "${VERDICT}" = "PASS" ] && echo YES || echo NO)" | tee -a "${EXEC_LOG}"
echo "═══════════════════════════════════════════════════════════" | tee -a "${EXEC_LOG}"

exit "${FINAL_EXIT}"
