"""Stage 19 Mask Builder.

Builds protection, removal, and outpaint masks from object analysis.

Mask convention: PIL L-mode, 255=active region, 0=background.

generationBlockedMask = product | logo | text | cta | protected_person
generationAllowedMask = removalMask | outpaintMask
"""
from __future__ import annotations

import math
from PIL import Image, ImageChops, ImageFilter

from .schemas import MaskBuildResult


# Protected roles — AI-generation strictly forbidden here
_PROTECTED_ROLES = frozenset({
    "product", "logo", "text", "cta", "badge",
    "person_face", "person", "person_or_hand",
})

# Roles whose removal opens up an inpaint target
_REMOVAL_ROLES = frozenset({
    "text", "cta", "badge", "logo",
    "product", "person", "person_or_hand",
})

# Default dilation radii (fraction of short side, clamped to min/max px)
_DILATION_RATIOS: dict[str, tuple[float, int, int]] = {
    "product":          (0.003, 2, 4),
    "person":           (0.004, 3, 6),
    "person_or_hand":   (0.004, 3, 6),
    "person_face":      (0.003, 2, 5),
    "text":             (0.003, 2, 4),
    "cta":              (0.003, 2, 4),
    "logo":             (0.002, 2, 3),
    "badge":            (0.002, 2, 3),
}
_DEFAULT_DILATION = (0.002, 2, 4)


def _compute_dilation(role: str, canvas_w: int, canvas_h: int) -> int:
    short_side = min(canvas_w, canvas_h)
    ratio, lo, hi = _DILATION_RATIOS.get(role, _DEFAULT_DILATION)
    px = int(round(short_side * ratio))
    return max(lo, min(hi, px))


def _dilate(mask: Image.Image, radius: int) -> Image.Image:
    if radius <= 0:
        return mask
    return mask.filter(ImageFilter.MaxFilter(size=max(3, radius * 2 + 1)))


def _feather(mask: Image.Image, radius: int) -> Image.Image:
    if radius <= 0:
        return mask
    return mask.filter(ImageFilter.GaussianBlur(radius=radius))


def _union(a: Image.Image, b: Image.Image) -> Image.Image:
    return ImageChops.lighter(a, b)


def _mask_from_bbox(
    bbox: dict,
    canvas_w: int,
    canvas_h: int,
) -> Image.Image:
    mask = Image.new("L", (canvas_w, canvas_h), 0)
    x = max(0, int(bbox.get("x", 0)))
    y = max(0, int(bbox.get("y", 0)))
    w = max(0, int(bbox.get("width", 0)))
    h = max(0, int(bbox.get("height", 0)))
    w = min(w, canvas_w - x)
    h = min(h, canvas_h - y)
    if w > 0 and h > 0:
        mask.paste(Image.new("L", (w, h), 255), (x, y))
    return mask


def _mask_from_object(
    obj: dict,
    canvas_w: int,
    canvas_h: int,
    dilation: int = 0,
) -> Image.Image:
    # prefer explicit mask image
    mask_img = obj.get("_maskImg")
    if mask_img is not None and isinstance(mask_img, Image.Image):
        m = mask_img.convert("L")
        if m.size != (canvas_w, canvas_h):
            m = m.resize((canvas_w, canvas_h), Image.LANCZOS)
    else:
        bbox = obj.get("bbox") or {}
        m = _mask_from_bbox(bbox, canvas_w, canvas_h)

    if dilation > 0:
        m = _dilate(m, dilation)
    return m


def _count_white(mask: Image.Image) -> int:
    return sum(1 for v in mask.getdata() if v > 127)


