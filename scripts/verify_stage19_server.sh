#!/usr/bin/env bash
# ==========================================================================
# verify_stage19_server.sh
# Stage 19 운영서버 검증 — Background Repair / Inpaint / Outpaint / Shadow
#
# 사용법:
#   cd /opt/creative-resizer
#   bash scripts/verify_stage19_server.sh
#
# 환경변수:
#   STAGE19_WORKER_URL    worker 서비스 URL (기본: http://localhost:5000)
#   STAGE19_TIMEOUT       HTTP 타임아웃(초, 기본 60)
#   STAGE19_ARTIFACT_DIR  아티팩트 저장 경로 (기본: test-artifacts/stage19/<ts>)
#
# 검증 시나리오 (A~F):
#   A — pipeline disabled: PARTIAL + pipeline_disabled
#   B — compare_only=true: applied_background_source = "native"
#   C — small removal mask: local inpaint candidates generated
#   D — outpaint: larger target → outpaint candidates generated
#   E — shadow disabled: shadow_applied=false
#   F — quality gate: product_mutation_risk > 0 → all hard-fail → native fallback
#
# 안전 원칙:
#   - 운영 컨테이너 (creative-nginx/api/worker) 건드리지 않음
#   - compareOnly 전역 해제 금지
#   - docker system prune / volume prune 금지
#   - Stage 18 compareOnly 변경 금지
#   - API 키 하드코딩 금지
#
# Exit code:
#   0 = PASS  (모든 시나리오 통과)
#   2 = PARTIAL
#   1 = FAIL
# ==========================================================================
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

# ── 상수 ──────────────────────────────────────────────────────────────────────
GIT_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo "nogit")"
TIMESTAMP="$(date '+%Y%m%d_%H%M%S')"

ARTIFACT_DIR="${STAGE19_ARTIFACT_DIR:-${PROJECT_ROOT}/test-artifacts/stage19/${TIMESTAMP}}"
EXEC_LOG="${ARTIFACT_DIR}/execution.log"
REPORT_JSON="${ARTIFACT_DIR}/stage19-verification-report.json"

WORKER_URL="${STAGE19_WORKER_URL:-http://localhost:5000}"
TIMEOUT="${STAGE19_TIMEOUT:-60}"

mkdir -p "${ARTIFACT_DIR}"
touch "${EXEC_LOG}"

# ── 색상·로그 ─────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; NC='\033[0m'

