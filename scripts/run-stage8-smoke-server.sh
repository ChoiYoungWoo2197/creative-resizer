#!/usr/bin/env bash
# ==========================================================================
# run-stage8-smoke-server.sh
# BannerSpec Stage 8 — 운영서버 격리 Docker Smoke Test
#
# 사용법 (프로젝트 루트 또는 scripts/ 어디서든 실행 가능):
#   bash scripts/run-stage8-smoke-server.sh
#
# 보안 제약:
#   - 기존 운영 컨테이너 일절 중지·재시작 금지
#   - 운영 MongoDB·RabbitMQ 사용 금지
#   - 운영 환경변수 파일 수정 금지
#   - 운영 포트 덮어쓰기 금지 (18082만 사용)
#   - docker system prune / 전체 볼륨 정리 금지
# ==========================================================================
set -Eeuo pipefail

# ══════════════════════════════════════════════════════════════════════════════
# 0-A: 스크립트 위치 기반 프로젝트 루트 확정 및 이동
#      (스크립트가 어느 디렉토리에서 호출되어도 상대경로 오류 없음)
# ══════════════════════════════════════════════════════════════════════════════
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

# ══════════════════════════════════════════════════════════════════════════════
# 0-B: 아티팩트 디렉토리 및 로그 파일 선행 생성
#      tee / 로그 함수를 정의하기 전에 반드시 완료해야 함
# ══════════════════════════════════════════════════════════════════════════════
TIMESTAMP="$(date '+%Y%m%d_%H%M%S')"
ARTIFACT_DIR="${PROJECT_ROOT}/artifacts/stage8-smoke/${TIMESTAMP}"
SMOKE_LOG="${ARTIFACT_DIR}/smoke.log"

if ! mkdir -p "${ARTIFACT_DIR}"; then
    echo "[FAIL] Unable to create artifact directory: ${ARTIFACT_DIR}" >&2
    exit 1
fi

if ! touch "${SMOKE_LOG}"; then
    echo "[FAIL] Unable to create log file: ${SMOKE_LOG}" >&2
    exit 1
fi

# ══════════════════════════════════════════════════════════════════════════════
# 0-C: 상수 / 색상 / 로그 함수 정의
#      SMOKE_LOG 파일이 존재하는 것이 보장된 이후에만 tee -a 사용
# ══════════════════════════════════════════════════════════════════════════════
PROJECT_NAME="creative-resizer-stage8-smoke"
COMPOSE_FILE="docker-compose.smoke.yml"
COMPOSE_CMD="docker compose -p ${PROJECT_NAME} -f ${COMPOSE_FILE}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; NC='\033[0m'

