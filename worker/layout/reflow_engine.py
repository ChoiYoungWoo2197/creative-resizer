"""Stage 21 Bundle B: Deterministic foreground reflow engine.

plan_foreground_layout() is the main entry point.
It reads BannerSpec safe zone data, generates 2-3 layout candidates,
validates them, selects the best, and updates each fg_layer["bbox"]
to the planned targetBBox so the compositor can paste at the correct position.

No AI decisions, no random values. Same input → same output always.
"""
from __future__ import annotations

import sys

from layout.models import LayoutPlanResult, ObjectPlacement, CandidateScore
from layout.layout_role_resolver import (
    resolve_layout_role,
    SAFE_ZONE_REQUIRED_ROLES,
    BLEED_ALLOWED_ROLES,
    CANVAS_BLEED_ROLES,
    REQUIRED_ROLES,
    HERO_VISUAL_ROLES,
)
from layout.safe_zone_validator import validate_placement, is_inside_safe_zone
from layout.candidate_scorer import score_candidate

# Wide banner threshold (ratio >= 1.7 → wide candidate logic)
_WIDE_RATIO = 1.7

# Clipping threshold for required roles (> 10% → hard fail)
_CLIP_HARD_FAIL_RATIO = 0.10


def plan_foreground_layout(
    fg_layers: list,
    spec: dict,
    canvas_w: int,
    canvas_h: int,
    target_w: int,
    target_h: int,
    psd_layers: list | None = None,
    apply_logs: list | None = None,
    job_id: str = "",
    spec_id: str = "",
) -> LayoutPlanResult:
    """Plan foreground object positions using BannerSpec safe zone data.

    Steps:
      1. Compute safe zone rect from spec.
      2. Resolve layoutRole for each fg_layer.
      3. Deduplicate by objectId.
      4. Detect visual side (left/right) from source positions.
      5. Generate 2-3 deterministic layout candidates.
      6. Validate + score each candidate.
      7. Select best candidate (lowest score).
      8. Update fg_layer["bbox"] to selected targetBBox.
      9. Return LayoutPlanResult.

    The compositor reads layer["bbox"] unchanged — no coordinate re-computation needed.
    """
    result = LayoutPlanResult()

    # ── Step 1: Safe zone ────────────────────────────────────────────────────
    sys.stdout.flush()
    try:
        from safe_zone import normalize_safe_zone
        safe_zones = normalize_safe_zone(spec or {}, target_w, target_h)
    except Exception as _sz_err:
        print(f"[LAYOUT] safe_zone import failed: {_sz_err}", flush=True)
        safe_zones = {"general": {"top": 0, "right": 0, "bottom": 0, "left": 0},
                      "text": {}, "cta": {}}

    parse_status = (spec or {}).get("safeZoneParseStatus")
    _HARD_STATUSES = ("parsed_text", "parsed_diagram")
    safe_zone_available = parse_status in _HARD_STATUSES

    gen_sz = safe_zones.get("general", {})
    safe_x1 = gen_sz.get("left", 0)
    safe_y1 = gen_sz.get("top", 0)
    safe_x2 = target_w - gen_sz.get("right", 0)
    safe_y2 = target_h - gen_sz.get("bottom", 0)
    safe_w = max(1, safe_x2 - safe_x1)
    safe_h = max(1, safe_y2 - safe_y1)

    if not safe_zone_available:
        msg = (
            f"SAFE_ZONE_UNAVAILABLE: safeZoneParseStatus={parse_status!r}"
            f" — using ratio-based fallback safe zone"
        )
        print(f"[LAYOUT] WARNING: {msg}", flush=True)
        result.warnings.append(msg)

    result.safeZoneAvailable = safe_zone_available
    result.safeZoneEnforced = safe_zone_available
    result.safeZoneRect = {
        "x1": safe_x1, "y1": safe_y1, "x2": safe_x2, "y2": safe_y2
    }

    # ── Step 2: Build lookup dicts ───────────────────────────────────────────
    psd_by_lid: dict[str, dict] = {
        l.get("id", ""): l for l in (psd_layers or []) if l.get("id")
    }
    attempted_roles: dict[str, str] = {}
    for log in (apply_logs or []):
        if not log.get("applied") and log.get("rejectReason") == "human_subject_immutable":
            lid = log.get("layerId", "")
            nr = log.get("newRole", "")
            if lid and nr:
                attempted_roles[lid] = nr

    # ── Step 3: Resolve layout roles and build layout objects ────────────────
    layout_objs: list[dict] = []
    for fg_layer in fg_layers:
        psd_layer = psd_by_lid.get(fg_layer.get("layerId", ""))
        attempted = attempted_roles.get(fg_layer.get("layerId", ""))
        layout_role, reason = resolve_layout_role(fg_layer, psd_layer, attempted)
        required = layout_role in REQUIRED_ROLES or (
            fg_layer.get("role") in REQUIRED_ROLES and layout_role == fg_layer.get("role")
        )
        sz_required = layout_role in SAFE_ZONE_REQUIRED_ROLES
        layout_objs.append({
            "fg_layer":         fg_layer,
            "objectId":         fg_layer.get("objectId", ""),
            "semanticRole":     fg_layer.get("role", "unknown"),
            "layoutRole":       layout_role,
            "layoutRoleReason": reason,
            "required":         required,
            "safeZoneRequired": sz_required,
            "sourceBBox":       fg_layer.get("sourceBBox", {}),
            "originalTargetBBox": dict(fg_layer.get("bbox", {})),
            "layerId":          fg_layer.get("layerId", ""),
        })

    result.inputObjectCount = len(layout_objs)

    # ── Step 4: Deduplicate by objectId ─────────────────────────────────────
    seen_ids: set[str] = set()
    duplicate_ids: list[str] = []
    unique_objs: list[dict] = []
    for obj in layout_objs:
        oid = obj["objectId"]
        if oid in seen_ids:
            duplicate_ids.append(oid)
        else:
            seen_ids.add(oid)
            unique_objs.append(obj)

    result.duplicateCount = len(duplicate_ids)
    result.noDuplicateComposition = (len(duplicate_ids) == 0)
    result.uniqueObjectCount = len(unique_objs)
    result.requiredObjectCount = sum(1 for o in unique_objs if o["required"])

    # Log LAYOUT_INPUT
    roles = sorted({o["layoutRole"] for o in unique_objs})
    print(
        f"[LAYOUT_INPUT]"
        f" jobId={job_id} specId={spec_id}"
        f" targetSize={target_w}x{target_h}"
        f" safeZone=({safe_x1},{safe_y1},{safe_x2},{safe_y2})"
        f" safeZoneAvailable={safe_zone_available}"
        f" objectCount={len(unique_objs)}"
        f" roles={roles}",
        flush=True,
    )

    # Log role overrides
    for obj in unique_objs:
        if obj["layoutRole"] != obj["semanticRole"]:
            print(
                f"[LAYOUT_ROLE_OVERRIDE]"
                f" jobId={job_id} specId={spec_id}"
                f" objectId={obj['objectId']!r}"
                f" semanticRole={obj['semanticRole']!r}"
                f" layoutRole={obj['layoutRole']!r}"
                f" reason={obj['layoutRoleReason']!r}"
                f" attemptedRole={attempted_roles.get(obj['layerId'], '')!r}"
                f" layerType={(psd_by_lid.get(obj['layerId']) or {}).get('type', '')!r}",
                flush=True,
            )

    # ── Step 5: Detect visual side from source positions ────────────────────
    visual_side = _detect_visual_side(unique_objs, canvas_w)

    # ── Step 6: Generate candidates ─────────────────────────────────────────
    target_ratio = target_w / max(target_h, 1)
    safe_rect = (safe_x1, safe_y1, safe_x2, safe_y2)

    if target_ratio >= _WIDE_RATIO:
        candidates = _generate_wide_candidates(
            unique_objs, safe_rect, target_w, target_h, visual_side
        )
    else:
        candidates = _generate_generic_candidates(
            unique_objs, safe_rect, target_w, target_h, visual_side
        )

    # ── Step 7: Score and select ────────────────────────────────────────────
    scored: list[tuple[str, list, CandidateScore]] = []
    for cid, placements in candidates.items():
        sc = score_candidate(
            cid, placements,
            safe_x1, safe_y1, safe_x2, safe_y2,
            target_w, target_h,
            original_visual_side=visual_side,
            canvas_w=canvas_w,
            canvas_h=canvas_h,
        )
        scored.append((cid, placements, sc))
        print(
            f"[LAYOUT_CANDIDATE]"
            f" jobId={job_id} specId={spec_id}"
            f" candidateId={cid}"
            f" hardFailureCount={sc.hardFailureCount}"
            f" safeZoneViolationCount={sc.safeZoneViolationCount}"
            f" clippingViolationCount={sc.clippingViolationCount}"
            f" overlapViolationCount={sc.overlapViolationCount}"
            f" score={sc.total}",
            flush=True,
        )

    # Sort: hard fails → sz violations → clipping → overlap → total → candidateId
    scored.sort(key=lambda x: (
        x[2].hardFailureCount,
        x[2].safeZoneViolationCount,
        x[2].clippingViolationCount,
        x[2].overlapViolationCount,
        x[2].total,
        x[0],
    ))

    best_cid, best_placements, best_score = scored[0]
    result.selectedCandidateId = best_cid
    result.candidateScores = [s[2] for s in scored]
    result.safeZoneViolationCount = best_score.safeZoneViolationCount
    result.clippingViolationCount = best_score.clippingViolationCount
    result.overlapViolationCount = best_score.overlapViolationCount

    hard_fail_reasons: list[str] = []
    for p in best_placements:
        check = validate_placement(
            p, best_placements,
            safe_x1, safe_y1, safe_x2, safe_y2, target_w, target_h
        )
        if check["hardFail"]:
            hard_fail_reasons.extend(check["hardFailReasons"])

    result.hardFailReasons = sorted(set(hard_fail_reasons))

    print(
        f"[LAYOUT_SELECTED]"
        f" jobId={job_id} specId={spec_id}"
        f" candidateId={best_cid}"
        f" score={best_score.total}"
        f" hardFailReasons={result.hardFailReasons}"
        f" warnings={result.warnings}",
        flush=True,
    )

    # ── Step 8: Apply placements to fg_layers ────────────────────────────────
    placement_map = {p.objectId: p for p in best_placements}
    placed_count = 0
    skipped_count = 0
    required_placed = 0

    for obj in unique_objs:
        oid = obj["objectId"]
        fg_layer = obj["fg_layer"]
        placement = placement_map.get(oid)

        if placement is None:
            skipped_count += 1
            print(
                f"[REFLOW_OBJECT] SKIPPED jobId={job_id} specId={spec_id}"
                f" objectId={oid!r} semanticRole={obj['semanticRole']!r}"
                f" layoutRole={obj['layoutRole']!r}",
                flush=True,
            )
            continue

        # Update fg_layer["bbox"] so the compositor uses planned coords
        fg_layer["bbox"] = dict(placement.targetBBox)

        placed_count += 1
        if obj["required"]:
            required_placed += 1

        sz_passed = is_inside_safe_zone(
            placement.targetBBox, safe_x1, safe_y1, safe_x2, safe_y2
        )

        print(
            f"[REFLOW_OBJECT]"
            f" jobId={job_id} specId={spec_id}"
            f" objectId={oid!r}"
            f" semanticRole={obj['semanticRole']!r}"
            f" layoutRole={obj['layoutRole']!r}"
            f" sourceBBox={_fmt_bbox(obj['sourceBBox'])}"
            f" originalTargetBBox={_fmt_bbox(obj['originalTargetBBox'])}"
            f" targetBBox={_fmt_bbox(placement.targetBBox)}"
            f" scale={placement.scale:.4f}"
            f" anchor={placement.anchor!r}"
            f" safeZonePassed={sz_passed}",
            flush=True,
        )
        print(
            f"[SAFE_ZONE_CHECK]"
            f" jobId={job_id} specId={spec_id}"
            f" objectId={oid!r}"
            f" layoutRole={obj['layoutRole']!r}"
            f" required={obj['required']}"
            f" safeZoneRequired={obj['safeZoneRequired']}"
            f" passed={sz_passed}"
            f" targetBBox={_fmt_bbox(placement.targetBBox)}"
            f" safeZoneRect=({safe_x1},{safe_y1},{safe_x2},{safe_y2})",
            flush=True,
        )

    result.placedObjectCount = placed_count
    result.skippedObjectCount = skipped_count
    result.allRequiredObjectsPlaced = (
        required_placed == result.requiredObjectCount
    )
    result.allUniqueObjectsPlaced = (skipped_count == 0)
    result.allObjectsCompositedOnce = (
        result.noDuplicateComposition and result.allUniqueObjectsPlaced
    )
    result.objectPlacements = best_placements

    if not result.allRequiredObjectsPlaced:
        result.hardFailReasons.append("required_object_not_placed")
        result.success = False
    elif best_score.hardFailureCount > 0:
        result.success = False
    else:
        result.success = True

    print(
        f"[LAYOUT_SUMMARY]"
        f" jobId={job_id} specId={spec_id}"
        f" inputObjectCount={result.inputObjectCount}"
        f" uniqueObjectCount={result.uniqueObjectCount}"
        f" requiredObjectCount={result.requiredObjectCount}"
        f" placedObjectCount={result.placedObjectCount}"
        f" skippedObjectCount={result.skippedObjectCount}"
        f" duplicateCount={result.duplicateCount}"
        f" allRequiredObjectsPlaced={result.allRequiredObjectsPlaced}"
        f" allUniqueObjectsPlaced={result.allUniqueObjectsPlaced}"
        f" noDuplicateComposition={result.noDuplicateComposition}"
        f" allObjectsCompositedOnce={result.allObjectsCompositedOnce}"
        f" safeZoneViolationCount={result.safeZoneViolationCount}"
        f" clippingViolationCount={result.clippingViolationCount}"
        f" overlapViolationCount={result.overlapViolationCount}"
        f" success={result.success}",
        flush=True,
    )

    return result