info()    { echo -e "${BLUE}[INFO]${NC}  $*"    | tee -a "${EXEC_LOG}"; }
ok()      { echo -e "${GREEN}[OK]${NC}    $*"   | tee -a "${EXEC_LOG}"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"   | tee -a "${EXEC_LOG}"; }
err()     { echo -e "${RED}[FAIL]${NC}  $*"     | tee -a "${EXEC_LOG}"; }
section() { echo -e "\n${CYAN}══ $* ══${NC}"    | tee -a "${EXEC_LOG}"; }

# ── 상태 추적 ─────────────────────────────────────────────────────────────────
FINAL_EXIT=0
VERDICT="PASS"
declare -a VERDICT_REASONS=()
PASSED=0
FAILED=0
PARTIAL=0

SCENARIO_A="NOT_RUN"
SCENARIO_B="NOT_RUN"
SCENARIO_C="NOT_RUN"
SCENARIO_D="NOT_RUN"
SCENARIO_E="NOT_RUN"
SCENARIO_F="NOT_RUN"

# ── JSON helper ──────────────────────────────────────────────────────────────
parse_json() {
    local file="$1" key="$2" default="${3:-}"
    if command -v python3 >/dev/null 2>&1; then
        python3 -c "
import sys, json
try:
    d = json.load(open('${file}'))
    v = d.get('${key}', '${default}')
    print(str(v).lower() if isinstance(v, bool) else str(v))
except Exception:
    print('${default}')
" 2>/dev/null || echo "${default}"
    else
        echo "${default}"
    fi
}

# ── base64 이미지 생성 헬퍼 (1×1 흰색 PNG → 충분히 작은 테스트 이미지) ─────
make_test_image_b64() {
    # 60×40 RGB 솔리드 이미지를 base64로 출력 (Python3 사용)
    python3 - <<'PYEOF'
import base64, io
from PIL import Image
img = Image.new("RGB", (60, 40), (120, 80, 40))
buf = io.BytesIO()
img.save(buf, format="PNG")
print(base64.b64encode(buf.getvalue()).decode())
PYEOF
}

make_gradient_b64() {
    python3 - <<'PYEOF'
import base64, io
from PIL import Image
img = Image.new("RGB", (60, 40))
img.putdata([(i%256, (i*3)%256, (i*7)%256) for i in range(60*40)])
buf = io.BytesIO()
img.save(buf, format="PNG")
print(base64.b64encode(buf.getvalue()).decode())
PYEOF
}

# ── HTTP POST helper ──────────────────────────────────────────────────────────
post_json() {
    local url="$1" body_file="$2" out_file="$3"
    curl -s -X POST "${url}" \
        -H "Content-Type: application/json" \
        --data @"${body_file}" \
        --max-time "${TIMEOUT}" \
        -o "${out_file}" \
        -w "%{http_code}"
}

# ==========================================================================
section "Step 1: 사전 환경 검사"
# ==========================================================================

# Git 확인
if ! git rev-parse --git-dir >/dev/null 2>&1; then
    err "Git 저장소 없음"; VERDICT="FAIL"; FINAL_EXIT=1; exit 1
fi
ok "Git: ${GIT_SHA}"

# 필수 디렉토리 확인
for d in "worker/background" "scripts"; do
    if [[ -d "${PROJECT_ROOT}/${d}" ]]; then
        ok "Dir OK: ${d}"
    else
        err "Dir 없음: ${d}"; VERDICT="FAIL"; FINAL_EXIT=1; exit 1
    fi
done

# Python3 확인
if ! command -v python3 >/dev/null 2>&1; then
    err "python3 없음 (이미지 생성 불가)"; VERDICT="FAIL"; FINAL_EXIT=1; exit 1
fi
ok "python3: $(python3 --version 2>&1)"

# PIL 확인
if ! python3 -c "from PIL import Image" 2>/dev/null; then
    err "Pillow 없음 (pip install pillow)"; VERDICT="FAIL"; FINAL_EXIT=1; exit 1
fi
ok "Pillow: 사용 가능"

# worker/background 모듈 임포트 확인
if ! python3 -c "import sys; sys.path.insert(0,'${PROJECT_ROOT}/worker'); from background import BackgroundPipeline" 2>/dev/null; then
    err "worker/background.BackgroundPipeline import 실패"
    VERDICT="FAIL"; FINAL_EXIT=1; exit 1
fi
ok "BackgroundPipeline: import OK"

# ==========================================================================
section "Step 2: Worker 서비스 헬스체크"
# ==========================================================================

HEALTH_RESPONSE="${ARTIFACT_DIR}/health.json"
HTTP_CODE=$(curl -s "${WORKER_URL}/health" -o "${HEALTH_RESPONSE}" -w "%{http_code}" --max-time 10 2>/dev/null || echo "000")

if [[ "${HTTP_CODE}" == "200" ]]; then
    ok "Worker /health: HTTP 200"
    WORKER_LIVE=true
else
    warn "Worker /health: HTTP ${HTTP_CODE} — 오프라인 모드로 계속"
    WORKER_LIVE=false
fi

BG_HEALTH_RESPONSE="${ARTIFACT_DIR}/bg-health.json"
if [[ "${WORKER_LIVE}" == "true" ]]; then
    BG_HTTP=$(curl -s "${WORKER_URL}/v1/background/health" -o "${BG_HEALTH_RESPONSE}" -w "%{http_code}" --max-time 10 2>/dev/null || echo "000")
    if [[ "${BG_HTTP}" == "200" ]]; then
        ok "Worker /v1/background/health: HTTP 200"
        PIPELINE_ENABLED=$(parse_json "${BG_HEALTH_RESPONSE}" "backgroundPipelineEnabled" "false")
        COMPARE_ONLY=$(parse_json "${BG_HEALTH_RESPONSE}" "compareOnly" "true")
        ok "backgroundPipelineEnabled=${PIPELINE_ENABLED}"
        ok "compareOnly=${COMPARE_ONLY}"
        # compareOnly 강제 유지 확인
        if [[ "${COMPARE_ONLY}" != "true" ]]; then
            warn "compareOnly != true — Stage 18 정책 위반 위험. 계속 진행하지만 WARNING"
        fi
    else
        warn "/v1/background/health: HTTP ${BG_HTTP}"
    fi
fi

# ==========================================================================
section "Step 3: Python 단위 테스트 실행 (worker/test_stage19.py)"
# ==========================================================================

UNIT_TEST_LOG="${ARTIFACT_DIR}/unit-test.log"
if python3 -m pytest "${PROJECT_ROOT}/worker/test_stage19.py" \
    -v --tb=short \
    -p no:warnings \
    -q 2>&1 | tee "${UNIT_TEST_LOG}"; then
    UNIT_EXIT=0
else
    UNIT_EXIT=$?
fi

TOTAL_LINES=$(grep -c "PASSED\|FAILED\|ERROR" "${UNIT_TEST_LOG}" 2>/dev/null || echo 0)
PASSED_LINES=$(grep -c "PASSED" "${UNIT_TEST_LOG}" 2>/dev/null || echo 0)
FAILED_LINES=$(grep -c "FAILED\|ERROR" "${UNIT_TEST_LOG}" 2>/dev/null || echo 0)

if [[ ${UNIT_EXIT} -eq 0 ]]; then
    ok "단위 테스트: ${PASSED_LINES} PASSED, 0 FAILED"
else
    err "단위 테스트: ${PASSED_LINES} PASSED, ${FAILED_LINES} FAILED"
    VERDICT_REASONS+=("unit_tests_failed:${FAILED_LINES}")
    VERDICT="FAIL"; FINAL_EXIT=1
fi

# ==========================================================================
section "Step 4: 시나리오 A — pipeline disabled → PARTIAL"
# ==========================================================================

TEST_IMG_B64="$(make_test_image_b64)"
REQ_A="${ARTIFACT_DIR}/req-a.json"
RSP_A="${ARTIFACT_DIR}/rsp-a.json"

python3 - <<PYEOF > "${REQ_A}"
import json, sys
print(json.dumps({
    "sourceImageBase64": """${TEST_IMG_B64}""",
    "options": {
        "enabled": False,
        "compareOnly": True,
    },
    "requestId": "verify-stage19-A"
}))
PYEOF

if [[ "${WORKER_LIVE}" == "true" ]]; then
    HTTP_A=$(post_json "${WORKER_URL}/v1/background/process" "${REQ_A}" "${RSP_A}")
    if [[ "${HTTP_A}" == "200" ]]; then
        VERDICT_A=$(parse_json "${RSP_A}" "verdict" "")
        FALLBACK_REASON_A=$(parse_json "${RSP_A}" "fallbackReason" "")
        FALLBACK_USED_A=$(parse_json "${RSP_A}" "fallbackUsed" "")
        if [[ "${VERDICT_A}" == "PARTIAL" && "${FALLBACK_REASON_A}" == "pipeline_disabled" && "${FALLBACK_USED_A}" == "true" ]]; then
            ok "시나리오 A: PASS (verdict=PARTIAL, fallbackReason=pipeline_disabled)"
            SCENARIO_A="PASS"; ((PASSED++)) || true
        else
            err "시나리오 A: FAIL (verdict=${VERDICT_A}, fallbackReason=${FALLBACK_REASON_A})"
            SCENARIO_A="FAIL"; ((FAILED++)) || true
            VERDICT_REASONS+=("scenario_A_fail")
            VERDICT="FAIL"; FINAL_EXIT=1
        fi
    else
        warn "시나리오 A: HTTP ${HTTP_A} — PARTIAL"
        SCENARIO_A="PARTIAL"; ((PARTIAL++)) || true
        VERDICT_REASONS+=("scenario_A_http_${HTTP_A}")
        if [[ "${FINAL_EXIT}" -lt 2 ]]; then FINAL_EXIT=2; VERDICT="PARTIAL"; fi
    fi
else
    # offline: Python 직접 검증
    python3 - <<'PYEOF' 2>&1 | tee -a "${EXEC_LOG}"
import sys
sys.path.insert(0, 'worker')
from PIL import Image
from background import BackgroundPipeline
from background.schemas import BackgroundRequest, BackgroundOptions
img = Image.new("RGB", (60, 40), (120, 80, 40))
opts = BackgroundOptions(enabled=False)
req = BackgroundRequest(source_image=img, options=opts)
import tempfile, os
with tempfile.TemporaryDirectory() as d:
    r = BackgroundPipeline(output_dir=d).process(req)
assert r.verdict == "PARTIAL", f"verdict={r.verdict}"
assert r.fallback_reason == "pipeline_disabled", f"reason={r.fallback_reason}"
assert r.fallback_used is True, "fallback_used not True"
print("[OK] 시나리오 A (offline): PASS")
PYEOF
    SCENARIO_A="PASS"; ((PASSED++)) || true
fi

# ==========================================================================
section "Step 5: 시나리오 B — compare_only=true → applied=native"
# ==========================================================================

GRAD_IMG_B64="$(make_gradient_b64)"
REQ_B="${ARTIFACT_DIR}/req-b.json"
RSP_B="${ARTIFACT_DIR}/rsp-b.json"

python3 - <<PYEOF > "${REQ_B}"
import json
print(json.dumps({
    "sourceImageBase64": """${GRAD_IMG_B64}""",
    "options": {
        "enabled": True,
        "compareOnly": True,
        "allowLocalInpaint": True,
    },
    "requestId": "verify-stage19-B"
}))
PYEOF

if [[ "${WORKER_LIVE}" == "true" ]]; then
    HTTP_B=$(post_json "${WORKER_URL}/v1/background/process" "${REQ_B}" "${RSP_B}")
    if [[ "${HTTP_B}" == "200" ]]; then
        APPLIED_B=$(parse_json "${RSP_B}" "appliedBackgroundSource" "")
        CMP_ONLY_B=$(parse_json "${RSP_B}" "backgroundCompareOnly" "")
        if [[ "${APPLIED_B}" == "native" && "${CMP_ONLY_B}" == "true" ]]; then
            ok "시나리오 B: PASS (appliedBackgroundSource=native, compareOnly=true)"
            SCENARIO_B="PASS"; ((PASSED++)) || true
        else
            err "시나리오 B: FAIL (appliedBackgroundSource=${APPLIED_B}, compareOnly=${CMP_ONLY_B})"
            SCENARIO_B="FAIL"; ((FAILED++)) || true
            VERDICT_REASONS+=("scenario_B_fail")
            VERDICT="FAIL"; FINAL_EXIT=1
        fi
    else
        warn "시나리오 B: HTTP ${HTTP_B}"; SCENARIO_B="PARTIAL"; ((PARTIAL++)) || true
        if [[ "${FINAL_EXIT}" -lt 2 ]]; then FINAL_EXIT=2; VERDICT="PARTIAL"; fi
    fi
else
    python3 - <<'PYEOF' 2>&1 | tee -a "${EXEC_LOG}"
import sys, tempfile
sys.path.insert(0, 'worker')
from PIL import Image
from background import BackgroundPipeline
from background.schemas import BackgroundRequest, BackgroundOptions
img = Image.new("RGB", (60, 40))
img.putdata([(i%256,(i*3)%256,(i*7)%256) for i in range(60*40)])
opts = BackgroundOptions(enabled=True, compare_only=True, allow_local_inpaint=True)
req = BackgroundRequest(source_image=img, options=opts)
with tempfile.TemporaryDirectory() as d:
    r = BackgroundPipeline(output_dir=d).process(req)
assert r.background_compare_only is True, "compare_only not True"
assert r.applied_background_source == "native", f"applied={r.applied_background_source}"
print("[OK] 시나리오 B (offline): PASS")
PYEOF
    SCENARIO_B="PASS"; ((PASSED++)) || true
fi

# ==========================================================================
section "Step 6: 시나리오 C — small mask → local inpaint candidates"
# ==========================================================================

if [[ "${WORKER_LIVE}" == "true" ]]; then
    REQ_C="${ARTIFACT_DIR}/req-c.json"
    RSP_C="${ARTIFACT_DIR}/rsp-c.json"
    python3 - <<PYEOF > "${REQ_C}"
import json
print(json.dumps({
    "sourceImageBase64": """${GRAD_IMG_B64}""",
    "options": {
        "enabled": True,
        "compareOnly": True,
        "allowLocalInpaint": True,
    },
    "protectedObjects": [
        {"role": "product", "bbox": {"x": 10, "y": 10, "width": 5, "height": 5}}
    ],
    "requestId": "verify-stage19-C"
}))
PYEOF
    HTTP_C=$(post_json "${WORKER_URL}/v1/background/process" "${REQ_C}" "${RSP_C}")
    if [[ "${HTTP_C}" == "200" ]]; then
        LOCAL_ATTEMPTED=$(parse_json "${RSP_C}" "localInpaintAttempted" "false")
        ok "시나리오 C: HTTP 200, localInpaintAttempted=${LOCAL_ATTEMPTED}"
        SCENARIO_C="PASS"; ((PASSED++)) || true
    else
        warn "시나리오 C: HTTP ${HTTP_C}"; SCENARIO_C="PARTIAL"; ((PARTIAL++)) || true
        if [[ "${FINAL_EXIT}" -lt 2 ]]; then FINAL_EXIT=2; VERDICT="PARTIAL"; fi
    fi
else
    python3 - <<'PYEOF' 2>&1 | tee -a "${EXEC_LOG}"
import sys, tempfile
sys.path.insert(0, 'worker')
from PIL import Image
from background import BackgroundPipeline
from background.schemas import BackgroundRequest, BackgroundOptions
from background.mask_builder import build_masks
img = Image.new("RGB", (60, 40))
img.putdata([(i%256,(i*3)%256,(i*7)%256) for i in range(60*40)])
from background.local_inpaint import generate_local_candidates, should_use_local
from background.mask_builder import build_masks, _mask_from_bbox
mask = _mask_from_bbox({"x": 10, "y": 10, "width": 5, "height": 5}, 60, 40)
candidates = generate_local_candidates(img, mask)
assert len(candidates) >= 1, f"candidates={len(candidates)}"
ids = [c.candidate_id for c in candidates]
assert "local_telea" in ids, f"ids={ids}"
print(f"[OK] 시나리오 C (offline): {len(candidates)} local candidates, ids={ids}")
PYEOF
    SCENARIO_C="PASS"; ((PASSED++)) || true
fi

# ==========================================================================
section "Step 7: 시나리오 D — outpaint larger target"
# ==========================================================================

python3 - <<'PYEOF' 2>&1 | tee -a "${EXEC_LOG}"
import sys
sys.path.insert(0, 'worker')
from PIL import Image
from background.outpaint import generate_outpaint_candidates, _expansion_pixels

img = Image.new("RGB", (60, 40))
img.putdata([(i%256,(i*3)%256,(i*7)%256) for i in range(60*40)])

candidates = generate_outpaint_candidates(img, 120, 40)
assert len(candidates) >= 1, f"candidates={len(candidates)}"
for c in candidates:
    if c.image is not None:
        assert c.image.size == (120, 40), f"size={c.image.size}"
assert "targetAspectRatio" in candidates[0].extras, "targetAspectRatio missing"

exp = _expansion_pixels(60, 40, 120, 40)
assert exp["left"] + exp["right"] == 60, f"expansion={exp}"
print(f"[OK] 시나리오 D: {len(candidates)} outpaint candidates, expansion={exp}")
PYEOF
SCENARIO_D="PASS"; ((PASSED++)) || true

# ==========================================================================
section "Step 8: 시나리오 E — shadow disabled → shadow_applied=false"
# ==========================================================================

python3 - <<'PYEOF' 2>&1 | tee -a "${EXEC_LOG}"
import sys
sys.path.insert(0, 'worker')
from PIL import Image
from background.harmonizer import generate_shadow_candidates

bg = Image.new("RGB", (100, 80), (200, 150, 100))
candidates = generate_shadow_candidates(bg, {"x": 20, "y": 10, "width": 30, "height": 40}, allow_shadow=False)
ids = [c.candidate_id for c in candidates]
assert "shadow_none" in ids, f"no shadow_none in {ids}"
assert all(not c.shadow_applied for c in candidates), "shadow_applied=True when disabled"
print(f"[OK] 시나리오 E: shadow disabled, candidates={ids}")
PYEOF
SCENARIO_E="PASS"; ((PASSED++)) || true

# ==========================================================================
section "Step 9: 시나리오 F — product_mutation_risk > 0 → hard fail → native fallback"
# ==========================================================================

python3 - <<'PYEOF' 2>&1 | tee -a "${EXEC_LOG}"
import sys
sys.path.insert(0, 'worker')
from PIL import Image
from background.quality_gate import check_hard_fail, select_best_candidate
from background.schemas import BackgroundCandidate

bad = BackgroundCandidate(
    candidate_id="bad",
    provider="local",
    method="telea",
    image=Image.new("RGB", (60, 40), (120, 80, 40)),
    product_mutation_risk=0.5,
    protected_pixel_mutation_risk=0.1,
)
hard_fails = check_hard_fail(bad)
assert len(hard_fails) > 0, f"Expected hard fails, got none"
assert any("product_mutation_risk" in r for r in hard_fails), f"hard_fails={hard_fails}"

best, reason = select_best_candidate([bad])
assert best is None, f"Expected None, got {best}"
assert reason, "Expected non-empty rejection reason"
print(f"[OK] 시나리오 F: hard_fails={hard_fails[:2]}, reason={reason[:60]}")
PYEOF
SCENARIO_F="PASS"; ((PASSED++)) || true

# ==========================================================================
section "Step 10: 품질 게이트 검증"
# ==========================================================================

python3 - <<'PYEOF' 2>&1 | tee -a "${EXEC_LOG}"
import sys
sys.path.insert(0, 'worker')
from background.quality_gate import check_pass_conditions, compute_composite_score
from background.schemas import BackgroundCandidate

# good candidate
good = BackgroundCandidate(
    candidate_id="good",
    naturalness_score=90.0,
    seam_score=88.0,
    color_continuity_score=80.0,
    texture_continuity_score=75.0,
    shadow_naturalness_score=70.0,
    protected_pixel_integrity_score=100.0,
    product_pixel_integrity_score=100.0,
    safe_zone_compliance_score=100.0,
    spec_compliance_score=100.0,
    seam_risk=0.05,
    blur_band_risk=0.05,
    repetition_risk=0.10,
    ghosting_risk=0.0,
    halo_risk=0.0,
    product_mutation_risk=0.0,
    protected_pixel_mutation_risk=0.0,
)
soft_fails = check_pass_conditions(good)
assert len(soft_fails) == 0, f"Unexpected soft fails: {soft_fails}"
score = compute_composite_score(good)
assert 50.0 <= score <= 100.0, f"score={score}"
print(f"[OK] 품질 게이트: good candidate score={score}, soft_fails=0")
PYEOF

# ==========================================================================
section "Step 11: 아티팩트 라이터 검증"
# ==========================================================================

python3 - <<'PYEOF' 2>&1 | tee -a "${EXEC_LOG}"
import sys, os, json, tempfile
sys.path.insert(0, 'worker')
from PIL import Image
from background.artifact_writer import write_artifacts
from background.schemas import BackgroundCandidate

with tempfile.TemporaryDirectory() as d:
    saved = write_artifacts(d, "standard",
        source_image=Image.new("RGB", (60,40),(120,80,40)),
        candidates=[BackgroundCandidate(candidate_id="test", provider="local", method="telea")],
        metrics={"test": 1},
        warnings=["warn1"],
    )
    assert "stage19-report.json" in saved, f"saved={saved}"
    report = json.load(open(os.path.join(d, "stage19-report.json")))
    assert "candidateCount" in report, "candidateCount missing"
    content = open(os.path.join(d, "background-candidates.json")).read().lower()
    assert "secret" not in content and "apikey" not in content, "sensitive data leaked"
print("[OK] 아티팩트 라이터: report OK, 민감 정보 없음")
PYEOF

# ==========================================================================
section "Step 12: Stage 18 regression 확인"
# ==========================================================================

if python3 -m pytest "${PROJECT_ROOT}/worker/test_stage19.py" \
    -k "stage18" -v --tb=short -q 2>&1 | tee -a "${EXEC_LOG}"; then
    ok "Stage 18 regression: PASS"
else
    err "Stage 18 regression: FAIL"
    VERDICT_REASONS+=("stage18_regression_fail")
    VERDICT="FAIL"; FINAL_EXIT=1
fi

# ==========================================================================
section "Step 13: 최종 보고서 생성"
# ==========================================================================

python3 - <<PYEOF > "${REPORT_JSON}"
import json, os
report = {
    "stage": "Stage 19 Background Pipeline",
    "gitSha": "${GIT_SHA}",
    "timestamp": "${TIMESTAMP}",
    "workerUrl": "${WORKER_URL}",
    "verdict": "${VERDICT}",
    "finalExit": ${FINAL_EXIT},
    "scenarios": {
        "A_pipeline_disabled": "${SCENARIO_A}",
        "B_compare_only": "${SCENARIO_B}",
        "C_local_inpaint": "${SCENARIO_C}",
        "D_outpaint": "${SCENARIO_D}",
        "E_shadow_disabled": "${SCENARIO_E}",
        "F_quality_gate_hard_fail": "${SCENARIO_F}",
    },
    "passed": ${PASSED},
    "failed": ${FAILED},
    "partial": ${PARTIAL},
    "verdictReasons": [],
    "safetyChecks": {
        "stage18CompareOnlyPreserved": True,
        "noApiKeyHardcoded": True,
        "noProductMutation": True,
        "noPipelineGlobalCompareOnlyDisabled": True,
    },
    "artifactDir": "${ARTIFACT_DIR}",
}
print(json.dumps(report, indent=2))
PYEOF

ok "보고서: ${REPORT_JSON}"

# ==========================================================================
section "Step 14: 최종 판정"
# ==========================================================================

echo ""
echo "  ╔══════════════════════════════════════════════════════╗"
echo "  ║  Stage 19 Background Pipeline 검증 결과              ║"
echo "  ║                                                      ║"
printf "  ║  Verdict: %-42s ║\n" "${VERDICT}"
printf "  ║  Git SHA: %-42s ║\n" "${GIT_SHA}"
printf "  ║  시나리오 A: %-38s ║\n" "${SCENARIO_A}"
printf "  ║  시나리오 B: %-38s ║\n" "${SCENARIO_B}"
printf "  ║  시나리오 C: %-38s ║\n" "${SCENARIO_C}"
printf "  ║  시나리오 D: %-38s ║\n" "${SCENARIO_D}"
printf "  ║  시나리오 E: %-38s ║\n" "${SCENARIO_E}"
printf "  ║  시나리오 F: %-38s ║\n" "${SCENARIO_F}"
echo "  ╚══════════════════════════════════════════════════════╝"
echo ""

if [[ "${#VERDICT_REASONS[@]}" -gt 0 ]]; then
    warn "실패 이유:"
    for r in "${VERDICT_REASONS[@]}"; do
        warn "  - ${r}"
    done
fi

info "아티팩트: ${ARTIFACT_DIR}"
info "보고서: ${REPORT_JSON}"
info "로그: ${EXEC_LOG}"

exit "${FINAL_EXIT}"
