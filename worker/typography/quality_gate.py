"""Stage 20F: Typography Pipeline Quality Gate.

Checks:
  - Required roles present (background, main_image, title)
  - No duplicate text (dedupSkip count within accepted bounds)
  - Safe zone compliance for title/cta layers
  - Korean text preserved (is_korean layers exist if PSD had Korean)
  - Layout score >= threshold (65 to match existing layer-reflow standard)
"""
from __future__ import annotations
from .schemas import LayoutSlot, TypographyResult


REQUIRED_ROLES = {"title", "main_image"}
LAYOUT_SCORE_THRESHOLD = 65.0

# Points by quality aspect
_SCORES = {
    "required_roles":   35.0,
    "safe_zone":        20.0,
    "no_dup_text":      15.0,
    "korean_preserved": 10.0,
    "has_background":   10.0,
    "has_cta":          10.0,
}


def _safe_zone_pass(
    slots: list[LayoutSlot],
    target_w: int,
    target_h: int,
    safe_margin_ratio: float = 0.05,
) -> tuple[bool, list[str]]:
    """Check that title/cta slots are within safe zone.

    Safe zone = canvas inset by safe_margin_ratio on each side.
    """
    sx = int(target_w * safe_margin_ratio)
    sy = int(target_h * safe_margin_ratio)
    sw = target_w - sx * 2
    sh = target_h - sy * 2
    violations: list[str] = []
    for slot in slots:
        if slot.role not in ("title", "cta", "body_text"):
            continue
        ox = max(0, min(slot.x + slot.w, sx + sw) - max(slot.x, sx))
        oy = max(0, min(slot.y + slot.h, sy + sh) - max(slot.y, sy))
        coverage = (ox * oy) / max(slot.w * slot.h, 1)
        if coverage < 0.50:
            violations.append(f"{slot.role}:safe_zone_coverage={coverage:.2f}")
    return len(violations) == 0, violations


def evaluate(
    classified: list[dict],
    slots: list[LayoutSlot],
    target_w: int,
    target_h: int,
    had_korean: bool = False,
    dedup_count: int = 0,
    cta_group_detected: bool = False,
) -> TypographyResult:
    """Score the typography pipeline result.

    Returns a TypographyResult with quality_score and warnings.
    """
    active_roles = {l.get("role") for l in classified if not l.get("dedupSkip")}
    slot_roles = {s.role for s in slots}
    detected_roles = sorted(active_roles)
    placed_roles = active_roles & slot_roles
    missing_roles = sorted(REQUIRED_ROLES - placed_roles)

    score = 0.0
    warnings: list[str] = []

    # Required roles
    if not missing_roles:
        score += _SCORES["required_roles"]
    else:
        warnings.append(f"missing_required_roles:{missing_roles}")

    # Safe zone
    szp, sz_violations = _safe_zone_pass(slots, target_w, target_h)
    if szp:
        score += _SCORES["safe_zone"]
    else:
        warnings.extend(sz_violations)

    # No duplicate text
    if dedup_count == 0:
        score += _SCORES["no_dup_text"]
    else:
        warnings.append(f"dedup_removed:{dedup_count}")
        # Partial credit if dedups were removed (pipeline handled it)
        score += _SCORES["no_dup_text"] * 0.7

    # Korean preserved
    korean_in_active = sum(1 for l in classified if l.get("isKorean") and not l.get("dedupSkip"))
    if had_korean and korean_in_active > 0:
        score += _SCORES["korean_preserved"]
    elif not had_korean:
        score += _SCORES["korean_preserved"]  # not applicable → full points
    else:
        warnings.append("korean_text_not_preserved")

    # Background present
    if "background" in active_roles or "overlay" in active_roles:
        score += _SCORES["has_background"]
    else:
        warnings.append("no_background_layer")

    # CTA present
    if "cta" in active_roles or cta_group_detected:
        score += _SCORES["has_cta"]

    layout_score = round(score, 2)
    success = layout_score >= LAYOUT_SCORE_THRESHOLD and not missing_roles

    return TypographyResult(
        success=success,
        error="" if success else f"layout_score={layout_score} < {LAYOUT_SCORE_THRESHOLD} or missing={missing_roles}",
        detected_roles=detected_roles,
        missing_roles=missing_roles,
        safe_zone_pass=szp,
        safe_zone_violations=sz_violations,
        quality_score=layout_score,
        layout_score=layout_score,
        warnings=warnings,
        cta_group_detected=cta_group_detected,
        used_layer_roles=sorted(placed_roles),
        metrics={
            "requiredRolesOk": not missing_roles,
            "safeZonePass": szp,
            "dedupRemoved": dedup_count,
            "koreanPreserved": had_korean and korean_in_active > 0,
            "hasBackground": "background" in active_roles,
            "hasCta": "cta" in active_roles or cta_group_detected,
        },
    )