# ── Candidate generation ──────────────────────────────────────────────────────

def _generate_wide_candidates(
    unique_objs: list,
    safe_rect: tuple,
    target_w: int,
    target_h: int,
    visual_side: str,
) -> dict[str, list]:
    """Generate 3 candidates for wide banners (ratio >= 1.7)."""
    opposite = "right" if visual_side == "left" else "left"
    return {
        "candidate_A": _place_wide(unique_objs, safe_rect, target_w, target_h, visual_side),
        "candidate_B": _place_wide(unique_objs, safe_rect, target_w, target_h, opposite),
        "candidate_C": _place_proportional(unique_objs, safe_rect, target_w, target_h),
    }


def _generate_generic_candidates(
    unique_objs: list,
    safe_rect: tuple,
    target_w: int,
    target_h: int,
    visual_side: str,
) -> dict[str, list]:
    """Generate 3 candidates for non-wide banners (square / vertical)."""
    opposite = "right" if visual_side == "left" else "left"
    return {
        "candidate_A": _place_generic(unique_objs, safe_rect, target_w, target_h, "hero-top"),
        "candidate_B": _place_wide(unique_objs, safe_rect, target_w, target_h, visual_side),
        "candidate_C": _place_proportional(unique_objs, safe_rect, target_w, target_h),
    }


