"""Greedy deterministic constraint solver.

Processing order (deterministic, no randomness):
  1. required=True first
  2. then by role priority (product < title_group < logo < cta_group < ...)
  3. then by object_id lexicographic

For each object, the first valid candidate in (anchor-order, scale-desc) is chosen.

Constraints checked per candidate:
  1. Canvas bounds
  2. Safe zone (required objects only)
  3. Overlap with already-placed objects (ratio > OVERLAP_THRESHOLD → reject)
  4. Protected subject coverage (ratio > PROTECTED_COVERAGE_THRESHOLD → reject)

Hard Fail: a required object has no valid candidate → return (None, "NO_VALID_CANDIDATES", reason).
"""
from __future__ import annotations

from clean_pipeline.extraction.models import ExtractedObject
from clean_pipeline.layout.models import ObjectCandidate, PlacedObject, SafeZone
from clean_pipeline.layout.safe_zone import (
    contains as sz_contains,
    intersection_area,
    within_canvas,
)

_OVERLAP_THRESHOLD = 0.30
_PROTECTED_COVERAGE_THRESHOLD = 0.30

_ROLE_PRIORITY: dict[str, int] = {
    "product": 0,
    "title_group": 1,
    "logo": 2,
    "cta_group": 3,
    "badge": 4,
    "body_text_group": 5,
    "decorative_ad": 6,
}


def _sort_key(obj: ExtractedObject) -> tuple:
    return (
        0 if obj.required else 1,
        _ROLE_PRIORITY.get(obj.role, 99),
        obj.id,
    )


def _is_valid(
    cand: ObjectCandidate,
    placed: list[PlacedObject],
    safe_zone: SafeZone,
    target_width: int,
    target_height: int,
    protected_bboxes: list[tuple[int, int, int, int]],
) -> bool:
    # 1. Canvas bounds
    if not within_canvas(cand.x, cand.y, cand.width, cand.height, target_width, target_height):
        return False

    # 2. Safe zone (required only)
    if cand.required and not sz_contains(safe_zone, cand.x, cand.y, cand.width, cand.height):
        return False

    # 3. Overlap with already-placed objects
    cand_area = cand.width * cand.height
    for po in placed:
        overlap = intersection_area(
            cand.x, cand.y, cand.width, cand.height,
            po.x, po.y, po.width, po.height,
        )
        if overlap == 0:
            continue
        min_area = min(cand_area, po.width * po.height)
        if min_area > 0 and overlap / min_area > _OVERLAP_THRESHOLD:
            return False

    # 4. Protected subject coverage
    for px, py, pw, ph in protected_bboxes:
        overlap = intersection_area(
            cand.x, cand.y, cand.width, cand.height, px, py, pw, ph
        )
        if overlap == 0:
            continue
        protected_area = pw * ph
        if protected_area > 0 and overlap / protected_area > _PROTECTED_COVERAGE_THRESHOLD:
            return False

    return True


def solve(
    candidates: dict[str, list[ObjectCandidate]],
    extracted: list[ExtractedObject],
    safe_zone: SafeZone,
    target_width: int,
    target_height: int,
    protected_bboxes: list[tuple[int, int, int, int]],
) -> tuple[list[PlacedObject] | None, str, str]:
    """Greedy placement. Returns (placed, "", "") on success or (None, code, reason) on fail."""
    sorted_objs = sorted(extracted, key=_sort_key)
    placed: list[PlacedObject] = []
    seen: set[str] = set()

    for obj in sorted_objs:
        if obj.id in seen:
            return None, "DUPLICATE_OBJECT", f"Object '{obj.id}' appears twice in extraction"

        valid_found = False
        for cand in candidates.get(obj.id, []):
            if _is_valid(cand, placed, safe_zone, target_width, target_height, protected_bboxes):
                placed.append(PlacedObject(
                    object_id=obj.id,
                    role=obj.role,
                    required=obj.required,
                    anchor=cand.anchor,
                    scale=cand.scale,
                    x=cand.x,
                    y=cand.y,
                    width=cand.width,
                    height=cand.height,
                    source_rgba_path=obj.rgba_path,
                ))
                seen.add(obj.id)
                valid_found = True
                break

        if not valid_found and obj.required:
            return (
                None,
                "NO_VALID_CANDIDATES",
                f"Required object '{obj.id}' (role={obj.role}) has no valid placement "
                "— all candidates violate constraints",
            )

    return placed, "", ""
