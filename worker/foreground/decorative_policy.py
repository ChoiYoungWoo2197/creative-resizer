"""Stage 21 Bundle D-3: Decorative layer grouping and composition ownership.

Policy:
  A. Independent decorative   → excluded (compositionOwner=excluded)
  B. Title background deco    → title group (compositionOwner=group_child)
  C. CTA background deco      → cta group  (compositionOwner=group_child)
  D. Logo decorative          → logo group (compositionOwner=group_child)
  E. Scene-like decorative    → scene_plate (compositionOwner=scene_plate)

Grouping is determined by geometry, not by file-name heuristics.
No PSD-specific names, no Korean text hardcoding.
"""
from __future__ import annotations

# ── compositionOwner constants ─────────────────────────────────────────────────
OWNER_FOREGROUND_REFLOW = "foreground_reflow"
OWNER_SCENE_PLATE = "scene_plate"
OWNER_GROUP_CHILD = "group_child"
OWNER_EXCLUDED = "excluded"

# Roles that are always required/protected — never excluded
_REQUIRED_ROLES = frozenset({"product", "main_image", "human_subject", "title",
                               "body_text", "cta", "logo", "headline"})

# Scene-like roles: go to scene_plate, not foreground
_SCENE_ROLES = frozenset({"human_subject", "person", "main_image",
                           "photographic_scene", "background"})

# Large area threshold: decorative occupying > N% of canvas → scene-like
_LARGE_AREA_RATIO = 0.35

# IoU / containment threshold for grouping
_CONTAIN_RATIO_THRESHOLD = 0.6  # bbox_A contains bbox_B by at least 60%
_OVERLAP_RATIO_THRESHOLD = 0.4  # bbox overlap / smaller area >= 40%


def _area(bbox: dict) -> float:
    return max(0.0, bbox.get("width", 0) * bbox.get("height", 0))


def _canvas_ratio(bbox: dict, canvas_w: int, canvas_h: int) -> float:
    if canvas_w <= 0 or canvas_h <= 0:
        return 0.0
    return _area(bbox) / (canvas_w * canvas_h)


def _containment_ratio(outer: dict, inner: dict) -> float:
    """Fraction of inner bbox that lies within outer bbox. [0, 1]"""
    ox1 = outer.get("x", 0)
    oy1 = outer.get("y", 0)
    ox2 = ox1 + outer.get("width", 0)
    oy2 = oy1 + outer.get("height", 0)
    ix1 = inner.get("x", 0)
    iy1 = inner.get("y", 0)
    ix2 = ix1 + inner.get("width", 0)
    iy2 = iy1 + inner.get("height", 0)

    inter_w = max(0, min(ox2, ix2) - max(ox1, ix1))
    inter_h = max(0, min(oy2, iy2) - max(oy1, iy1))
    inter_area = inter_w * inter_h
    inner_area = _area(inner)
    if inner_area <= 0:
        return 0.0
    return inter_area / inner_area


def _overlap_ratio(a: dict, b: dict) -> float:
    """Overlap area / area of smaller bbox. [0, 1]"""
    ax1 = a.get("x", 0)
    ay1 = a.get("y", 0)
    ax2 = ax1 + a.get("width", 0)
    ay2 = ay1 + a.get("height", 0)
    bx1 = b.get("x", 0)
    by1 = b.get("y", 0)
    bx2 = bx1 + b.get("width", 0)
    by2 = by1 + b.get("height", 0)

    inter_w = max(0, min(ax2, bx2) - max(ax1, bx1))
    inter_h = max(0, min(ay2, by2) - max(ay1, by1))
    inter_area = inter_w * inter_h
    smaller = min(_area(a), _area(b))
    if smaller <= 0:
        return 0.0
    return inter_area / smaller


def _z_order_adjacent(deco_layer: dict, anchor_layer: dict, max_depth_gap: int = 3) -> bool:
    """True when deco depth is within max_depth_gap of anchor."""
    d_depth = deco_layer.get("depth", 0)
    a_depth = anchor_layer.get("depth", 0)
    return abs(d_depth - a_depth) <= max_depth_gap


def _is_text_evidence(layer: dict) -> bool:
    """True when layer has textual content evidence."""
    if layer.get("type") in ("text", "typelayer"):
        return True
    if (layer.get("textContent") or "").strip():
        return True
    if (layer.get("text_content") or "").strip():
        return True
    return False