def _place_wide(
    unique_objs: list,
    safe_rect: tuple,
    target_w: int,
    target_h: int,
    img_side: str,
) -> list:
    """Place objects in a wide banner with visual hero on img_side.

    Returns list[ObjectPlacement].
    """
    safe_x1, safe_y1, safe_x2, safe_y2 = safe_rect
    safe_w = safe_x2 - safe_x1
    safe_h = safe_y2 - safe_y1
    mid = safe_x1 + safe_w // 2

    gap = max(5, safe_w // 50)

    if img_side == "left":
        visual_x, visual_y = safe_x1, safe_y1
        visual_w, visual_h = mid - safe_x1 - gap, safe_h
        text_x, text_y = mid + gap, safe_y1
        text_w, text_h = safe_x2 - mid - gap, safe_h
    else:
        text_x, text_y = safe_x1, safe_y1
        text_w, text_h = mid - safe_x1 - gap, safe_h
        visual_x, visual_y = mid + gap, safe_y1
        visual_w, visual_h = safe_x2 - mid - gap, safe_h

    # Vertical sub-slots within text area
    title_slot   = (text_x, text_y,                    text_w, max(1, int(text_h * 0.30)))
    body_slot    = (text_x, text_y + int(text_h * 0.33), text_w, max(1, int(text_h * 0.28)))
    cta_slot     = (text_x, text_y + int(text_h * 0.64), text_w, max(1, int(text_h * 0.20)))
    logo_slot    = (text_x, text_y + int(text_h * 0.87), max(1, int(text_w * 0.45)), max(1, int(text_h * 0.13)))
    badge_slot   = (text_x + int(text_w * 0.52), text_y + int(text_h * 0.87),
                    max(1, int(text_w * 0.45)), max(1, int(text_h * 0.13)))

    slot_map = {
        "title":     (title_slot,  "top-center"),
        "headline":  (title_slot,  "top-center"),
        "body_text": (body_slot,   "top-center"),
        "text":      (body_slot,   "top-center"),
        "cta":       (cta_slot,    "top-center"),
        "logo":      (logo_slot,   "top-left"),
        "badge":     (badge_slot,  "top-left"),
    }

    placements: list[ObjectPlacement] = []

    for obj in unique_objs:
        lr = obj["layoutRole"]
        sr = obj["semanticRole"]
        src_bbox = obj["sourceBBox"]
        src_w = src_bbox.get("width", 1) or 1
        src_h = src_bbox.get("height", 1) or 1

        if lr in CANVAS_BLEED_ROLES:
            # Decorative/background: expand to full canvas width, preserve vertical pos
            orig = obj["originalTargetBBox"]
            oy = orig.get("y", 0)
            oh = max(1, orig.get("height", int(target_h * 0.15)))
            bbox = {"x": 0, "y": oy, "width": target_w, "height": oh}
            scale = target_w / max(src_w, 1)
            placements.append(ObjectPlacement(
                objectId=obj["objectId"],
                semanticRole=sr,
                layoutRole=lr,
                sourceBBox=src_bbox,
                originalTargetBBox=obj["originalTargetBBox"],
                targetBBox=bbox,
                scale=scale,
                anchor="top-left",
                required=obj["required"],
                safeZoneRequired=False,
                safeZonePassed=True,
                reason=f"decorative_canvas_bleed",
            ))
            continue

        if lr in HERO_VISUAL_ROLES or sr in HERO_VISUAL_ROLES:
            # Hero: fit in visual slot, centered
            bbox, scale = _fit_in_slot(src_w, src_h, visual_x, visual_y, visual_w, visual_h, "center")
            placements.append(ObjectPlacement(
                objectId=obj["objectId"],
                semanticRole=sr,
                layoutRole=lr,
                sourceBBox=src_bbox,
                originalTargetBBox=obj["originalTargetBBox"],
                targetBBox=bbox,
                scale=scale,
                anchor="center",
                required=obj["required"],
                safeZoneRequired=lr in HERO_VISUAL_ROLES,
                safeZonePassed=True,  # hero may bleed
                reason=f"hero_visual_slot_{img_side}",
            ))
            continue

        if lr in slot_map:
            slot, anchor = slot_map[lr]
            sx, sy, sw, sh = slot
            bbox, scale = _fit_in_slot(src_w, src_h, sx, sy, sw, sh, anchor)
            placements.append(ObjectPlacement(
                objectId=obj["objectId"],
                semanticRole=sr,
                layoutRole=lr,
                sourceBBox=src_bbox,
                originalTargetBBox=obj["originalTargetBBox"],
                targetBBox=bbox,
                scale=scale,
                anchor=anchor,
                required=obj["required"],
                safeZoneRequired=obj["safeZoneRequired"],
                safeZonePassed=True,
                reason=f"text_slot_{lr}_{img_side}",
            ))
            continue

        # Unknown role: use proportional position
        placements.append(_proportional_placement(obj, safe_rect, target_w, target_h))

    return placements


def _place_generic(
    unique_objs: list,
    safe_rect: tuple,
    target_w: int,
    target_h: int,
    mode: str = "hero-top",
) -> list:
    """Generic layout for non-wide banners (square / vertical)."""
    safe_x1, safe_y1, safe_x2, safe_y2 = safe_rect
    safe_w = safe_x2 - safe_x1
    safe_h = safe_y2 - safe_y1

    hero_slot   = (safe_x1, safe_y1,                    safe_w, max(1, int(safe_h * 0.52)))
    title_slot  = (safe_x1, safe_y1 + int(safe_h * 0.55), safe_w, max(1, int(safe_h * 0.18)))
    body_slot   = (safe_x1, safe_y1 + int(safe_h * 0.76), safe_w, max(1, int(safe_h * 0.14)))
    cta_slot    = (safe_x1, safe_y1 + int(safe_h * 0.88), safe_w, max(1, int(safe_h * 0.10)))
    logo_slot   = (safe_x1, safe_y1,                     max(1, int(safe_w * 0.25)), max(1, int(safe_h * 0.10)))

    slot_map = {
        "title":     (title_slot,  "top-center"),
        "headline":  (title_slot,  "top-center"),
        "body_text": (body_slot,   "top-center"),
        "text":      (body_slot,   "top-center"),
        "cta":       (cta_slot,    "top-center"),
        "logo":      (logo_slot,   "top-left"),
    }

    placements: list[ObjectPlacement] = []
    for obj in unique_objs:
        lr = obj["layoutRole"]
        sr = obj["semanticRole"]
        src_bbox = obj["sourceBBox"]
        src_w = src_bbox.get("width", 1) or 1
        src_h = src_bbox.get("height", 1) or 1

        if lr in CANVAS_BLEED_ROLES:
            orig = obj["originalTargetBBox"]
            bbox = {"x": 0, "y": orig.get("y", 0), "width": target_w,
                    "height": max(1, orig.get("height", 30))}
            placements.append(ObjectPlacement(
                objectId=obj["objectId"], semanticRole=sr, layoutRole=lr,
                sourceBBox=src_bbox, originalTargetBBox=obj["originalTargetBBox"],
                targetBBox=bbox, scale=1.0, anchor="top-left",
                required=obj["required"], safeZoneRequired=False,
                safeZonePassed=True, reason="decorative_canvas_bleed",
            ))
            continue

        if lr in HERO_VISUAL_ROLES or sr in HERO_VISUAL_ROLES:
            sx, sy, sw, sh = hero_slot
            bbox, scale = _fit_in_slot(src_w, src_h, sx, sy, sw, sh, "center")
            placements.append(ObjectPlacement(
                objectId=obj["objectId"], semanticRole=sr, layoutRole=lr,
                sourceBBox=src_bbox, originalTargetBBox=obj["originalTargetBBox"],
                targetBBox=bbox, scale=scale, anchor="center",
                required=obj["required"], safeZoneRequired=False,
                safeZonePassed=True, reason="hero_top",
            ))
            continue

        if lr in slot_map:
            slot, anchor = slot_map[lr]
            sx, sy, sw, sh = slot
            bbox, scale = _fit_in_slot(src_w, src_h, sx, sy, sw, sh, anchor)
            placements.append(ObjectPlacement(
                objectId=obj["objectId"], semanticRole=sr, layoutRole=lr,
                sourceBBox=src_bbox, originalTargetBBox=obj["originalTargetBBox"],
                targetBBox=bbox, scale=scale, anchor=anchor,
                required=obj["required"], safeZoneRequired=obj["safeZoneRequired"],
                safeZonePassed=True, reason=f"text_slot_{lr}",
            ))
            continue

        placements.append(_proportional_placement(obj, safe_rect, target_w, target_h))

    return placements


def _place_proportional(
    unique_objs: list,
    safe_rect: tuple,
    target_w: int,
    target_h: int,
) -> list:
    """Candidate C: keep originalTargetBBox, clamp required objects into safe rect."""
    safe_x1, safe_y1, safe_x2, safe_y2 = safe_rect
    placements: list[ObjectPlacement] = []

    for obj in unique_objs:
        orig = obj["originalTargetBBox"]
        lr = obj["layoutRole"]
        sr = obj["semanticRole"]
        src_bbox = obj["sourceBBox"]
        src_w = src_bbox.get("width", 1) or 1
        src_h = src_bbox.get("height", 1) or 1

        x = orig.get("x", 0)
        y = orig.get("y", 0)
        w = orig.get("width", 1) or 1
        h = orig.get("height", 1) or 1
        scale = w / src_w if src_w > 0 else 1.0

        # Clamp required + safe-zone objects into safe rect
        if obj["safeZoneRequired"] and lr in SAFE_ZONE_REQUIRED_ROLES:
            x = max(safe_x1, min(safe_x2 - w, x))
            y = max(safe_y1, min(safe_y2 - h, y))

        bbox = {"x": x, "y": y, "width": w, "height": h}
        placements.append(ObjectPlacement(
            objectId=obj["objectId"],
            semanticRole=sr,
            layoutRole=lr,
            sourceBBox=src_bbox,
            originalTargetBBox=orig,
            targetBBox=bbox,
            scale=scale,
            anchor="top-left",
            required=obj["required"],
            safeZoneRequired=obj["safeZoneRequired"],
            safeZonePassed=True,
            reason="proportional_clamped",
        ))

    return placements


# ── Helpers ───────────────────────────────────────────────────────────────────

def _detect_visual_side(unique_objs: list, canvas_w: int) -> str:
    """Determine which side the main visual occupies in the source canvas."""
    hero_roles = HERO_VISUAL_ROLES
    heroes = [o for o in unique_objs if o["layoutRole"] in hero_roles or o["semanticRole"] in hero_roles]
    if not heroes or canvas_w <= 0:
        return "left"
    # Use the hero with largest source area
    hero = max(heroes, key=lambda o: (
        o["sourceBBox"].get("width", 0) * o["sourceBBox"].get("height", 0)
    ))
    cx = hero["sourceBBox"].get("x", 0) + hero["sourceBBox"].get("width", 0) / 2
    return "left" if cx <= canvas_w / 2 else "right"


def _fit_in_slot(
    src_w: int,
    src_h: int,
    slot_x: int,
    slot_y: int,
    slot_w: int,
    slot_h: int,
    anchor: str = "center",
) -> tuple[dict, float]:
    """Fit object with uniform scale into slot.

    Returns (targetBBox dict, scale float).
    """
    if src_w <= 0 or src_h <= 0 or slot_w <= 0 or slot_h <= 0:
        return {"x": slot_x, "y": slot_y, "width": max(1, slot_w), "height": max(1, slot_h)}, 1.0

    scale = min(slot_w / src_w, slot_h / src_h)
    new_w = max(1, round(src_w * scale))
    new_h = max(1, round(src_h * scale))

    if anchor == "center":
        x = slot_x + (slot_w - new_w) // 2
        y = slot_y + (slot_h - new_h) // 2
    elif anchor == "top-center":
        x = slot_x + (slot_w - new_w) // 2
        y = slot_y
    elif anchor == "bottom-center":
        x = slot_x + (slot_w - new_w) // 2
        y = slot_y + slot_h - new_h
    elif anchor == "top-left":
        x, y = slot_x, slot_y
    elif anchor == "top-right":
        x = slot_x + slot_w - new_w
        y = slot_y
    else:
        x, y = slot_x, slot_y

    return {"x": x, "y": y, "width": new_w, "height": new_h}, round(scale, 4)


def _proportional_placement(
    obj: dict,
    safe_rect: tuple,
    target_w: int,
    target_h: int,
) -> ObjectPlacement:
    """Fallback: use originalTargetBBox without modification."""
    orig = obj["originalTargetBBox"]
    src_bbox = obj["sourceBBox"]
    src_w = src_bbox.get("width", 1) or 1
    w = orig.get("width", 1) or 1
    scale = w / src_w if src_w > 0 else 1.0
    return ObjectPlacement(
        objectId=obj["objectId"],
        semanticRole=obj["semanticRole"],
        layoutRole=obj["layoutRole"],
        sourceBBox=src_bbox,
        originalTargetBBox=orig,
        targetBBox=dict(orig),
        scale=scale,
        anchor="top-left",
        required=obj["required"],
        safeZoneRequired=obj["safeZoneRequired"],
        safeZonePassed=True,
        reason="proportional_fallback",
    )


def _fmt_bbox(b: dict) -> str:
    return f"({b.get('x',0)},{b.get('y',0)},{b.get('width',0)},{b.get('height',0)})"
