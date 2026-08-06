"""Per-spec pipeline type selector.

TYPE A: 원본과 규격 동일 → 캐노니컬 그대로 출력 (pass-through)
TYPE B: GPT-4o scene analysis + element extraction + layout repositioning
TYPE D: contain-scale + AI border outpaint only (no element analysis)

Selection logic (per spec, runs after P1 canonical load):
  tgt == src (치수 동일)          → TYPE A
  scale >= MIN_SCALE & ar <= MAX  → TYPE D (거의 같은 비율, 단순 확장)
  그 외                           → TYPE B

Note: safe zone은 P7 layout 단계에서 요소 배치 제약으로 사용.
      타입 선택 기준이 아니므로 라우팅에서 제외.

Environment variables:
  CLEAN_PIPELINE_TYPE   "A" | "B" | "D" | "auto"  (default: "auto")
  TYPE_D_MIN_SCALE      float (default: 0.75)
  TYPE_D_MAX_AR_RATIO   float (default: 1.10)
"""
from __future__ import annotations

import os

_FORCED_TYPE: str = os.environ.get("CLEAN_PIPELINE_TYPE", "auto").upper()
_VALID_FORCED = ("A", "B", "D")
TYPE_D_MIN_SCALE: float = float(os.environ.get("TYPE_D_MIN_SCALE", "0.75"))
TYPE_D_MAX_AR_RATIO: float = float(os.environ.get("TYPE_D_MAX_AR_RATIO", "1.10"))


def select(
    src_w: int, src_h: int, tgt_w: int, tgt_h: int,
    safe_left: int = 0, safe_top: int = 0,
    safe_right: int = 0, safe_bottom: int = 0,
) -> str:
    """Return 'A', 'B', or 'D' for this (source, target) pair.

    If CLEAN_PIPELINE_TYPE is 'A', 'B', or 'D', that value is returned directly.
    safe_left/top/right/bottom is accepted for signature compatibility but not
    used for routing — safe zone is a layout-stage constraint, not a type signal.
    """
    if _FORCED_TYPE in _VALID_FORCED:
        return _FORCED_TYPE

    # 원본과 규격 동일 → TYPE A (pass-through)
    if tgt_w == src_w and tgt_h == src_h:
        return "A"

    scale = min(tgt_w / src_w, tgt_h / src_h)
    src_ar = src_w / src_h
    tgt_ar = tgt_w / tgt_h
    ar_ratio = max(src_ar, tgt_ar) / min(src_ar, tgt_ar)

    # 거의 같은 비율로 단순 축소/확장 → TYPE D (contain-scale + outpaint)
    # 비율 차이가 크거나 scale이 작으면 → TYPE B (배경 생성 + 레이아웃)
    if scale >= TYPE_D_MIN_SCALE and ar_ratio <= TYPE_D_MAX_AR_RATIO:
        return "D"
    return "B"
