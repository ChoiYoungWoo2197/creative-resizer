"""Stage 20.2: Source-faithful repair prompt builder.

Prompts are versioned via PROMPT_VERSIONS dict. Never hard-code prompts inline
in pipeline code — always build via build_prompt().
"""
from __future__ import annotations

# ── Prompt templates ──────────────────────────────────────────────────────────

_SOURCE_FAITHFUL_REPAIR_V1 = """\
Edit the provided reference image directly.

This is a source-faithful image editing and reconstruction task.
This is not a new scene generation task and not a free-form
background redesign task.

PRIMARY GOAL

Remove only the product and all advertising elements while
preserving the original caregiving photograph as faithfully as possible.

PIXEL PRESERVATION RULE

Preserve every visible original pixel outside the removal and extension masks.
Do not repaint, regenerate, reinterpret, retouch, enhance,
beautify, or restyle any visible hand, finger, skin, arm,
jewelry, sleeve, or unaffected background region.
Generate new pixels only:
1. inside areas previously covered by the removed product and
   advertising elements;
2. in additional canvas areas required to reach the target aspect ratio.

PRESERVE EXACTLY

- all currently visible original hands, fingers, arms, and skin;
- the elderly hand and the supporting adult hands;
- the original hand positions, gestures, anatomy, proportions, and overlap;
- all rings, bracelets, and jewelry;
- the original cream-colored sleeves;
- the original camera angle;
- the original subject scale and visible framing;
- the original warm beige color tone;
- the original lighting direction, highlights, shadows, grain,
  focus, and depth of field;
- the original softly defocused domestic background;
- all unaffected source pixels outside the edit mask.

REMOVE COMPLETELY

- the cosmetic tube and all of its visible shadow;
- all Korean and English text;
- all black text boxes and graphic panels;
- the yellow CTA area;
- all logos, product labels, numbers, badges, icons, symbols,
  and watermarks;
- all remnants, outlines, reflections, or shadows belonging
  to these removed elements.

RECONSTRUCT ONLY THE REMOVED AREAS

Where the removed product previously covered the hands:
- reconstruct only the hidden portions inside the removal mask;
- continue the most likely existing hand contours from the surrounding source pixels;
- match the nearby skin tone, wrinkles, age spots, highlights,
  shadows, grain, focus, and perspective;
- maintain plausible hand anatomy and continuous finger contours;
- do not change any already-visible hand pixels;
- do not add extra fingers, hands, arms, or people.

Where text, CTA, boxes, logos, or graphics are removed:
- reconstruct the scene from the nearest surrounding visual evidence;
- restore nearby skin, sleeve, or neutral domestic background texture
  as appropriate;
- match the original warm beige palette, lighting, photographic grain,
  depth of field, and perspective;
- preserve the original optical background blur where it naturally exists.

CANVAS EXTENSION

- Output an exact {target_width} x {target_height} banner.
- Preserve the original camera angle, subject scale, hand placement,
  and visible composition.
- Do not crop, move, resize, or redesign the hands to reach the target ratio.
- If additional canvas is required, extend only the surrounding background.
- Use the source image as the strict reference for color, lighting,
  texture, focus, and scene continuity.
- Keep optional clean negative space available for later deterministic
  placement of the original product, copy, logo, and CTA.

STRICTLY PROHIBITED

- do not generate a replacement cosmetic product, tube, bottle,
  packaging, or product-like object;
- do not generate text, letters, Korean characters, numbers,
  labels, logos, buttons, badges, icons, symbols, or watermarks;
- do not create text-like decorative marks;
- do not create new hands, fingers, skin, arms, people, faces,
  or body parts;
- do not smooth, beautify, de-age, or stylize the elderly skin;
- do not change jewelry or sleeve details;
- do not create flowers, furniture, fabric arrangements, or
  decorative props not present in the source;
- do not redesign the scene;
- do not leave empty boxes, flat patches, masks, seams,
  or visible removal marks;
- do not duplicate any part of the hands or background.

BLUR AND EXTENSION RULE

- Preserve the original optical depth-of-field blur already
  present in the photograph.
- Do not use synthetic smart-fit blur-fill.
- Do not enlarge and blur a duplicate of the source to fill the canvas.
- Do not use mirrored edges, stretched textures, repeated patterns,
  or duplicated fragments.
- Do not replace the domestic background with an artificial studio background.

OUTPUT REQUIREMENTS

- exact dimensions: {target_width} x {target_height};
- photorealistic;
- seamless and source-faithful;
- original caregiving scene preserved;
- hands remain the central visual subject;
- only masked removal and extension regions are reconstructed;
- suitable as a clean plate for later deterministic compositing
  of the original product, text, logo, and CTA.

When uncertain, preserve the source image rather than inventing
new visual content.\
"""

