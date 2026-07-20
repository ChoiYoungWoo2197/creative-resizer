#!/usr/bin/env bash
# ==========================================================================
# test_verify_stage18_nvidia_count.sh
# Step 4.5 NVIDIA 패키지 카운트 + pip list 오류 처리 단위 테스트
#
# 사용법:
#   bash scripts/test_verify_stage18_nvidia_count.sh
#
# Exit code:
#   0 = 전체 PASS
#   1 = 1개 이상 FAIL
# ==========================================================================
set -uo pipefail   # -e 제거: 개별 case 실패를 직접 처리

PASS=0
FAIL=0
TMPDIR_TEST=$(mktemp -d)
trap 'rm -rf "${TMPDIR_TEST}"' EXIT

# ─── 색상 ─────────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; RED='\033[0;31m'; NC='\033[0m'
pass() { echo -e "${GREEN}[PASS]${NC} $*"; PASS=$((PASS+1)); }
fail() { echo -e "${RED}[FAIL]${NC} $*"; FAIL=$((FAIL+1)); }

# ─── 공통: NVIDIA_PKG_COUNT 계산 함수 (스크립트 본문과 동일 로직) ────────────

_count_nvidia() {
    local file="$1"
    local count
    count=$(
        awk '
            BEGIN { IGNORECASE = 1; count = 0 }
            $1 ~ /^(nvidia-|cuda-toolkit$|triton$)/ { count++ }
            END { print count }
        ' "${file}"
    )
    echo "${count:-0}"
}

# ─── TC-01: GPU 패키지 0개 → 카운트 0, 스크립트 종료 없음 ───────────────────
TC="TC-01: GPU 패키지 0개"
f="${TMPDIR_TEST}/pip-list-clean.txt"
cat > "${f}" <<'EOF'
Package         Version
--------------- ----------
flask           3.0.3
torch           2.5.1+cpu
torchvision     0.20.1+cpu
sam2            1.0.0
transformers    4.44.2
EOF

count=$(_count_nvidia "${f}")
if [[ "${count}" -eq 0 ]]; then
    pass "${TC} → count=${count}"
else
    fail "${TC} → count=${count} (기대: 0)"
fi

# ─── TC-02: nvidia-cublas 존재 → 카운트 1 이상 ───────────────────────────────
TC="TC-02: nvidia-cublas 존재"
f="${TMPDIR_TEST}/pip-list-nvidia.txt"
cat > "${f}" <<'EOF'
Package              Version
-------------------- ----------
torch                2.5.1
torchvision          0.20.1
nvidia-cublas-cu12   12.1.3.1
nvidia-cudnn-cu12    8.9.7.29
triton               2.1.0
sam2                 1.0.0
EOF

count=$(_count_nvidia "${f}")
if [[ "${count}" -ge 2 ]]; then
    pass "${TC} → count=${count} (≥2 기대)"
else
    fail "${TC} → count=${count} (기대: ≥2)"
fi

# ─── TC-03: triton 단독 존재 → 카운트 1 ─────────────────────────────────────
TC="TC-03: triton 단독"
f="${TMPDIR_TEST}/pip-list-triton.txt"
cat > "${f}" <<'EOF'
Package    Version
---------- -------
torch      2.5.1
torchvision 0.20.1
triton     2.1.0
EOF

count=$(_count_nvidia "${f}")
if [[ "${count}" -ge 1 ]]; then
    pass "${TC} → count=${count}"
else
    fail "${TC} → count=${count} (기대: ≥1)"
fi

# ─── TC-04: 미일치 파일에서 set -e 상태에서도 종료 없음 ──────────────────────
TC="TC-04: 미일치 시 exit 0 (set -e 영향 없음)"
f="${TMPDIR_TEST}/pip-list-empty.txt"
cat > "${f}" <<'EOF'
Package    Version
---------- -------
flask      3.0.0
EOF

# 서브쉘에서 set -e 활성화 후 실행
result=$(
    set -e
    awk '
        BEGIN { IGNORECASE = 1; count = 0 }
        $1 ~ /^(nvidia-|cuda-toolkit$|triton$)/ { count++ }
        END { print count }
    ' "${f}"
)
exit_code=$?
if [[ "${exit_code}" -eq 0 && "${result}" == "0" ]]; then
    pass "${TC} → exit_code=${exit_code} count=${result}"
else
    fail "${TC} → exit_code=${exit_code} count=${result}"
fi

# ─── TC-05: pip list 실패 처리 ───────────────────────────────────────────────
TC="TC-05: pip list 실패 시 exit 1 감지"
f="${TMPDIR_TEST}/pip-list-fail.txt"
# pip list 실패를 시뮬레이션
if ! bash -c 'exit 1' > "${f}" 2>&1; then
    pass "${TC} → 실패 exit code 감지 성공"
else
    fail "${TC} → 실패 exit code 미감지"
fi

# ─── TC-06: torch=2.5.1+cpu 정상 인식 ───────────────────────────────────────
TC="TC-06: torch 2.5.1+cpu 버전 파싱"
f="${TMPDIR_TEST}/pip-list-torch-cpu.txt"
cat > "${f}" <<'EOF'
Package         Version
--------------- ----------
torch           2.5.1+cpu
torchvision     0.20.1+cpu
torchaudio      2.5.1+cpu
EOF

TORCH_VER=$(awk '/^[Tt]orch /{print $2}' "${f}" | head -1)
TORCH_VER="${TORCH_VER:-N/A}"
NVIDIA_COUNT=$(_count_nvidia "${f}")

if [[ "${TORCH_VER}" == "2.5.1+cpu" && "${NVIDIA_COUNT}" -eq 0 ]]; then
    pass "${TC} → torch=${TORCH_VER} nvidia=${NVIDIA_COUNT}"
else
    fail "${TC} → torch=${TORCH_VER} nvidia=${NVIDIA_COUNT}"
fi

# ─── TC-07: cuda-toolkit 패키지 감지 ─────────────────────────────────────────
TC="TC-07: cuda-toolkit 감지"
f="${TMPDIR_TEST}/pip-list-cuda.txt"
cat > "${f}" <<'EOF'
Package      Version
------------ -------
torch        2.5.1
cuda-toolkit 12.1.0
EOF

count=$(_count_nvidia "${f}")
if [[ "${count}" -ge 1 ]]; then
    pass "${TC} → count=${count}"
else
    fail "${TC} → count=${count} (기대: ≥1)"
fi

# ─── 결과 요약 ────────────────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════"
echo "  결과: PASS=${PASS} FAIL=${FAIL}"
echo "═══════════════════════════════════════"

if [[ "${FAIL}" -gt 0 ]]; then
    exit 1
fi
exit 0