def _classify_decorative(
    deco: dict,
    anchor_layers: list[dict],
    canvas_w: int,
    canvas_h: int,
) -> tuple[str, str, str | None]:
    """Classify a decorative layer into ownership + group_role + exclusion_reason.

    Returns:
        (compositionOwner, group_role, exclusion_reason)
        group_role is the anchor role when owner==group_child, else ""
    """
    deco_bbox = deco.get("bbox", {})
    deco_area_ratio = _canvas_ratio(deco_bbox, canvas_w, canvas_h)

    # Priority 1: explicit group from PSD structure
    explicit_group_id = deco.get("groupId") or deco.get("parentId") or ""

    # Priority 2: find best anchor match by geometry
    best_anchor_role = ""
    best_score = 0.0

    for anchor in anchor_layers:
        a_role = anchor.get("role", "")
        if a_role == "decorative" or a_role not in _REQUIRED_ROLES:
            continue
        a_bbox = anchor.get("bbox", {})

        # containment: decorative contains anchor (background-box pattern)
        cont = _containment_ratio(deco_bbox, a_bbox)
        # overlap: decorative overlaps anchor significantly
        ovlp = _overlap_ratio(deco_bbox, a_bbox)

        score = 0.0
        if cont >= _CONTAIN_RATIO_THRESHOLD:
            score += cont
        elif ovlp >= _OVERLAP_RATIO_THRESHOLD:
            score += ovlp * 0.8

        if score > 0 and _z_order_adjacent(deco, anchor, max_depth_gap=5):
            score += 0.1

        if score > best_score:
            best_score = score
            best_anchor_role = a_role

    # Scene-like: very large decorative with no anchor match
    if deco_area_ratio >= _LARGE_AREA_RATIO and not explicit_group_id and best_score < 0.3:
        return OWNER_SCENE_PLATE, "", "DECORATIVE_LARGE_AREA_EXCLUDED"

    # Explicit PSD group → group_child of closest anchor role
    if explicit_group_id and best_anchor_role:
        return OWNER_GROUP_CHILD, best_anchor_role, None

    # Geometry-based grouping
    if best_score >= _CONTAIN_RATIO_THRESHOLD or best_score >= _OVERLAP_RATIO_THRESHOLD * 0.8:
        return OWNER_GROUP_CHILD, best_anchor_role, None

    # No clear group → exclude
    reason = (
        "DECORATIVE_LARGE_AREA_EXCLUDED" if deco_area_ratio >= 0.15
        else "DECORATIVE_UNGROUPED_EXCLUDED"
    )
    return OWNER_EXCLUDED, "", reason


def apply_decorative_policy(
    fg_layers: list[dict],
    canvas_w: int,
    canvas_h: int,
    job_id: str = "",
) -> tuple[list[dict], dict]:
    """Apply decorative ownership policy to all foreground layers.

    Args:
        fg_layers: list of layer dicts from extract_foreground_layers()
        canvas_w/h: source canvas dimensions

    Returns:
        (eligible_layers, policy_report)
        eligible_layers: only compositionOwner==foreground_reflow layers
        policy_report: counts + excluded/grouped object IDs
    """
    if not fg_layers:
        return [], {
            "detectedCount": 0, "groupedCount": 0,
            "excludedCount": 0, "compositionCount": 0,
            "excludedObjectIds": [], "groupedObjectIds": [],
        }

    # Partition: required anchors vs decorative
    anchor_layers = [l for l in fg_layers if l.get("role") in _REQUIRED_ROLES]
    decorative_layers = [l for l in fg_layers if l.get("role") == "decorative"]
    other_layers = [l for l in fg_layers
                    if l.get("role") not in _REQUIRED_ROLES
                    and l.get("role") != "decorative"]

    grouped_ids: list[str] = []
    excluded_ids: list[str] = []
    enriched: list[dict] = []

    # All required/anchor layers go straight to foreground_reflow
    for layer in anchor_layers + other_layers:
        layer = dict(layer)
        layer.setdefault("compositionOwner", OWNER_FOREGROUND_REFLOW)
        layer.setdefault("compositionEligible", True)
        layer.setdefault("exclusionReason", None)
        enriched.append(layer)

    for deco in decorative_layers:
        deco = dict(deco)
        obj_id = deco.get("objectId") or deco.get("layerId") or deco.get("name", "?")
        owner, group_role, excl_reason = _classify_decorative(
            deco, anchor_layers, canvas_w, canvas_h
        )
        deco["compositionOwner"] = owner
        deco["compositionEligible"] = (owner == OWNER_FOREGROUND_REFLOW)
        deco["exclusionReason"] = excl_reason
        if group_role:
            deco["groupRole"] = group_role
            deco["renderAsGroup"] = True

        if owner == OWNER_GROUP_CHILD:
            grouped_ids.append(obj_id)
            deco["compositionEligible"] = False  # parent handles rendering
            print(
                f"[DECORATIVE_POLICY] jobId={job_id}"
                f" objectId={obj_id!r} owner=group_child groupRole={group_role!r}",
                flush=True,
            )
        elif owner in (OWNER_EXCLUDED, OWNER_SCENE_PLATE):
            excluded_ids.append(obj_id)
            print(
                f"[DECORATIVE_POLICY] jobId={job_id}"
                f" objectId={obj_id!r} owner={owner!r} reason={excl_reason!r}",
                flush=True,
            )
        else:
            print(
                f"[DECORATIVE_POLICY] jobId={job_id}"
                f" objectId={obj_id!r} owner=foreground_reflow",
                flush=True,
            )
        enriched.append(deco)

    # Only foreground_reflow-eligible layers go to compositor
    eligible = [l for l in enriched if l.get("compositionEligible", True)]
    composition_count = len(eligible)
    detected_count = len(decorative_layers)

    print(
        f"[DECORATIVE_POLICY] jobId={job_id}"
        f" detectedCount={detected_count}"
        f" groupedCount={len(grouped_ids)}"
        f" excludedCount={len(excluded_ids)}"
        f" compositionCount={composition_count}"
        f" excludedObjectIds={excluded_ids}"
        f" groupedObjectIds={grouped_ids}",
        flush=True,
    )

    return eligible, {
        "detectedCount": detected_count,
        "groupedCount": len(grouped_ids),
        "excludedCount": len(excluded_ids),
        "compositionCount": composition_count,
        "excludedObjectIds": excluded_ids,
        "groupedObjectIds": grouped_ids,
    }
