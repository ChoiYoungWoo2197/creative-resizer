"""Per-spec pipeline type selector.

TYPE A: 원본과 규격 동일 → 캐노니컬 그대로 출력 (pass-through)
TYPE G: PSD 레이어 기반 분해·재배치 (기본값)

Selection logic (per spec, runs after P1 canonical load):
  tgt == src (치수 동일)  → TYPE A
  그 외                   → TYPE G (기본)

Environment variables:
  CLEAN_PIPELINE_TYPE   "A" | "B" | "D" | "E" | "F" | "G" | "auto"  (default: "auto")
"""
from __future__ import annotations

import os

_FORCED_TYPE: str = os.environ.get("CLEAN_PIPELINE_TYPE", "auto").upper()
_VALID_FORCED = ("A", "B", "D", "E", "F", "G")


def select(
    src_w: int, src_h: int, tgt_w: int, tgt_h: int,
    safe_left: int = 0, safe_top: int = 0,
    safe_right: int = 0, safe_bottom: int = 0,
) -> str:
    """Return pipeline type for this (source, target) pair.

    If CLEAN_PIPELINE_TYPE is a valid type, that value is returned directly.
    safe_left/top/right/bottom is accepted for signature compatibility but not
    used for routing — safe zone is a layout-stage constraint, not a type signal.
    """
    if _FORCED_TYPE in _VALID_FORCED:
        return _FORCED_TYPE

    # 원본과 규격 동일 → TYPE A (pass-through)
    if tgt_w == src_w and tgt_h == src_h:
        return "A"

    return "G"
