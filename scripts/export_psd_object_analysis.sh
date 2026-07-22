#!/usr/bin/env bash
# export_psd_object_analysis.sh
#
# MongoDB의 psd_object_analysis 컬렉션에서 Golden Batch fixture JSON을 내보냅니다.
# 내보낸 파일명은 반드시 <sourceFileSha256>.json 형식을 사용합니다.
#
# 사용법:
#   bash scripts/export_psd_object_analysis.sh [OPTIONS]
#
# 옵션:
#   --mongo-uri URI        MongoDB URI (기본: $MONGODB_URI 또는 mongodb://localhost:27017/creative_resizer)
#   --output-dir DIR       출력 디렉토리 (기본: worker/analysis-fixtures)
#   --source-sha256 SHA    특정 sourceFileSha256으로 단건 Export (--analysis-id와 택일)
#   --analysis-id ID       특정 MongoDB _id로 단건 Export (--source-sha256와 택일)
#   --help                 이 도움말 출력
#
# 출력:
#   <output-dir>/<sourceFileSha256>.json
#     분석 문서에 sourceFileSha256이 있으면 그 값으로, 없으면 ObjectId hex로 파일명 생성.
#
# 주의:
#   - status=READY 문서만 내보냅니다.
#   - 내보낸 JSON에 __POPULATE_FROM_SERVER__ 값이 있으면 Export FAIL로 처리합니다.
#   - API Key, PSD 바이트, Prompt 원문은 파일에 저장하지 않습니다.
#
# 사전 요건:
#   - mongosh CLI 설치 (MongoDB Shell)
#   - PSD가 이미 서버에서 분석되어 status=READY 문서가 저장되어 있어야 함.
#
# 예시:
#   # sourceFileSha256 기반 단건 Export
#   bash scripts/export_psd_object_analysis.sh \
#     --mongo-uri mongodb://localhost:27017/creative_resizer \
#     --source-sha256 abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890 \
#     --output-dir worker/analysis-fixtures
#
#   # analysis-id 기반 단건 Export
#   bash scripts/export_psd_object_analysis.sh \
#     --analysis-id 66a1b2c3d4e5f6a7b8c9d0e1 \
#     --output-dir worker/analysis-fixtures

set -euo pipefail

# ── Defaults ──────────────────────────────────────────────────────────────────
MONGO_URI="${MONGODB_URI:-mongodb://localhost:27017/creative_resizer}"
OUTPUT_DIR="worker/analysis-fixtures"
SOURCE_SHA256=""
ANALYSIS_ID=""

# ── Argument parsing ──────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h)
      sed -n '2,50p' "$0" | grep '^#' | sed 's/^# \?//'
      exit 0
      ;;
    --mongo-uri)   MONGO_URI="$2";    shift 2 ;;
    --output-dir)  OUTPUT_DIR="$2";   shift 2 ;;
    --source-sha256) SOURCE_SHA256="$2"; shift 2 ;;
    --analysis-id)   ANALYSIS_ID="$2";   shift 2 ;;
    *)
      echo "[EXPORT] ERROR: Unknown argument: $1" >&2
      echo "         Run with --help for usage." >&2
      exit 1
      ;;
  esac
done

# ── Validation ────────────────────────────────────────────────────────────────
if [[ -n "$SOURCE_SHA256" && -n "$ANALYSIS_ID" ]]; then
  echo "[EXPORT] ERROR: --source-sha256 and --analysis-id are mutually exclusive." >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"

echo "[EXPORT] MongoDB URI: ${MONGO_URI//:*@/:***@}"
echo "[EXPORT] Output dir:  $OUTPUT_DIR"

# ── mongosh availability ──────────────────────────────────────────────────────
if ! command -v mongosh &>/dev/null; then
  echo "[EXPORT] ERROR: mongosh not found. Install MongoDB Shell." >&2
  exit 1
fi

# ── Query builder ─────────────────────────────────────────────────────────────
# Build the mongosh JS filter expression based on mode
if [[ -n "$ANALYSIS_ID" ]]; then
  echo "[EXPORT] Mode: single by analysis-id=$ANALYSIS_ID"
  MONGO_FILTER="{ _id: ObjectId('${ANALYSIS_ID}'), status: 'READY' }"
  EXPORT_LABEL="id:$ANALYSIS_ID"
elif [[ -n "$SOURCE_SHA256" ]]; then
  echo "[EXPORT] Mode: single by source-sha256=${SOURCE_SHA256:0:16}..."
  MONGO_FILTER="{ sourceFileSha256: '${SOURCE_SHA256}', status: 'READY' }"
  EXPORT_LABEL="sha256:${SOURCE_SHA256:0:16}"
