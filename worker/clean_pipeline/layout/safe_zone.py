"""Safe zone computation and geometry helpers.

Safe zone boundaries:
  left   = safe_left
  top    = safe_top
  right  = target_width  - safe_right   (exclusive)
  bottom = target_height - safe_bottom  (exclusive)

Required objects must be fully inside [left, right) × [top, bottom).
"""
from __future__ import annotations

from worker.clean_pipeline.layout.models import SafeZone


def compute(
    target_width: int,
    target_height: int,
    safe_left: int,
    safe_top: int,
    safe_right: int,
    safe_bottom: int,
) -> SafeZone:
    return SafeZone(
        left=safe_left,
        top=safe_top,
        right=target_width - safe_right,
        bottom=target_height - safe_bottom,
    )


def contains(sz: SafeZone, x: int, y: int, w: int, h: int) -> bool:
    """True if the rectangle (x, y, w, h) is fully within the safe zone."""
    return (
        x >= sz.left
        and y >= sz.top
        and x + w <= sz.right
        and y + h <= sz.bottom
    )


def within_canvas(x: int, y: int, w: int, h: int, cw: int, ch: int) -> bool:
    """True if the rectangle fits within a canvas of size (cw, ch)."""
    return x >= 0 and y >= 0 and x + w <= cw and y + h <= ch


def intersection_area(
    ax: int, ay: int, aw: int, ah: int,
    bx: int, by: int, bw: int, bh: int,
) -> int:
    """Area of axis-aligned bounding box intersection (0 if no overlap)."""
    ix1 = max(ax, bx)
    iy1 = max(ay, by)
    ix2 = min(ax + aw, bx + bw)
    iy2 = min(ay + ah, by + bh)
    if ix2 <= ix1 or iy2 <= iy1:
        return 0
    return (ix2 - ix1) * (iy2 - iy1)
