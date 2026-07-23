"""Stage 21 Bundle B: Determine layoutRole from semantic role + metadata.

layoutRole is separate from semanticRole:
  - semanticRole: original classification (never modified here)
  - layoutRole: used for placement decisions only

Priority:
  1. Object Map attempted role confirmed by geometry (immutable-rejected layers)
  2. PSD layer type=text → infer title/body_text/cta from geometry + content
  3. semanticRole fallback
"""
from __future__ import annotations

# Roles that require safe zone protection during placement
SAFE_ZONE_REQUIRED_ROLES: frozenset = frozenset({
    "product", "title", "headline", "body_text", "text",
    "logo", "badge", "cta",
})

# Roles for hero visuals — allowed to partially bleed outside safe zone
BLEED_ALLOWED_ROLES: frozenset = frozenset({
    "main_image", "human_subject", "person", "hand",
})

# Roles that can fully bleed to canvas edges (background / decorative)
CANVAS_BLEED_ROLES: frozenset = frozenset({
    "background", "background_fill", "background_texture",
    "environmental_background", "decorative",
})

# Roles considered "required" — layout fails if any required object is skipped
REQUIRED_ROLES: frozenset = frozenset({
    "product", "title", "headline", "body_text",
})

# Hero visual roles (get the "visual half" of the banner)
HERO_VISUAL_ROLES: frozenset = frozenset({
    "product", "main_image", "human_subject", "person", "hand",
})

# Visual-subject roles that the immutable guard may have protected
_VISUAL_SUBJECT_ROLES: frozenset = frozenset({
    "human_subject", "product", "main_image",
})


def resolve_layout_role(
    layer: dict,
    psd_layer: dict | None = None,
    attempted_role: str | None = None,
) -> tuple[str, str]:
    """Return (layoutRole, reason). semanticRole is never modified.

    Args:
        layer:         fg_layer dict from extract_foreground_layers()
        psd_layer:     corresponding psd_layers_classified entry (for type/textContent/canvasH)
        attempted_role: role that Object Map tried to assign (blocked by immutable guard)
    """
    semantic = layer.get("role", "unknown")
    ltype = (psd_layer or {}).get("type", "pixel") if psd_layer else "pixel"
    source_bbox = layer.get("sourceBBox") or layer.get("bbox", {})
    canvas_w = int((psd_layer or {}).get("canvasWidth", 0) or 0)
    canvas_h = int((psd_layer or {}).get("canvasHeight", 0) or 0)
    text_content = str((psd_layer or {}).get("textContent") or "")
    name = (layer.get("name") or "").lower()

    # 1. Object Map attempted_role confirmed by geometry
    if attempted_role and attempted_role != semantic:
        confirmed = _confirm_role_by_geometry(
            attempted_role, source_bbox, canvas_w, canvas_h, ltype, name
        )
        if confirmed:
            return attempted_role, f"object_map_attempted={attempted_role!r} geometry_confirmed"
        if attempted_role in SAFE_ZONE_REQUIRED_ROLES and attempted_role not in _VISUAL_SUBJECT_ROLES:
            return attempted_role, f"object_map_attempted={attempted_role!r} role_class"

    # 2. Text layer type → infer from position + content
    if ltype == "type":
        inferred = _infer_text_role(text_content or name, source_bbox, canvas_w, canvas_h)
        if inferred:
            return inferred, f"text_layer_type inferred={inferred!r}"

    # 3. Fallback
    return semantic, "semantic_role_fallback"


def _confirm_role_by_geometry(
    role: str,
    bbox: dict,
    canvas_w: int,
    canvas_h: int,
    ltype: str,
    name: str,
) -> bool:
    """Return True when the object's geometry is consistent with the given role."""
    w = bbox.get("width", 0)
    h = bbox.get("height", 0)
    if w <= 0 or h <= 0:
        return False
    aspect = w / h
    height_ratio = h / canvas_h if canvas_h > 0 else 0.5

    if role in ("title", "headline"):
        # Wide-and-short strip (aspect >= 2.0, height ≤ 20% of canvas)
        wide_strip = aspect >= 2.0 and height_ratio <= 0.20
        text_name = any(k in name for k in (
            "제목", "타이틀", "headline", "title", "copy", "text", "문구", "카피"
        ))
        return wide_strip or (text_name and height_ratio <= 0.40)

    if role == "body_text":
        return height_ratio <= 0.25

    if role == "cta":
        return height_ratio <= 0.15

    return False


def _infer_text_role(text: str, bbox: dict, canvas_w: int, canvas_h: int) -> str | None:
    """For type=text layers: infer role from content length and vertical position."""
    stripped = text.strip()
    length = len(stripped)
    cy = 0.5
    if canvas_h > 0:
        cy = (bbox.get("y", 0) + bbox.get("height", 0) / 2) / canvas_h

    if length <= 15 and cy >= 0.75:
        return "cta"
    if length > 30:
        return "body_text"
    if cy <= 0.55:
        return "title"
    return "body_text"
