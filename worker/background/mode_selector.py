"""Stage 20.2: Background generation mode selector.

Determines which background generation mode to use for a given PSD/image:
  - source_faithful_repair : for photos with real subjects (hands, people, etc.)
  - generative_background  : for abstract/product-only imagery

mother-hand-product.psd → source_faithful_repair (human_subject_detected)
"""
from __future__ import annotations

SOURCE_FAITHFUL_REPAIR = "source_faithful_repair"
GENERATIVE_BACKGROUND = "generative_background"

# Roles / name hints that indicate a real human subject
_HUMAN_ROLES = frozenset({
    "person", "person_or_hand", "person_face", "hand", "main_image",
})
_HUMAN_NAME_HINTS = frozenset({
    "hand", "face", "skin", "model", "person", "portrait", "human",
    "손", "손가락", "얼굴", "피부", "사람", "모델",
})

# Roles that count as protected objects for multi-object heuristic
_PROTECTED_ROLES = frozenset({
    "product", "logo", "main_image", "cta", "title",
})


def _has_human_subject(classified_layers: list[dict]) -> bool:
    """True if classified layers indicate a real-life human subject."""
    for layer in classified_layers:
        role = (layer.get("role") or "").lower()
        name = (layer.get("name") or "").lower()
        if role in _HUMAN_ROLES:
            return True
        if any(hint in name for hint in _HUMAN_NAME_HINTS):
            return True
    return False


def _has_multiple_protected_objects(classified_layers: list[dict]) -> bool:
    """True if 2+ distinct protected object types are present."""
    found = {l.get("role") for l in classified_layers if l.get("role") in _PROTECTED_ROLES}
    return len(found) >= 2


def _is_photographic_scene(classified_layers: list[dict]) -> bool:
    """Heuristic: if main_image is a raster pixel layer, likely a photo."""
    for layer in classified_layers:
        if layer.get("role") == "main_image" and layer.get("type") in ("pixel", "smartobject"):
            return True
    return False


def select_background_mode(
    classified_layers: list[dict],
    source_image=None,
    forced_mode: str = "",
) -> tuple[str, str]:
    """Determine background generation mode for this image.

    Returns (mode, reason):
        mode   = SOURCE_FAITHFUL_REPAIR | GENERATIVE_BACKGROUND
        reason = short diagnostic string
    """
    if forced_mode in (SOURCE_FAITHFUL_REPAIR, GENERATIVE_BACKGROUND):
        return forced_mode, f"forced:{forced_mode}"

    if _has_human_subject(classified_layers):
        return SOURCE_FAITHFUL_REPAIR, "human_subject_detected"

    if _has_multiple_protected_objects(classified_layers) and _is_photographic_scene(classified_layers):
        return SOURCE_FAITHFUL_REPAIR, "photographic_scene_with_multiple_protected_objects"

    return GENERATIVE_BACKGROUND, "no_human_subject_detected"
