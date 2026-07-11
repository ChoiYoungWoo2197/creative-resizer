#!/usr/bin/env bash
# BannerSpec Stage 8 Smoke Test — Linux / macOS / CI
# Usage:
#   ./scripts/run-stage8-smoke.sh           # full run
#   ./scripts/run-stage8-smoke.sh --no-build    # skip image build
#   ./scripts/run-stage8-smoke.sh --skip-cleanup # keep containers for debug

set -euo pipefail

PROJECT="creative-resizer-stage8-smoke"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_F="${SCRIPT_DIR}/../docker-compose.smoke.yml"
NO_BUILD=0
SKIP_CLEANUP=0

for arg in "$@"; do
  case "$arg" in
    --no-build)     NO_BUILD=1 ;;
    --skip-cleanup) SKIP_CLEANUP=1 ;;
  esac
done

# ── Docker / Compose 확인 ────────────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
  echo "ERROR: docker not found in PATH" >&2
  exit 1
fi

if ! docker info &>/dev/null; then
  echo "ERROR: Docker daemon is not running" >&2
  exit 1
fi
echo "Docker daemon: UP"

# docker compose v2 우선, v1 fallback
if docker compose version &>/dev/null 2>&1; then
  COMPOSE="docker compose"
else
  COMPOSE="docker-compose"
fi
echo "Compose: $COMPOSE"

# ── 정리 함수 ────────────────────────────────────────────────────────────────
smoke_compose() {
  $COMPOSE -p "$PROJECT" -f "$COMPOSE_F" "$@"
}

cleanup() {
  if [ "$SKIP_CLEANUP" -eq 1 ]; then
    echo ""
    echo "[INFO] SkipCleanup: containers kept for inspection"
    echo "       $COMPOSE -p $PROJECT -f $COMPOSE_F logs"
    return
  fi
  echo ""
  echo "[INFO] Cleaning up smoke environment..."
  smoke_compose down -v --remove-orphans 2>/dev/null || true
  echo "[INFO] Cleanup done."
}

trap cleanup EXIT

# ── 1. 기존 smoke 정리 ───────────────────────────────────────────────────────
echo ""
echo "[1/5] Cleaning up previous smoke run..."
smoke_compose down -v --remove-orphans 2>/dev/null || true

# ── 2. 이미지 빌드 ───────────────────────────────────────────────────────────
if [ "$NO_BUILD" -eq 0 ]; then
  echo ""
  echo "[2/5] Building images (JDK17 compile + test inside Docker)..."
  if ! smoke_compose build --no-cache; then
    echo "[FAIL] Image build failed — JDK17 compile or test failed" >&2
    exit 1
  fi
  echo "[PASS] Image build succeeded (JDK17 compileJava + test PASS)"
else
  echo ""
  echo "[2/5] Skipping build (--no-build)"
fi

# ── 3. Smoke 실행 ─────────────────────────────────────────────────────────────
echo ""
echo "[3/5] Starting smoke environment..."
echo "      (MongoDB -> RabbitMQ -> API[JDK17] -> smoke runner)"

SMOKE_EXIT=0
smoke_compose up --abort-on-container-exit --exit-code-from smoke || SMOKE_EXIT=$?

# ── 4. 로그 수집 ─────────────────────────────────────────────────────────────
echo ""
echo "[4/5] Collecting logs..."
echo ""
echo "--- smoke runner log ---"
smoke_compose logs smoke 2>&1 | tail -80

if [ "$SMOKE_EXIT" -ne 0 ]; then
  echo ""
  echo "--- api log (last 40 lines) ---"
  smoke_compose logs --tail=40 api 2>&1

  echo ""
  echo "--- mongo log (last 20 lines) ---"
  smoke_compose logs --tail=20 mongo 2>&1

  echo ""
  echo "--- rabbitmq log (last 20 lines) ---"
  smoke_compose logs --tail=20 rabbitmq 2>&1
fi

# ── 결과 ─────────────────────────────────────────────────────────────────────
echo ""
if [ "$SMOKE_EXIT" -eq 0 ]; then
  echo "╔══════════════════════════════════════╗"
  echo "║  BannerSpec Stage 8 Smoke: PASS ✓   ║"
  echo "╚══════════════════════════════════════╝"
else
  echo "╔══════════════════════════════════════╗"
  echo "║  BannerSpec Stage 8 Smoke: FAIL ✗   ║"
  echo "╚══════════════════════════════════════╝"
fi
exit "$SMOKE_EXIT"
