#!/bin/bash
# Stage 20 Server Verification Shell Script
# Run inside the creative-worker container:
#   docker exec -it creative-worker bash /app/scripts/verify_stage20_server.sh
#
# Or from host:
#   docker exec creative-worker bash /app/scripts/verify_stage20_server.sh

set -euo pipefail

HOST="${CREATIVE_WORKER_HOST:-http://localhost:5000}"
PASS_COUNT=0
FAIL_COUNT=0

check_pass() {
    echo "[OK] $1"
    PASS_COUNT=$((PASS_COUNT + 1))
}

check_fail() {
    echo "[NG] $1  ($2)"
    FAIL_COUNT=$((FAIL_COUNT + 1))
}

echo "=== Stage 20 Server Verification ==="
echo "Host: $HOST"
echo ""

# Step 1: Typography health endpoint
echo "-- Step 1: /v1/typography/health --"
STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$HOST/v1/typography/health" 2>/dev/null || echo "000")
if [ "$STATUS" = "200" ]; then
    check_pass "Step1_health_endpoint_200"
    BODY=$(curl -s "$HOST/v1/typography/health")
    if echo "$BODY" | grep -q "typographyPipelineEnabled"; then
        check_pass "Step1_health_has_enabled_flag"
    else
        check_fail "Step1_health_has_enabled_flag" "field missing: $BODY"
    fi
else
    check_fail "Step1_health_endpoint_200" "status=$STATUS"
    check_fail "Step1_health_has_enabled_flag" "endpoint failed"
fi

# Step 2: Stage 19 health still works (no regression)
echo "-- Step 2: /v1/background/health (Stage 19 regression) --"
STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$HOST/v1/background/health" 2>/dev/null || echo "000")
if [ "$STATUS" = "200" ]; then
    check_pass "Step2_stage19_health_not_broken"
else
    check_fail "Step2_stage19_health_not_broken" "status=$STATUS"
fi

