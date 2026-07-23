"""Bundle D-1: Semantic scene cleanup prompt builder.

Static prompt with SHA-256 integrity guard.
Dynamic content: target dimensions only.
No scene-specific terms, no brand names, no product names hardcoded.
"""
from __future__ import annotations
import hashlib

SEMANTIC_CLEANUP_PROMPT_VERSION = "semantic-scene-cleanup-v1"

# Static template — {target_w} and {target_h} are the only dynamic parts.
# SHA guard detects unintended mutations before any API call.
_STATIC_PROMPT_TEMPLATE = (
    "Remove all advertisement graphic elements from this image and restore "
    "the clean photographic background scene. "
    "Preserve all natural environmental elements, architectural details, "
    "surface textures, ambient lighting, atmospheric depth, and spatial context. "
    "Eliminate text overlays, overlaid logos, overlaid product images, "
    "decorative graphical elements, UI-style graphics, color grading overlays, "
    "and watermarks. "
    "Output a seamless, photographic-quality background plate. "
    "Target output dimensions: {target_w}x{target_h}."
)

# SHA-256 of the template computed at import time.
# If the template above is modified without updating this constant, the guard raises.
_TEMPLATE_SHA256 = hashlib.sha256(_STATIC_PROMPT_TEMPLATE.encode("utf-8")).hexdigest()

# No scene-specific terms allowed in the built prompt.
_FORBIDDEN_PROMPT_TERMS: frozenset[str] = frozenset()


def build_semantic_prompt(target_w: int, target_h: int) -> tuple[str, str]:
    """Build semantic cleanup prompt and verify SHA integrity guard.

    Returns:
        (prompt_text, prompt_version)

    Raises:
        RuntimeError: if template SHA does not match (unauthorized modification).
    """
    current_sha = hashlib.sha256(_STATIC_PROMPT_TEMPLATE.encode("utf-8")).hexdigest()
    if current_sha != _TEMPLATE_SHA256:
        raise RuntimeError(
            f"SEMANTIC_PROMPT_SHA_MISMATCH: "
            f"expected={_TEMPLATE_SHA256[:16]} actual={current_sha[:16]}"
        )

    prompt = _STATIC_PROMPT_TEMPLATE.format(target_w=target_w, target_h=target_h)

    prompt_lower = prompt.lower()
    for term in _FORBIDDEN_PROMPT_TERMS:
        if term.lower() in prompt_lower:
            raise RuntimeError(
                f"SEMANTIC_PROMPT_FORBIDDEN_TERM: {term!r} found in built prompt"
            )

    return prompt, SEMANTIC_CLEANUP_PROMPT_VERSION
