"""Per-spec pipeline type selector.

TYPE B: GPT-4o scene analysis + element extraction + layout repositioning
TYPE D: contain-scale + AI border outpaint only (no element analysis)

Selection logic (per spec, runs after P1 canonical load):
  scale    = min(tgt_w/src_w, tgt_h/src_h)
  ar_ratio = max(src_ar, tgt_ar) / min(src_ar, tgt_ar)
  → TYPE D if scale >= TYPE_D_MIN_SCALE AND ar_ratio <= TYPE_D_MAX_AR_RATIO
  → TYPE B otherwise

Environment variables:
  CLEAN_PIPELINE_TYPE   "B" | "D" | "auto"  (default: "auto")
                        "B" or "D" forces the type for all specs.
                        "auto" applies per-spec selection.
  TYPE_D_MIN_SCALE      float (default: 0.75)
  TYPE_D_MAX_AR_RATIO   float (default: 1.20)
"""
from __future__ import annotations

import os

_FORCED_TYPE: str = os.environ.get("CLEAN_PIPELINE_TYPE", "auto").upper()
TYPE_D_MIN_SCALE: float = float(os.environ.get("TYPE_D_MIN_SCALE", "0.75"))
TYPE_D_MAX_AR_RATIO: float = float(os.environ.get("TYPE_D_MAX_AR_RATIO", "1.20"))


def select(src_w: int, src_h: int, tgt_w: int, tgt_h: int) -> str:
    """Return 'B' or 'D' for this (source, target) pair.

    If CLEAN_PIPELINE_TYPE is 'B' or 'D', that value is returned directly.
    Otherwise auto-route based on scale + aspect-ratio change.
    """
    if _FORCED_TYPE in ("B", "D"):
        return _FORCED_TYPE

    scale = min(tgt_w / src_w, tgt_h / src_h)
    src_ar = src_w / src_h
    tgt_ar = tgt_w / tgt_h
    ar_ratio = max(src_ar, tgt_ar) / min(src_ar, tgt_ar)

    if scale >= TYPE_D_MIN_SCALE and ar_ratio <= TYPE_D_MAX_AR_RATIO:
        return "D"
    return "B"