info()    { echo -e "${BLUE}[INFO]${NC}  $*" | tee -a "${SMOKE_LOG}"; }
success() { echo -e "${GREEN}[OK]${NC}    $*" | tee -a "${SMOKE_LOG}"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*" | tee -a "${SMOKE_LOG}"; }
err()     { echo -e "${RED}[ERR]${NC}   $*" | tee -a "${SMOKE_LOG}"; }
section() { CURRENT_STEP="$*"; echo -e "\n${BLUE}══ Step $* ══${NC}" | tee -a "${SMOKE_LOG}"; }

# ══════════════════════════════════════════════════════════════════════════════
# 0-D: trap 등록 (SMOKE_LOG 생성 이후)
# ══════════════════════════════════════════════════════════════════════════════
SMOKE_RUNNER_EXIT=0
FINAL_RESULT="UNKNOWN"
CURRENT_STEP="(init)"

cleanup() {
    # trap 진입 직후의 실제 종료코드를 보존 (set -e 로 인한 실패 포함)
    local exit_code=$?
    set +e

    echo "" | tee -a "${SMOKE_LOG}"
    echo -e "\n${BLUE}══ Step 26: Cleanup (trap) ══${NC}" | tee -a "${SMOKE_LOG}"
    info "Removing smoke containers and volumes..."
    ${COMPOSE_CMD} down --volumes --remove-orphans 2>>"${SMOKE_LOG}" || true
    success "Smoke containers removed (project=${PROJECT_NAME})"

    # smoke runner 실패는 exit_code=0으로 흘러도 비정상 처리
    if [ "${exit_code}" -eq 0 ] && [ "${SMOKE_RUNNER_EXIT}" -ne 0 ]; then
        exit_code="${SMOKE_RUNNER_EXIT}"
    fi

    echo "" | tee -a "${SMOKE_LOG}"
    echo "════════════════════════════════════════════════════════" | tee -a "${SMOKE_LOG}"
    if [ "${exit_code}" -eq 0 ] && [ "${FINAL_RESULT}" = "PASS" ]; then
        success "Stage 8 Smoke: ALL PASS"
    else
        # exit_code가 0이면 최소 1로 설정해 프로세스 종료코드 보장
        [ "${exit_code}" -eq 0 ] && exit_code=1
        err "Stage 8 Smoke: FAIL (exit ${exit_code})"
        err "  Last step : ${CURRENT_STEP}"
    fi
    echo "  Artifacts: ${ARTIFACT_DIR}/" | tee -a "${SMOKE_LOG}"
    echo "════════════════════════════════════════════════════════" | tee -a "${SMOKE_LOG}"
    exit "${exit_code}"
}
trap cleanup EXIT

# ── Step 1: 아티팩트 메타 기록 ───────────────────────────────────────────────
section "1: Artifact directory ready"
{
    echo "Stage 8 Smoke Test started : ${TIMESTAMP}"
    echo "Project                    : ${PROJECT_NAME}"
    echo "Project root               : ${PROJECT_ROOT}"
    echo "Host                       : $(hostname)"
    echo "Compose file               : ${COMPOSE_FILE}"
} >> "${SMOKE_LOG}"
success "Artifact dir: ${ARTIFACT_DIR}"
success "Log file    : ${SMOKE_LOG}"

# ── Step 2: git 상태 스냅샷 ──────────────────────────────────────────────────
section "2: Git snapshot"
git rev-parse HEAD > "${ARTIFACT_DIR}/git_commit.txt" 2>&1 \
    || echo "(no git)" > "${ARTIFACT_DIR}/git_commit.txt"
git status --short > "${ARTIFACT_DIR}/git_status.txt" 2>&1 || true
COMMIT=$(cat "${ARTIFACT_DIR}/git_commit.txt")
info "Commit: ${COMMIT}"

# ── Step 3: 운영 컨테이너 스냅샷 (사후 비교용) ───────────────────────────────
section "3: Production container snapshot (before)"
docker ps --format '{{.Names}}\t{{.Status}}\t{{.Image}}' \
    > "${ARTIFACT_DIR}/prod_before.txt" 2>/dev/null || true
PROD_COUNT=$(wc -l < "${ARTIFACT_DIR}/prod_before.txt" | tr -d ' ')
info "Running containers: ${PROD_COUNT}"
cat "${ARTIFACT_DIR}/prod_before.txt" | tee -a "${SMOKE_LOG}" || true

# ── Step 4: 서버 리소스 확인 ──────────────────────────────────────────────────
section "4: Server resource check"
FREE_KB=$(awk '/MemAvailable/{print $2}' /proc/meminfo 2>/dev/null || echo 0)
FREE_GB=$(awk "BEGIN{printf \"%.1f\", ${FREE_KB}/1048576}")
info "Available memory: ${FREE_GB} GB"
if [ "${FREE_KB}" -lt 3145728 ] 2>/dev/null; then
    warn "Less than 3 GB free — smoke test may fail due to OOM"
fi
df -h . 2>/dev/null | tail -1 | tee -a "${SMOKE_LOG}" || true

# ── Step 5: 운영 포트 충돌 확인 ──────────────────────────────────────────────
section "5: Port 18082 conflict check"
if ss -tlnp 2>/dev/null | grep -q ':18082 '; then
    err "Port 18082 is already in use — aborting to avoid conflict"
    SMOKE_RUNNER_EXIT=1
    exit 1
fi
success "Port 18082 is free"

# ── Step 6: 기존 stale smoke 컨테이너 정리 ───────────────────────────────────
section "6: Remove stale smoke containers"
STALE=$(docker ps -a --filter "label=com.docker.compose.project=${PROJECT_NAME}" -q 2>/dev/null || true)
if [ -n "${STALE}" ]; then
    warn "Stale smoke containers found — removing..."
    ${COMPOSE_CMD} down --volumes --remove-orphans 2>>"${SMOKE_LOG}" || true
    success "Stale containers removed"
else
    info "No stale smoke containers"
fi

# ── Step 7: Docker 이미지 빌드 (--no-cache) ──────────────────────────────────
section "7: Build Docker images (no-cache)"
info "Building: API (JDK17 compile + test), Worker (Python), smoke runner..."
if ! ${COMPOSE_CMD} build --no-cache 2>&1 | tee -a "${SMOKE_LOG}"; then
    err "Docker image build failed — check Dockerfile or .dockerignore"
    exit 1
fi
success "All images built"

# ── Step 8: MongoDB + RabbitMQ 기동 ──────────────────────────────────────────
section "8: Start MongoDB and RabbitMQ"
${COMPOSE_CMD} up -d mongo rabbitmq 2>&1 | tee -a "${SMOKE_LOG}"
info "Waiting for mongo health..."
WAIT=0
until ${COMPOSE_CMD} ps mongo 2>/dev/null | grep -q "(healthy)"; do
    sleep 3; WAIT=$((WAIT+3))
    [ "${WAIT}" -gt 120 ] && { err "MongoDB health timeout (${WAIT}s)"; SMOKE_RUNNER_EXIT=1; exit 1; }
done
info "Waiting for rabbitmq health..."
WAIT=0
until ${COMPOSE_CMD} ps rabbitmq 2>/dev/null | grep -q "(healthy)"; do
    sleep 3; WAIT=$((WAIT+3))
    [ "${WAIT}" -gt 120 ] && { err "RabbitMQ health timeout (${WAIT}s)"; SMOKE_RUNNER_EXIT=1; exit 1; }
done
success "MongoDB + RabbitMQ healthy"

# ── Step 9: Python Worker 기동 ────────────────────────────────────────────────
section "9: Start Python Worker"
${COMPOSE_CMD} up -d worker 2>&1 | tee -a "${SMOKE_LOG}"
info "Waiting for Worker health (psd-tools 초기화 포함)..."
WAIT=0
until ${COMPOSE_CMD} ps worker 2>/dev/null | grep -q "(healthy)"; do
    sleep 3; WAIT=$((WAIT+3))
    [ "${WAIT}" -gt 120 ] && { err "Worker health timeout (${WAIT}s)"; SMOKE_RUNNER_EXIT=1; exit 1; }
done
success "Python Worker healthy"

# ── Step 10: Spring Boot API 기동 ────────────────────────────────────────────
section "10: Start Spring Boot API"
${COMPOSE_CMD} up -d api 2>&1 | tee -a "${SMOKE_LOG}"
info "Waiting for API health (JDK17 Spring Boot 기동 대기)..."
WAIT=0
until ${COMPOSE_CMD} ps api 2>/dev/null | grep -q "(healthy)"; do
    sleep 5; WAIT=$((WAIT+5))
    [ "${WAIT}" -gt 210 ] && { err "API health timeout (${WAIT}s)"; SMOKE_RUNNER_EXIT=1; exit 1; }
done
success "Spring Boot API healthy"

# ── Step 11: actuator/health 직접 확인 ───────────────────────────────────────
section "11: Verify actuator/health from host"
HEALTH_RESP=$(curl -sf "http://127.0.0.1:18082/actuator/health" 2>/dev/null || echo "FAIL")
echo "${HEALTH_RESP}" | tee -a "${SMOKE_LOG}"
if echo "${HEALTH_RESP}" | grep -q '"status":"UP"'; then
    success "actuator/health = UP"
else
    err "actuator/health check failed: ${HEALTH_RESP}"
    SMOKE_RUNNER_EXIT=1
    exit 1
fi

# ── Step 12: Worker 연결 확인 (Spring Boot Smoke API 경유) ───────────────────
section "12: Verify Worker connectivity via Spring Boot Smoke API"
WORKER_RESP=$(curl -sf "http://127.0.0.1:18082/api/smoke/worker-health" 2>/dev/null || echo "FAIL")
echo "${WORKER_RESP}" | tee -a "${SMOKE_LOG}"
if echo "${WORKER_RESP}" | grep -q '"workerHealthy":true'; then
    success "Spring Boot → Worker connectivity OK"
else
    err "Spring Boot cannot reach Worker: ${WORKER_RESP}"
    SMOKE_RUNNER_EXIT=1
    exit 1
fi

# ── Step 13: API 빌드 로그 저장 (JDK17 검증) ─────────────────────────────────
section "13: Save API startup log (JDK17 compile verification)"
${COMPOSE_CMD} logs api 2>&1 | head -150 > "${ARTIFACT_DIR}/api_startup.log"
info "Saved: ${ARTIFACT_DIR}/api_startup.log"
grep -i "java.version\|jdk\|compileJava\|Tests run\|BUILD" "${ARTIFACT_DIR}/api_startup.log" \
    | head -20 | tee -a "${SMOKE_LOG}" || true

# ── Step 14-24: Smoke Test Runner 실행 ───────────────────────────────────────
section "14-24: Run smoke test runner (BannerSpec API + Worker Contract)"
info "Running: http_smoke_test.py (Steps 1-12) + worker_contract_smoke_test.py"
SMOKE_RUNNER_EXIT=0
${COMPOSE_CMD} run --rm -T \
    -e API_URL=http://api:8081 \
    -e WORKER_URL=http://worker:5000 \
    -e SMOKE_TIMEOUT=30 \
    smoke 2>&1 | tee "${ARTIFACT_DIR}/smoke_runner_output.txt" | tee -a "${SMOKE_LOG}" \
    || SMOKE_RUNNER_EXIT=$?

if [ "${SMOKE_RUNNER_EXIT}" -eq 0 ]; then
    success "Smoke runner: ALL PASS"
else
    err "Smoke runner: FAIL (exit ${SMOKE_RUNNER_EXIT})"
fi

# ── Step 25: 전체 서비스 로그 저장 ───────────────────────────────────────────
section "25: Save all service logs"
${COMPOSE_CMD} logs api    2>&1 > "${ARTIFACT_DIR}/api_full.log"
${COMPOSE_CMD} logs worker 2>&1 > "${ARTIFACT_DIR}/worker_full.log"
${COMPOSE_CMD} logs mongo  2>&1 > "${ARTIFACT_DIR}/mongo_full.log"
info "Logs saved to ${ARTIFACT_DIR}/"

# ── Step 25b: 운영 컨테이너 변경 여부 확인 ───────────────────────────────────
section "25b: Verify production containers unchanged"
docker ps --format '{{.Names}}\t{{.Status}}\t{{.Image}}' \
    > "${ARTIFACT_DIR}/prod_after.txt" 2>/dev/null || true

grep -v "${PROJECT_NAME}" "${ARTIFACT_DIR}/prod_before.txt" \
    > "${ARTIFACT_DIR}/prod_before_clean.txt" 2>/dev/null || true
grep -v "${PROJECT_NAME}" "${ARTIFACT_DIR}/prod_after.txt" \
    > "${ARTIFACT_DIR}/prod_after_clean.txt" 2>/dev/null || true

if diff -q "${ARTIFACT_DIR}/prod_before_clean.txt" \
           "${ARTIFACT_DIR}/prod_after_clean.txt" >/dev/null 2>&1; then
    success "Production containers: unchanged"
else
    warn "Production container state changed during smoke:"
    diff "${ARTIFACT_DIR}/prod_before_clean.txt" \
         "${ARTIFACT_DIR}/prod_after_clean.txt" | tee -a "${SMOKE_LOG}" || true
fi

# ── Step 25c: 최종 결과 설정 ─────────────────────────────────────────────────
# 모든 단계(seed/API/Worker 포함)가 통과한 경우에만 PASS 설정
# smoke runner 실패 시 SMOKE_RUNNER_EXIT != 0 이므로 FINAL_RESULT는 UNKNOWN 유지
if [ "${SMOKE_RUNNER_EXIT}" -eq 0 ]; then
    FINAL_RESULT="PASS"
fi

# Step 26: cleanup trap이 EXIT 시에 자동실행
# exit_code=$? + FINAL_RESULT 조합으로 최종 판정 → ALL PASS / FAIL 결정