# Step 3: Worker Python: typography module importable
echo "-- Step 3: Typography module import --"
IMPORT_OK=$(cd /app/worker && python -c "
import sys
try:
    from typography.pipeline import run_typography_pipeline
    from typography.role_resolver import classify_role_by_name, ROLE_ALIASES
    from typography.layout_templates import get_template
    print('ok')
except Exception as e:
    print(f'FAIL:{e}')
" 2>&1 || echo "FAIL:exec_error")
if [ "$IMPORT_OK" = "ok" ]; then
    check_pass "Step3_typography_module_import"
else
    check_fail "Step3_typography_module_import" "$IMPORT_OK"
fi

# Step 4: Role alias count (15 roles)
echo "-- Step 4: Role aliases --"
ROLE_COUNT=$(cd /app/worker && python -c "
from typography.role_resolver import ROLE_ALIASES
print(len(ROLE_ALIASES))
" 2>/dev/null || echo "0")
if [ "$ROLE_COUNT" = "15" ]; then
    check_pass "Step4_15_roles_defined"
else
    check_fail "Step4_15_roles_defined" "count=$ROLE_COUNT"
fi

# Step 5: Layout templates for all spec types
echo "-- Step 5: Layout template coverage --"
TEMPLATE_OK=$(cd /app/worker && python -c "
from typography.layout_templates import get_template, _spec_type
tests = [
    (1250, 560, '1250x560'),
    (1200, 628, 'horizontal'),
    (1000, 1000, 'square'),
    (600, 900, 'vertical'),
    (300, 1200, 'ultravert'),
    (2400, 600, 'ultrawide'),
]
failed = []
for w, h, expected in tests:
    name, slots = get_template(w, h, [])
    if expected not in name:
        failed.append(f'{w}x{h}:{name}!={expected}')
if failed:
    print('FAIL:' + ','.join(failed))
else:
    print('ok')
" 2>&1 || echo "FAIL:exec_error")
if [ "$TEMPLATE_OK" = "ok" ]; then
    check_pass "Step5_layout_templates_all_spec_types"
else
    check_fail "Step5_layout_templates_all_spec_types" "$TEMPLATE_OK"
fi

# Step 6: Pipeline disabled by default
echo "-- Step 6: Pipeline disabled by default --"
DISABLED_CHECK=$(cd /app/worker && python -c "
import os
os.environ.pop('TYPOGRAPHY_PIPELINE_ENABLED', None)
from typography.pipeline import run_typography_pipeline
r = run_typography_pipeline('/nonexistent.psd', 1000, 600, '/tmp/t.jpg')
print(r.get('error', ''))
" 2>/dev/null || echo "")
if [ "$DISABLED_CHECK" = "typography_pipeline_disabled" ]; then
    check_pass "Step6_pipeline_disabled_by_default"
else
    check_fail "Step6_pipeline_disabled_by_default" "error=$DISABLED_CHECK"
fi

# Step 7: Duplicate detector — group composite covers child
echo "-- Step 7: Duplicate detector --"
DEDUP_OK=$(cd /app/worker && python -c "
from typography.duplicate_detector import detect_duplicates
layers = [
    {'id': 'g', 'type': 'group', 'isTextLayer': False, 'isGroupComposite': True,
     'role': 'unknown', 'bbox': {'x': 0, 'y': 0, 'width': 400, 'height': 200}},
    {'id': 't', 'type': 'type', 'isTextLayer': True, 'isGroupComposite': False,
     'role': 'title', 'textContent': 'hello', 'fontSize': 18,
     'bbox': {'x': 10, 'y': 10, 'width': 200, 'height': 40}},
]
result = detect_duplicates(layers)
child = next(l for l in result if l['id'] == 't')
print('ok' if child['dedupSkip'] else 'FAIL')
" 2>/dev/null || echo "FAIL")
if [ "$DEDUP_OK" = "ok" ]; then
    check_pass "Step7_duplicate_detector_group_cover"
else
    check_fail "Step7_duplicate_detector_group_cover" "$DEDUP_OK"
fi

# Step 8: Quality gate scoring
echo "-- Step 8: Quality gate scoring --"
GATE_OK=$(cd /app/worker && python -c "
from typography.quality_gate import evaluate, LAYOUT_SCORE_THRESHOLD
from typography.schemas import LayoutSlot
classified = [
    {'role': 'background', 'dedupSkip': False, 'isKorean': False},
    {'role': 'main_image', 'dedupSkip': False, 'isKorean': False},
    {'role': 'title', 'dedupSkip': False, 'isKorean': True},
    {'role': 'cta', 'dedupSkip': False, 'isKorean': False},
]
slots = [
    LayoutSlot(role='background', x=0, y=0, w=1000, h=600, mode='cover'),
    LayoutSlot(role='main_image', x=0, y=50, w=500, h=500, mode='contain'),
    LayoutSlot(role='title', x=520, y=50, w=460, h=100, mode='contain'),
    LayoutSlot(role='cta', x=520, y=450, w=200, h=60, mode='contain'),
]
result = evaluate(classified, slots, 1000, 600, had_korean=True)
ok = result.success and result.quality_score >= LAYOUT_SCORE_THRESHOLD
print('ok' if ok else f'FAIL:score={result.quality_score}')
" 2>/dev/null || echo "FAIL")
if [ "$GATE_OK" = "ok" ]; then
    check_pass "Step8_quality_gate_full_pass_score"
else
    check_fail "Step8_quality_gate_full_pass_score" "$GATE_OK"
fi

# Summary
echo ""
echo "================================================"
echo "Total: $((PASS_COUNT + FAIL_COUNT))  PASS: $PASS_COUNT  FAIL: $FAIL_COUNT"

if [ "$FAIL_COUNT" -gt 0 ]; then
    echo "VERIFICATION FAILED"
    exit 1
else
    echo "ALL PASS"
    exit 0
fi
