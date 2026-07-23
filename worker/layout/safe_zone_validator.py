"""Stage 21 Bundle B: Safe zone, clipping, and overlap validation helpers."""
from __future__ import annotations

from layout.layout_role_resolver import SAFE_ZONE_REQUIRED_ROLES, CANVAS_BLEED_ROLES


def compute_clipping_ratio(target_bbox: dict, target_w: int, target_h: int) -> float:
    """Fraction of targetBBox that lies outside the canvas [0, 0, target_w, target_h].

    0.0 = fully inside canvas.
    1.0 = fully outside canvas.
    """
    x = target_bbox.get("x", 0)
    y = target_bbox.get("y", 0)
    w = target_bbox.get("width", 0)
    h = target_bbox.get("height", 0)
    if w <= 0 or h <= 0:
        return 1.0
    area = w * h
    ix1 = max(0, x)
    iy1 = max(0, y)
    ix2 = min(target_w, x + w)
    iy2 = min(target_h, y + h)
    if ix2 <= ix1 or iy2 <= iy1:
        return 1.0
    visible = (ix2 - ix1) * (iy2 - iy1)
    return 1.0 - visible / area


def compute_overlap_ratio(bbox1: dict, bbox2: dict) -> float:
    """Overlap area / area of the smaller bbox."""
    ix1 = max(bbox1["x"], bbox2["x"])
    iy1 = max(bbox1["y"], bbox2["y"])
    ix2 = min(bbox1["x"] + bbox1["width"], bbox2["x"] + bbox2["width"])
    iy2 = min(bbox1["y"] + bbox1["height"], bbox2["y"] + bbox2["height"])
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    overlap = (ix2 - ix1) * (iy2 - iy1)
    a1 = bbox1["width"] * bbox1["height"]
    a2 = bbox2["width"] * bbox2["height"]
    smaller = min(a1, a2)
    return overlap / max(1, smaller)


def is_inside_safe_zone(target_bbox: dict, safe_x1: int, safe_y1: int, safe_x2: int, safe_y2: int) -> bool:
    """Return True if the entire targetBBox is within the safe rect."""
    x = target_bbox.get("x", 0)
    y = target_bbox.get("y", 0)
    x2 = x + target_bbox.get("width", 0)
    y2 = y + target_bbox.get("height", 0)
    return x >= safe_x1 and y >= safe_y1 and x2 <= safe_x2 and y2 <= safe_y2


def validate_placement(
    placement,  # ObjectPlacement
    all_placements: list,  # list[ObjectPlacement]
    safe_x1: int,
    safe_y1: int,
    safe_x2: int,
    safe_y2: int,
    target_w: int,
    target_h: int,
) -> dict:
    """Run all checks for a single placement.

    Returns {
        safeZonePassed: bool,
        clippingRatio: float,
        overlapObjectIds: list[str],
        hardFail: bool,
        hardFailReasons: list[str],
        softPenalty: float,
    }
    """
    result = {
        "safeZonePassed": True,
        "clippingRatio": 0.0,
        "overlapObjectIds": [],
        "hardFail": False,
        "hardFailReasons": [],
        "softPenalty": 0.0,
    }
    bbox = placement.targetBBox
    role = placement.layoutRole
    required = placement.required
    safe_zone_req = placement.safeZoneRequired

    # --- Clipping check ---
    clip = compute_clipping_ratio(bbox, target_w, target_h)
    result["clippingRatio"] = clip

    if clip >= 1.0:
        result["hardFail"] = True
        result["hardFailReasons"].append(f"fully_out_of_canvas:{role}")
    elif clip > 0.1 and role in SAFE_ZONE_REQUIRED_ROLES:
        result["hardFail"] = True
        result["hardFailReasons"].append(f"clipping>{10}%:{role}")
    elif clip > 0.0:
        result["softPenalty"] += clip * 10

    # --- Safe zone check ---
    if safe_zone_req and role not in CANVAS_BLEED_ROLES:
        inside = is_inside_safe_zone(bbox, safe_x1, safe_y1, safe_x2, safe_y2)
        result["safeZonePassed"] = inside
        if not inside:
            result["softPenalty"] += 20
            # Hard fail only for truly required roles
            if role in {"product", "title", "headline", "body_text", "cta", "logo"}:
                result["hardFail"] = True
                result["hardFailReasons"].append(f"safe_zone_violation:{role}")

    # --- Overlap check ---
    for other in all_placements:
        if other.objectId == placement.objectId:
            continue
        other_role = other.layoutRole
        # Skip decorative-text overlap (intentional background plate)
        if (role in CANVAS_BLEED_ROLES or other_role in CANVAS_BLEED_ROLES):
            continue
        overlap = compute_overlap_ratio(bbox, other.targetBBox)
        if overlap > 0.1:
            result["overlapObjectIds"].append(other.objectId)
            if _is_hard_overlap(role, other_role, overlap):
                result["hardFail"] = True
                result["hardFailReasons"].append(
                    f"critical_overlap:{role}×{other_role}={overlap:.2f}"
                )
            else:
                result["softPenalty"] += overlap * 5

    return result


def _is_hard_overlap(role_a: str, role_b: str, ratio: float) -> bool:
    """True when two non-decorative foreground objects overlap critically."""
    text_roles = {"title", "headline", "body_text", "text", "cta", "logo", "badge"}
    visual_roles = {"product", "main_image", "human_subject"}
    # text-text overlap
    if role_a in text_roles and role_b in text_roles and ratio > 0.2:
        return True
    # product-text critical overlap
    if (role_a in visual_roles and role_b in text_roles) or (
        role_b in visual_roles and role_a in text_roles
    ):
        if ratio > 0.4:
            return True
    return False
