#!/bin/sh
# Stage 8 Smoke Test — sequential runner
# BannerSpec HTTP API + Worker Contract (direct) を順次実行し、最終結果を集計する

BANNER_API_EXIT=0
WORKER_CONTRACT_EXIT=0

echo "════════════════════════════════════════════════════════"
echo " Stage 8 Smoke — BannerSpec API + Worker Contract"
echo "════════════════════════════════════════════════════════"
echo ""

echo "▶ Part 1: BannerSpec HTTP API Tests (Steps 1–12)"
python http_smoke_test.py
BANNER_API_EXIT=$?

echo ""
echo "▶ Part 2: Worker Contract Tests (Direct HTTP)"
python worker_contract_smoke_test.py
WORKER_CONTRACT_EXIT=$?

echo ""
echo "════════════════════════════════════════════════════════"
echo " Final Result"
echo "════════════════════════════════════════════════════════"

OVERALL=0
if [ $BANNER_API_EXIT -ne 0 ]; then
    OVERALL=1
fi
if [ $WORKER_CONTRACT_EXIT -ne 0 ]; then
    OVERALL=1
fi

if [ $OVERALL -eq 0 ]; then
    echo " ALL PASS"
else
    echo " FAIL"
    [ $BANNER_API_EXIT    -ne 0 ] && echo "   BannerSpec API  : FAIL (exit $BANNER_API_EXIT)"
    [ $WORKER_CONTRACT_EXIT -ne 0 ] && echo "   Worker Contract : FAIL (exit $WORKER_CONTRACT_EXIT)"
fi
echo "════════════════════════════════════════════════════════"

exit $OVERALL
