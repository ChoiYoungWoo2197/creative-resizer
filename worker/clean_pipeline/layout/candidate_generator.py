"""Generate deterministic placement candidates for each extracted object.

Rules:
  - No randomness; same input always produces same candidate list
  - Candidates are ordered (anchor-index asc, scale desc): [1.0, 0.9, 0.8, 0.7, 0.6]
  - Object dimensions come from source_bbox (canonical pixel size)
  - Anchors are defined per role; unlisted roles use ["top-center"]

Anchors:
  title_group    → top-left, top-center, top-right
  product        → left-center, right-center, center-left, center-right
  logo           → top-left, top-right
  cta_group      → bottom-left, bottom-center, bottom-right
  body_text_group→ top-left, top-center, top-right
  badge          → top-left, top-right
  decorative_ad  → left-center, right-center
"""
from __future__ import annotations

from clean_pipeline.extraction.models import ExtractedObject
from clean_pipeline.layout.models import ObjectCandidate, SafeZone

_ROLE_ANCHORS: dict[str, list[str]] = {
    # top 3종 + bottom 2종: 제품이 center에 배치된 후 텍스트가 하단으로 배치될 수 있도록
    "title_group":     ["top-left", "top-center", "top-right",
                        "bottom-left", "bottom-center", "bottom-right"],
    "product":         ["left-center", "right-center", "center-left", "center-right",
                        "top-left", "top-right"],
    "logo":            ["top-left", "top-right"],
    "cta_group":       ["bottom-left", "bottom-center", "bottom-right"],
    "body_text_group": ["top-left", "top-center", "top-right"],
    "badge":           ["top-left", "top-right"],
    "decorative_ad":   ["left-center", "right-center"],
}

_SCALES: list[float] = [1.0, 0.9, 0.8, 0.7, 0.6]

# Product is the hero element; ensure it covers at least this fraction of canvas width.
# If the source bbox is smaller, a boost scale is prepended so the picker tries the
# larger size first (falls back to 1.0+ if it doesn't fit the safe zone).
_PRODUCT_MIN_CANVAS_RATIO: float = 0.25


def _anchor_position(anchor: str, sz: SafeZone, w: int, h: int) -> tuple[int, int]:
    """Return (x, y) top-left position for the given anchor within the safe zone."""
    cx = sz.left + (sz.width - w) // 2
    cy = sz.top + (sz.height - h) // 2
    positions: dict[str, tuple[int, int]] = {
        "top-left":     (sz.left,            sz.top),
        "top-center":   (cx,                 sz.top),
        "top-right":    (sz.right - w,       sz.top),
        "left-center":  (sz.left,            cy),
        "right-center": (sz.right - w,       cy),
        "center-left":  (sz.left + sz.width  // 3 - w // 2, cy),
        "center-right": (sz.left + 2 * sz.width // 3 - w // 2, cy),
        "bottom-left":  (sz.left,            sz.bottom - h),
        "bottom-center":(cx,                 sz.bottom - h),
        "bottom-right": (sz.right - w,       sz.bottom - h),
    }
    return positions.get(anchor, (sz.left, sz.top))


def generate(
    extracted: list[ExtractedObject],
    safe_zone: SafeZone,
    image_width: int = 0,
) -> dict[str, list[ObjectCandidate]]:
    """Return {object_id: [candidates]} in deterministic order.

    Dimensions are taken directly from source_bbox (canonical pixel size).
    When image_width is given, anchors are reordered so that objects whose
    original centre-x was on the right half of the canvas try right-side
    anchors first (and vice versa).  This preserves the original layout
    intent without hard-coding positions.
    """
    result: dict[str, list[ObjectCandidate]] = {}
    for obj in extracted:
        base_anchors = list(_ROLE_ANCHORS.get(obj.role, ["top-center"]))

        if image_width > 0:
            src_cx = obj.source_bbox.x + obj.source_bbox.width / 2
            if src_cx > image_width / 2:
                # 원본이 우측 → right 계열 anchor 먼저
                base_anchors = _prefer_side(base_anchors, "right")
            else:
                # 원본이 좌측 → left 계열 anchor 먼저
                base_anchors = _prefer_side(base_anchors, "left")

        orig_w = obj.source_bbox.width
        orig_h = obj.source_bbox.height

        # Product: prepend a boosted scale if the source is smaller than the minimum.
        if obj.role == "product" and image_width > 0 and orig_w > 0:
            min_w = round(image_width * _PRODUCT_MIN_CANVAS_RATIO)
            if orig_w < min_w:
                boost = round(min_w / orig_w, 2)
                scales = [boost] + _SCALES
            else:
                scales = _SCALES
        else:
            scales = _SCALES

        candidates: list[ObjectCandidate] = []
        for anchor in base_anchors:
            for scale in scales:
                w = max(1, round(orig_w * scale))
                h = max(1, round(orig_h * scale))
                x, y = _anchor_position(anchor, safe_zone, w, h)
                candidates.append(ObjectCandidate(
                    object_id=obj.id,
                    role=obj.role,
                    required=obj.required,
                    anchor=anchor,
                    scale=scale,
                    x=x, y=y, width=w, height=h,
                ))
        result[obj.id] = candidates
    return result


def _prefer_side(anchors: list[str], side: str) -> list[str]:
    """Return anchors reordered so that `side`-containing anchors come first."""
    preferred = [a for a in anchors if side in a]
    rest = [a for a in anchors if side not in a]
    return preferred + rest