# Attempt 2: more conservative prompt
_SOURCE_FAITHFUL_REPAIR_V1_CONSERVATIVE = """\
Edit the provided reference image.
Generate new pixels ONLY inside the provided mask regions.
Do not repaint any unmasked area.
Do not generate text, logos, products, or additional hands.
Inside the mask: restore using nearest surrounding pixels.
Match original color, lighting, texture, and focus.
Output exact size: {target_width} x {target_height}.\
"""

# Attempt 3: minimal restoration prompt
_SOURCE_FAITHFUL_REPAIR_V1_MINIMAL = """\
Minimally restore the masked regions using only adjacent pixel information.
Match exact surrounding texture, color, and lighting.
Do not add any new objects, text, hands, logos, or products.
No scene redesign. Output: {target_width} x {target_height}.\
"""

# ── Prompt registry ───────────────────────────────────────────────────────────

PROMPT_VERSIONS: dict[str, str] = {
    "source-faithful-repair-v1": _SOURCE_FAITHFUL_REPAIR_V1,
    "source-faithful-repair-v1-conservative": _SOURCE_FAITHFUL_REPAIR_V1_CONSERVATIVE,
    "source-faithful-repair-v1-minimal": _SOURCE_FAITHFUL_REPAIR_V1_MINIMAL,
}

LATEST_VERSION = "source-faithful-repair-v1"

# Per-attempt version progression
ATTEMPT_VERSION_SEQUENCE = [
    "source-faithful-repair-v1",
    "source-faithful-repair-v1-conservative",
    "source-faithful-repair-v1-minimal",
]

# ── Spec-specific augmentations ───────────────────────────────────────────────

_SPEC_AUGMENTATIONS: dict[tuple[int, int], str] = {
    (1250, 560): (
        "Extend only the surrounding domestic background horizontally "
        "where required. Keep the hands at their original scale and position."
    ),
    (1200, 300): (
        "Create a very wide source-faithful extension using only the "
        "existing beige domestic environment as reference. "
        "Do not invent additional objects or redesign the room."
    ),
    (300, 1200): (
        "Extend only the surrounding background vertically. "
        "Maintain the original hands, sleeves, scale, lighting, and "
        "photographic perspective without creating a new scene."
    ),
}


def build_prompt(
    prompt_version: str,
    target_width: int,
    target_height: int,
    spec_augmentation: bool = True,
) -> str:
    """Build a versioned prompt string with target dimensions substituted.

    Raises ValueError for unknown prompt_version.
    """
    template = PROMPT_VERSIONS.get(prompt_version)
    if template is None:
        raise ValueError(f"Unknown prompt version: {prompt_version!r}. "
                         f"Known: {list(PROMPT_VERSIONS)}")
    prompt = template.format(target_width=target_width, target_height=target_height)
    if spec_augmentation:
        aug = _SPEC_AUGMENTATIONS.get((target_width, target_height), "")
        if aug:
            prompt = f"{prompt}\n\n{aug}"
    return prompt


def get_attempt_version(attempt_index: int) -> str:
    """Return prompt version for attempt_index (0-based). Clamps to last version."""
    idx = min(attempt_index, len(ATTEMPT_VERSION_SEQUENCE) - 1)
    return ATTEMPT_VERSION_SEQUENCE[idx]