def build_masks(
    canvas_w: int,
    canvas_h: int,
    protected_objects: list[dict],
    removal_objects: list[dict] | None = None,
    target_w: int | None = None,
    target_h: int | None = None,
    external_removal_mask: Image.Image | None = None,
    feather_px: int = 3,
) -> MaskBuildResult:
    """Build all Stage 19 masks.

    Args:
        canvas_w/h: source canvas size
        protected_objects: objects to never overwrite
        removal_objects: objects whose footprint should be inpainted
                         (defaults to same as protected_objects filtered by role)
        target_w/h: final canvas size (for outpaint mask); None = same as canvas
        external_removal_mask: pre-built removal mask (overrides auto build)
        feather_px: feather radius on generation_allowed_mask boundary

    Returns:
        MaskBuildResult with all mask images and metadata.
    """
    result = MaskBuildResult()
    warnings: list[str] = []
    total_px = canvas_w * canvas_h

    tw = target_w or canvas_w
    th = target_h or canvas_h

    blank = Image.new("L", (canvas_w, canvas_h), 0)

    # ── per-role masks ────────────────────────────────────────────────────────
    role_masks: dict[str, Image.Image] = {}
    all_protected = blank.copy()
    all_removal = blank.copy()

    for obj in (protected_objects or []):
        role = obj.get("role", "unknown")
        dil = _compute_dilation(role, canvas_w, canvas_h)
        m = _mask_from_object(obj, canvas_w, canvas_h, dilation=dil)

        if role in role_masks:
            role_masks[role] = _union(role_masks[role], m)
        else:
            role_masks[role] = m

        if role in _PROTECTED_ROLES:
            all_protected = _union(all_protected, m)

        if role in _REMOVAL_ROLES:
            all_removal = _union(all_removal, m)

    result.product_mask  = role_masks.get("product",         blank.copy())
    result.person_mask   = _union(
        role_masks.get("person", blank.copy()),
        role_masks.get("person_or_hand", blank.copy()),
    )
    result.text_mask     = role_masks.get("text",            blank.copy())
    result.logo_mask     = role_masks.get("logo",            blank.copy())
    result.cta_mask      = role_masks.get("cta",             blank.copy())
    result.protected_mask = all_protected

    # ── removal mask ──────────────────────────────────────────────────────────
    if external_removal_mask is not None:
        rm = external_removal_mask.convert("L")
        if rm.size != (canvas_w, canvas_h):
            rm = rm.resize((canvas_w, canvas_h), Image.LANCZOS)
        result.removal_mask = rm
    else:
        # Objects from removal_objects list (or use auto-detected)
        if removal_objects is not None:
            rm = blank.copy()
            for obj in removal_objects:
                role = obj.get("role", "unknown")
                dil = _compute_dilation(role, canvas_w, canvas_h)
                m = _mask_from_object(obj, canvas_w, canvas_h, dilation=dil)
                rm = _union(rm, m)
        else:
            rm = all_removal
        result.removal_mask = rm

    # ── protection check: ensure removal doesn't eat protected pixels ─────────
    # removal mask AND protected mask = overlap pixels that must be cleared
    overlap = ImageChops.darker(result.removal_mask, all_protected)
    overlap_count = _count_white(overlap)
    result.protected_overlap_pixels = overlap_count
    if overlap_count > 0:
        warnings.append(f"removalOverlapsProtected:{overlap_count}px")
        result.mask_touches_protected_object = True

    # ── outpaint mask (expansion region) ─────────────────────────────────────
    if (tw, th) != (canvas_w, canvas_h):
        outpaint_mask = Image.new("L", (tw, th), 255)
        # existing canvas area = not outpaint
        paste_x = (tw - canvas_w) // 2 if tw >= canvas_w else 0
        paste_y = (th - canvas_h) // 2 if th >= canvas_h else 0
        existing_area = Image.new("L", (min(canvas_w, tw), min(canvas_h, th)), 0)
        outpaint_mask.paste(existing_area, (paste_x, paste_y))
        result.outpaint_mask = outpaint_mask
    else:
        result.outpaint_mask = Image.new("L", (tw, th), 0)

    # ── generation masks ──────────────────────────────────────────────────────
    # blocked: protected objects on canvas
    gen_blocked = all_protected.copy()

    # allowed: removal areas + outpaint areas, feathered, minus blocked
    gen_allowed = result.removal_mask.copy()
    if feather_px > 0:
        gen_allowed = _feather(gen_allowed, feather_px)
    # do NOT allow inside protected zone
    gen_allowed = ImageChops.subtract(gen_allowed, gen_blocked)

    result.generation_allowed_mask = gen_allowed
    result.generation_blocked_mask = gen_blocked

    # ── area ratios ───────────────────────────────────────────────────────────
    result.removal_mask_area_ratio = round(_count_white(result.removal_mask) / max(total_px, 1), 4)
    result.protected_mask_area_ratio = round(_count_white(all_protected) / max(total_px, 1), 4)
    result.outpaint_mask_area_ratio = round(
        _count_white(result.outpaint_mask) / max(tw * th, 1), 4
    )
    result.mask_dilation_px = max(
        (_compute_dilation(obj.get("role", ""), canvas_w, canvas_h) for obj in (protected_objects or [])),
        default=0,
    )
    result.mask_feather_px = feather_px
    result.warnings = warnings
    return result
