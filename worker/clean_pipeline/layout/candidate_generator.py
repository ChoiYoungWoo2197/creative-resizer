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

from worker.clean_pipeline.extraction.models import ExtractedObject
from worker.clean_pipeline.layout.models import ObjectCandidate, SafeZone

_ROLE_ANCHORS: dict[str, list[str]] = {
    "title_group":     ["top-left", "top-center", "top-right"],
    "product":         ["left-center", "right-center", "center-left", "center-right"],
    "logo":            ["top-left", "top-right"],
    "cta_group":       ["bottom-left", "bottom-center", "bottom-right"],
    "body_text_group": ["top-left", "top-center", "top-right"],
    "badge":           ["top-left", "top-right"],
    "decorative_ad":   ["left-center", "right-center"],
}

_SCALES: list[float] = [1.0, 0.9, 0.8, 0.7, 0.6]


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
) -> dict[str, list[ObjectCandidate]]:
    """Return {object_id: [candidates]} in deterministic order.

    Dimensions are taken directly from source_bbox (canonical pixel size).
    """
    result: dict[str, list[ObjectCandidate]] = {}
    for obj in extracted:
        anchors = _ROLE_ANCHORS.get(obj.role, ["top-center"])
        orig_w = obj.source_bbox.width
        orig_h = obj.source_bbox.height
        candidates: list[ObjectCandidate] = []
        for anchor in anchors:
            for scale in _SCALES:
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