else
  echo "[EXPORT] Mode: all READY documents"
  MONGO_FILTER="{ status: 'READY' }"
  EXPORT_LABEL="all"
fi

# ── Export function ───────────────────────────────────────────────────────────
# Writes one or more fixture JSON files from MongoDB query results.
# Returns number of documents exported.
do_export() {
  local filter="$1"
  local exported=0
  local failed=0

  # Use mongosh to export matching documents as newline-delimited JSON
  local tmp_out
  tmp_out=$(mktemp /tmp/export_psd_XXXXXX.ndjson)
  # shellcheck disable=SC2064
  trap "rm -f '$tmp_out'" EXIT

  mongosh "$MONGO_URI" --quiet --eval "
    const docs = db.psd_object_analysis.find($filter).sort({ updatedAt: -1 }).toArray();
    if (docs.length === 0) {
      print('__NO_DOCS__');
    } else {
      docs.forEach(d => print(JSON.stringify(d)));
    }
  " > "$tmp_out" 2>/dev/null || {
    echo "[EXPORT] ERROR: mongosh query failed." >&2
    return 1
  }

  if grep -qF '__NO_DOCS__' "$tmp_out"; then
    echo "[EXPORT] WARNING: No READY documents found for filter: $filter"
    return 0
  fi

  # Process each line (one JSON doc per line)
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    [[ "$line" == "__NO_DOCS__" ]] && continue

    # Extract sourceFileSha256 and _id for filename selection
    local sha256 doc_id
    sha256=$(echo "$line" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('sourceFileSha256',''))" 2>/dev/null || true)
    doc_id=$(echo "$line" | python3 -c "import sys,json; d=json.load(sys.stdin); oid=d.get('_id',{}); print(oid.get('\$oid', str(oid))[:24] if isinstance(oid,dict) else str(oid)[:24])" 2>/dev/null || true)

    # Choose output filename: SHA256 preferred, fallback to doc_id
    local out_name
    if [[ -n "$sha256" && "$sha256" != "__POPULATE_FROM_SERVER__" && ${#sha256} -ge 32 ]]; then
      out_name="${sha256}.json"
    elif [[ -n "$doc_id" ]]; then
      out_name="${doc_id}.json"
      echo "[EXPORT] WARNING: sourceFileSha256 missing/placeholder, using doc_id for filename"
    else
      out_name="unknown_$(date +%s).json"
      echo "[EXPORT] WARNING: cannot determine filename, using timestamp"
    fi

    local out_path="$OUTPUT_DIR/$out_name"

    # Write file (pretty-print)
    echo "$line" | python3 -c "
import sys, json
d = json.load(sys.stdin)
# Remove internal MongoDB fields we don't need in fixtures
for key in ('__v',):
    d.pop(key, None)
with open(sys.argv[1], 'w', encoding='utf-8') as f:
    json.dump(d, f, indent=2, ensure_ascii=False)
" "$out_path" 2>/dev/null || {
      echo "[EXPORT] ERROR: Failed to write $out_path" >&2
      (( failed += 1 ))
      continue
    }

    # Validate: no placeholders allowed in exported fixture
    local ph_count
    ph_count=$(grep -c '__POPULATE_FROM_SERVER__' "$out_path" 2>/dev/null || true)
    if [[ "$ph_count" -gt 0 ]]; then
      echo "[EXPORT] FAIL: Placeholder values found in $out_path ($ph_count occurrences)" >&2
      echo "         Ensure the document in MongoDB has real matchedLayerId values." >&2
      rm -f "$out_path"
      (( failed += 1 ))
      continue
    fi

    echo "[EXPORT] OK: $out_path"
    (( exported += 1 ))
  done < "$tmp_out"

  echo "[EXPORT] Exported: $exported  Failed: $failed"

  if [[ "$failed" -gt 0 ]]; then
    return 1
  fi
  return 0
}

# ── Run export ────────────────────────────────────────────────────────────────
echo "[EXPORT] Querying: filter=$MONGO_FILTER"
do_export "$MONGO_FILTER"
EXPORT_STATUS=$?

if [[ $EXPORT_STATUS -eq 0 ]]; then
  echo "[EXPORT] Done. Fixtures saved to: $OUTPUT_DIR"
  echo "[EXPORT] Run Golden Batch with: --analysis-dir $OUTPUT_DIR"
  exit 0
else
  echo "[EXPORT] Export completed with errors. See above for details." >&2
  exit 1
fi
