"""Stage 21 Bundle A: Background-only plate builder.

Builds a clean background-only image to send to the AI provider.
Ensures no human / product / text / logo / CTA pixels contaminate the AI context.

Strategy A:  Composite only background-role PSD layers (preferred).
Strategy B:  Blank foreground bbox areas in the full source composite.
Strategy C:  FAIL — no silent fallback to the full composite.

Roles included in the background plate:
  background, background_fill, background_texture, environmental_background

Roles explicitly excluded (foreground):
  human_subject, person, person_or_hand, hand, face, skin,
  product, main_image,
  title, headline, body_text, text,
  badge, logo, cta,
  decorative (ambiguous — treated as foreground for safety),
  unknown (no confirmed background evidence — treated as foreground)
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from PIL import Image

# ── Role sets ─────────────────────────────────────────────────────────────────

BACKGROUND_PLATE_ROLES = frozenset({
    "background",
    "background_fill",
    "background_texture",
    "environmental_background",
})

FOREGROUND_EXCLUDED_ROLES = frozenset({
    "human_subject", "person", "person_or_hand", "hand", "face", "skin",
    "product", "main_image",
    "title", "headline", "body_text", "text",
    "badge", "logo", "cta",
    # Decorative and unknown: no confirmed background evidence → exclude
    "decorative",
    "unknown",
})


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class BackgroundPlateResult:
    success: bool = False
    strategy: str = ""  # "layer_composite" | "foreground_blank" | ""

    image: object = None  # PIL Image (RGBA or RGB) or None

    included_layer_ids: list[str] = field(default_factory=list)
    excluded_layer_ids: list[str] = field(default_factory=list)
    excluded_foreground_objects: list[dict] = field(default_factory=list)

    # Mask: areas in the background plate that AI should fill
    # (outpaint margins + transparent holes + blanked foreground areas)
    # Used by SFR to build generationAllowedMask.
    foreground_removal_mask: object = None  # PIL Image "L"

    background_pixel_sha256: str = ""
    removal_pixel_count: int = 0

    failure_reason: str = ""
    warnings: list[str] = field(default_factory=list)


# ── Main entry point ──────────────────────────────────────────────────────────

def build_background_plate(
    source_composite: "Image.Image",
    psd_layers: list[dict],
    canvas_w: int,
    canvas_h: int,
    render_context=None,
) -> BackgroundPlateResult:
    """Build a background-only plate for AI provider input.

    Args:
        source_composite: Full flat PSD composite (used only as fallback for Strategy B).
        psd_layers:       classify_layers() output — each item may have _layer_obj.
        canvas_w/h:       Original PSD canvas dimensions.
        render_context:   AiRenderContext for logging (optional).

    Returns:
        BackgroundPlateResult. success=False → caller MUST NOT fall back to source_composite.
    """
    result = BackgroundPlateResult()

    if not psd_layers:
        result.failure_reason = "BACKGROUND_LAYER_NOT_FOUND"
        _log(result, render_context)
        return result

    # ── Classify layers ───────────────────────────────────────────────────────
    bg_layers: list[dict] = []
    fg_layers: list[dict] = []

    for layer in psd_layers:
        role = layer.get("role", "unknown")
        if role in BACKGROUND_PLATE_ROLES:
            bg_layers.append(layer)
        else:
            # Everything not explicitly background (including decorative, unknown) → foreground
            fg_layers.append(layer)

    result.included_layer_ids = [l.get("id", "") for l in bg_layers]
    result.excluded_layer_ids = [l.get("id", "") for l in fg_layers]
    result.excluded_foreground_objects = [
        {
            "id": l.get("id", ""),
            "name": l.get("name", ""),
            "role": l.get("role", "unknown"),
            "bbox": l.get("bbox", {}),
        }
        for l in fg_layers
    ]

    # ── Strategy A: composite background layers via psd-tools ─────────────────
    plate: "Image.Image | None" = None
    if bg_layers:
        plate = _try_composite_background_layers(bg_layers, canvas_w, canvas_h)
        if plate is not None:
            result.strategy = "layer_composite"
        else:
            result.warnings.append("BACKGROUND_LAYER_COMPOSITE_FAILED: trying strategy B")

    # ── Strategy B: blank foreground bboxes in source composite ───────────────
    if plate is None:
        plate = _try_blank_foreground(source_composite, fg_layers, canvas_w, canvas_h)
        if plate is not None:
            result.strategy = "foreground_blank"
            result.warnings.append("BACKGROUND_PLATE_STRATEGY_B: foreground areas blanked in composite")

    # ── Strategy C: fail closed ───────────────────────────────────────────────
    if plate is None:
        result.failure_reason = "BACKGROUND_PLATE_BUILD_FAILED"
        _log(result, render_context)
        return result

    # Validate: plate must not be fully transparent / empty
    try:
        extrema = plate.convert("L").getextrema()
        if extrema[1] == 0:
            result.failure_reason = "BACKGROUND_PLATE_EMPTY"
            _log(result, render_context)
            return result
    except Exception:
        pass

    # ── Foreground removal mask (where AI should fill) ────────────────────────
    removal_mask = _build_foreground_bbox_mask(fg_layers, canvas_w, canvas_h)
    result.foreground_removal_mask = removal_mask
    result.removal_pixel_count = _mask_pixel_count(removal_mask)

    result.image = plate
    result.background_pixel_sha256 = _sha256_image(plate)
    result.success = True

    _log(result, render_context)
    return result


# ── Strategy A ────────────────────────────────────────────────────────────────

def _try_composite_background_layers(
    bg_layers: list[dict],
    canvas_w: int,
    canvas_h: int,
) -> "Image.Image | None":
    """Composite background-role layers in depth order (deepest first)."""
    if not bg_layers:
        return None

    # Higher depth value = further back in PSD z-stack → painted first
    sorted_layers = sorted(bg_layers, key=lambda l: l.get("depth", 0), reverse=True)

    canvas = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    any_placed = False

    for layer in sorted_layers:
        lobj = layer.get("_layer_obj")
        if lobj is None:
            continue
        try:
            limg = lobj.composite()
            if limg is None or limg.width <= 0 or limg.height <= 0:
                continue
            limg = limg.convert("RGBA")
            bbox = layer.get("bbox", {})
            x = int(bbox.get("x", 0))
            y = int(bbox.get("y", 0))
            canvas.paste(limg, (x, y), limg)
            any_placed = True
            print(
                f"[BG_PLATE_A] placed role={layer.get('role')!r}"
                f" name={layer.get('name')!r}"
                f" size={limg.width}x{limg.height} @ ({x},{y})",
                flush=True,
            )
        except Exception as e:
            print(f"[BG_PLATE_A] skip layer={layer.get('name')!r}: {e}", flush=True)

    return canvas if any_placed else None


# ── Strategy B ────────────────────────────────────────────────────────────────

def _try_blank_foreground(
    source_composite: "Image.Image",
    fg_layers: list[dict],
    canvas_w: int,
    canvas_h: int,
) -> "Image.Image | None":
    """Blank foreground bbox areas (transparent) from source composite."""
    if source_composite is None:
        return None
    try:
        from PIL import ImageDraw
        plate = source_composite.convert("RGBA").copy()
        draw = ImageDraw.Draw(plate)
        any_blanked = False
        for layer in fg_layers:
            bbox = layer.get("bbox", {})
            x = int(bbox.get("x", 0))
            y = int(bbox.get("y", 0))
            w = int(bbox.get("width", 0))
            h = int(bbox.get("height", 0))
            if w <= 0 or h <= 0:
                continue
            draw.rectangle([x, y, x + w - 1, y + h - 1], fill=(0, 0, 0, 0))
            any_blanked = True
            print(
                f"[BG_PLATE_B] blanked role={layer.get('role')!r}"
                f" name={layer.get('name')!r} bbox=({x},{y},{w},{h})",
                flush=True,
            )
        return plate if any_blanked else source_composite.convert("RGBA").copy()
    except Exception as e:
        print(f"[BG_PLATE_B] failed: {e}", flush=True)
        return None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_foreground_bbox_mask(
    fg_layers: list[dict],
    canvas_w: int,
    canvas_h: int,
    dilation_px: int = 3,
) -> "Image.Image | None":
    """L-mode mask: 255 where foreground layers were (AI fill target)."""
    if not fg_layers:
        return None
    from PIL import ImageDraw
    mask = Image.new("L", (canvas_w, canvas_h), 0)
    draw = ImageDraw.Draw(mask)
    any_drawn = False
    for layer in fg_layers:
        bbox = layer.get("bbox", {})
        x = int(bbox.get("x", 0))
        y = int(bbox.get("y", 0))
        w = int(bbox.get("width", 0))
        h = int(bbox.get("height", 0))
        if w <= 0 or h <= 0:
            continue
        x0 = max(0, x - dilation_px)
        y0 = max(0, y - dilation_px)
        x1 = min(canvas_w, x + w + dilation_px)
        y1 = min(canvas_h, y + h + dilation_px)
        draw.rectangle([x0, y0, x1, y1], fill=255)
        any_drawn = True
    return mask if any_drawn else None


def _sha256_image(img: "Image.Image") -> str:
    try:
        data = img.convert("RGBA").tobytes()
        return hashlib.sha256(data).hexdigest()
    except Exception:
        return ""


def _mask_pixel_count(mask: "Image.Image | None") -> int:
    if mask is None:
        return 0
    return sum(1 for p in mask.getdata() if p > 127)


def _log(result: BackgroundPlateResult, render_context=None) -> None:
    job_id = getattr(render_context, "job_id", "") if render_context else ""
    spec_id = getattr(render_context, "spec_id", "") if render_context else ""
    sha_short = result.background_pixel_sha256[:16] if result.background_pixel_sha256 else ""
    print(
        f"[BACKGROUND_PLATE]"
        f" jobId={job_id}"
        f" specId={spec_id}"
        f" success={result.success}"
        f" strategy={result.strategy!r}"
        f" includedLayerCount={len(result.included_layer_ids)}"
        f" excludedForegroundCount={len(result.excluded_layer_ids)}"
        f" removalPixelCount={result.removal_pixel_count}"
        f" backgroundPixelSha256={sha_short}"
        f" failureReason={result.failure_reason!r}"
        f" warnings={result.warnings}",
        flush=True,
    )
