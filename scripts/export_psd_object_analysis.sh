#!/usr/bin/env bash
# export_psd_object_analysis.sh
#
# MongoDB에서 PSD Object Analysis 결과를 Golden Batch fixture 형식으로 내보내는 스크립트.
# 실행 결과를 worker/analysis-fixtures/<psd-name>.json 으로 저장합니다.
#
# 사용법:
#   bash scripts/export_psd_object_analysis.sh [--mongo-uri URI] [--output-dir DIR]
#
# 예시:
#   bash scripts/export_psd_object_analysis.sh \
#     --mongo-uri mongodb://localhost:27017/creative_resizer \
#     --output-dir worker/analysis-fixtures
#
# 사전 요건:
#   - mongosh 또는 mongo CLI 설치
#   - PSD 파일이 이미 서버에서 분석되어 psd_object_analysis 컬렉션에 저장되어 있어야 함.

set -euo pipefail

MONGO_URI="${MONGODB_URI:-mongodb://localhost:27017/creative_resizer}"
OUTPUT_DIR="worker/analysis-fixtures"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mongo-uri) MONGO_URI="$2"; shift 2 ;;
    --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

mkdir -p "$OUTPUT_DIR"

echo "[EXPORT] MongoDB URI: ${MONGO_URI//:*@/:***@}"
echo "[EXPORT] Output dir: $OUTPUT_DIR"

# GOLDEN PSD 이름 목록
GOLDEN_PSDS=(
  "mother-hand-product"
  "야다화장품_네이버GFA"
)

for PSD_STEM in "${GOLDEN_PSDS[@]}"; do
  OUT_FILE="$OUTPUT_DIR/$PSD_STEM.json"
  echo "[EXPORT] Querying for psdPath containing: $PSD_STEM"

  # mongosh를 사용해 가장 최근 READY 문서를 조회
  mongosh "$MONGO_URI" --quiet --eval "
    const doc = db.psd_object_analysis.findOne(
      { psdPath: { \$regex: '${PSD_STEM}', \$options: 'i' }, status: 'READY' },
      {},
      { sort: { createdAt: -1 } }
    );
    if (!doc) {
      print('NOT_FOUND');
    } else {
      print(JSON.stringify(doc, null, 2));
    }
  " > "$OUT_FILE.tmp" 2>/dev/null || true

  if grep -q "NOT_FOUND" "$OUT_FILE.tmp" 2>/dev/null; then
    echo "[EXPORT] WARNING: No READY analysis found for $PSD_STEM — skipping"
    rm -f "$OUT_FILE.tmp"
    continue
  fi

  if [ -s "$OUT_FILE.tmp" ]; then
    mv "$OUT_FILE.tmp" "$OUT_FILE"
    echo "[EXPORT] Saved: $OUT_FILE"
  else
    echo "[EXPORT] WARNING: Empty result for $PSD_STEM — skipping"
    rm -f "$OUT_FILE.tmp"
  fi
done

echo "[EXPORT] Done. Review $OUTPUT_DIR and verify matchedLayerId fields before running Golden Batch."
