"""Per-spec pipeline type selector.

TYPE A: 원본과 규격 동일 → 캐노니컬 그대로 출력 (pass-through)
TYPE B: GPT-4o scene analysis + element extraction + layout repositioning
TYPE D: contain-scale + AI border outpaint only (no element analysis)

Selection logic (per spec, runs after P1 canonical load):
  tgt == src (치수 동일)          → TYPE A
  safe zone 있음                  → TYPE D (세이프존 기준 축소)
  scale >= MIN_SCALE & ar <= MAX  → TYPE D (캔버스 기준 축소)
  그 외                           → TYPE B

Environment variables:
  CLEAN_PIPELINE_TYPE   "A" | "B" | "D" | "auto"  (default: "auto")
  TYPE_D_MIN_SCALE      float (default: 0.75)
  TYPE_D_MAX_AR_RATIO   float (default: 1.20)
"""
from __future__ import annotations

import os

_FORCED_TYPE: str = os.environ.get("CLEAN_PIPELINE_TYPE", "auto").upper()
_VALID_FORCED = ("A", "B", "D")
TYPE_D_MIN_SCALE: float = float(os.environ.get("TYPE_D_MIN_SCALE", "0.75"))
TYPE_D_MAX_AR_RATIO: float = float(os.environ.get("TYPE_D_MAX_AR_RATIO", "1.20"))


def select(
    src_w: int, src_h: int, tgt_w: int, tgt_h: int,
    safe_left: int = 0, safe_top: int = 0,
    safe_right: int = 0, safe_bottom: int = 0,
) -> str:
    """Return 'B' or 'D' for this (source, target) pair.

    If CLEAN_PIPELINE_TYPE is 'B' or 'D', that value is returned directly.
    Safe zone present → always TYPE D (safe-zone scale + outpaint).
    Otherwise auto-route based on scale + aspect-ratio change.
    """
    if _FORCED_TYPE in _VALID_FORCED:
        return _FORCED_TYPE

    # 원본과 규격 동일 → TYPE A (pass-through)
    if tgt_w == src_w and tgt_h == src_h:
        return "A"

    # 세이프존이 있으면 무조건 TYPE D (세이프존 기준 축소 + 외곽 AI 확장)
    if safe_left + safe_top + safe_right + safe_bottom > 0:
        return "D"

    scale = min(tgt_w / src_w, tgt_h / src_h)
    src_ar = src_w / src_h
    tgt_ar = tgt_w / tgt_h
    ar_ratio = max(src_ar, tgt_ar) / min(src_ar, tgt_ar)

    if scale >= TYPE_D_MIN_SCALE and ar_ratio <= TYPE_D_MAX_AR_RATIO:
        return "D"
    return "B"
