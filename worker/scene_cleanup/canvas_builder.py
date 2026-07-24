"""Bundle D-1: Build provider canvas for semantic scene cleanup.

cover-crop: scale source to fill target completely, then center-crop.
mask: full white (255 everywhere) — AI edits entire image semantically.

subject-preserving-outpaint: contain-scale source into target canvas (letterbox),
mask=white only in new-canvas (outpaint) regions — AI fills sides/top/bottom.

NO foreground bbox mask.  NO split BG/FG scales.  NO repair mask.
"""
from __future__ import annotations

import numpy as np

from scene_cleanup.models import (
    FullImageSource, SceneCanvasTransform,
    TRANSFORM_STRATEGY_COVER_CROP, MASK_STRATEGY_FULL_CANVAS,
    TRANSFORM_STRATEGY_SUBJECT_PRESERVING_OUTPAINT, MASK_STRATEGY_OUTPAINT_REGIONS,
)


def build_provider_canvas(
    full_image_source: FullImageSource,
    target_w: int,
    target_h: int,
) -> tuple:
    """Cover-crop source to target and create full-white semantic mask.

    Returns:
        (provider_input_image, mask_image, canvas_transform)

    provider_input_image: RGB PIL Image at (target_w, target_h)
    mask_image: L-mode PIL Image, all pixels = 255 (full canvas semantic edit)
    canvas_transform: SceneCanvasTransform with deterministic provenance
    """
    from PIL import Image

    src_w = full_image_source.width
    src_h = full_image_source.height
    src_img = full_image_source.image

    if src_img.mode not in ("RGBA", "RGB"):
        src_img = src_img.convert("RGB")

    # Cover-crop: scale so image fills target in both dimensions
    scale_x = target_w / src_w
    scale_y = target_h / src_h
    scale = max(scale_x, scale_y)

    scaled_w = max(int(src_w * scale + 0.5), target_w)
    scaled_h = max(int(src_h * scale + 0.5), target_h)
    scaled = src_img.resize((scaled_w, scaled_h), Image.LANCZOS)

    # Center-crop to exact target
    crop_x = (scaled_w - target_w) // 2
    crop_y = (scaled_h - target_h) // 2
    provider_input = scaled.crop((crop_x, crop_y, crop_x + target_w, crop_y + target_h))

    if provider_input.mode == "RGBA":
        provider_input = provider_input.convert("RGB")

    # Full-white mask — semantic edit over entire canvas
    mask = Image.new("L", (target_w, target_h), 255)

    transform = SceneCanvasTransform(
        strategy=TRANSFORM_STRATEGY_COVER_CROP,
        source_w=src_w,
        source_h=src_h,
        canvas_w=target_w,
        canvas_h=target_h,
        scale=round(scale, 6),
        crop_x=crop_x,
        crop_y=crop_y,
        outpaint_required=False,  # cover_crop always fills without gaps
        mask_strategy=MASK_STRATEGY_FULL_CANVAS,
    )

    return provider_input, mask, transform


def build_provider_canvas_outpaint(
    full_image_source: FullImageSource,
    target_w: int,
    target_h: int,
) -> tuple:
    """Contain-scale source into target canvas (subject-preserving outpaint).

    Scales source with contain semantics (no cropping), centers it in the
    target canvas, and creates a mask that is white ONLY in the new outpaint
    regions (sides/top/bottom). The source area mask is black (preserved).

    Returns:
        (provider_input_image, mask_image, canvas_transform, allowed_generation_mask)

    provider_input_image: RGB PIL Image at (target_w, target_h)
        Source letterboxed into target canvas; outpaint areas are neutral grey.
    mask_image: L-mode PIL Image, 255=outpaint regions, 0=source region
    canvas_transform: SceneCanvasTransform with outpaint_required=True
    allowed_generation_mask: numpy uint8 (H, W) identical to mask_image array
    """
    from PIL import Image

    src_w = full_image_source.width
    src_h = full_image_source.height
    src_img = full_image_source.image

    if src_img.mode not in ("RGBA", "RGB"):
        src_img = src_img.convert("RGB")
    src_rgb = src_img.convert("RGB")

    # Contain-scale: fit source into target without cropping
    scale_x = target_w / src_w if src_w > 0 else 1.0
    scale_y = target_h / src_h if src_h > 0 else 1.0
    scale = min(scale_x, scale_y)

    scaled_w = max(int(src_w * scale + 0.5), 1)
    scaled_h = max(int(src_h * scale + 0.5), 1)
    scaled = src_rgb.resize((scaled_w, scaled_h), Image.LANCZOS)

    # Center the scaled source in the target canvas
    offset_x = (target_w - scaled_w) // 2
    offset_y = (target_h - scaled_h) // 2

    # Build provider input: neutral grey canvas with source pasted at center
    provider_input = Image.new("RGB", (target_w, target_h), (128, 128, 128))
    provider_input.paste(scaled, (offset_x, offset_y))

    # Outpaint mask: white = AI fills here, black = preserve source region
    mask_arr = np.full((target_h, target_w), 255, dtype=np.uint8)
    x1 = max(0, offset_x)
    y1 = max(0, offset_y)
    x2 = min(target_w, offset_x + scaled_w)
    y2 = min(target_h, offset_y + scaled_h)
    if x2 > x1 and y2 > y1:
        mask_arr[y1:y2, x1:x2] = 0
    mask = Image.fromarray(mask_arr, "L")

    # allowed_generation_mask: same as mask (outpaint regions only)
    allowed_generation_mask = mask_arr.copy()

    transform = SceneCanvasTransform(
        strategy=TRANSFORM_STRATEGY_SUBJECT_PRESERVING_OUTPAINT,
        source_w=src_w,
        source_h=src_h,
        canvas_w=target_w,
        canvas_h=target_h,
        scale=round(scale, 6),
        crop_x=0,    # no crop in outpaint mode
        crop_y=0,
        outpaint_required=(scaled_w < target_w or scaled_h < target_h),
        mask_strategy=MASK_STRATEGY_OUTPAINT_REGIONS,
        paste_offset_x=offset_x,
        paste_offset_y=offset_y,
        scaled_width=scaled_w,
        scaled_height=scaled_h,
        mapped_rect={"x1": offset_x, "y1": offset_y,
                     "x2": offset_x + scaled_w, "y2": offset_y + scaled_h},
    )

    return provider_input, mask, transform, allowed_generation_mask


def build_canonical_at_target(
    source_img: object,
    canvas_transform: object,
) -> object:
    """Build a target-sized canonical reference image from source using transform geometry.

    For subject_preserving_outpaint: contain-scale source and paste at paste_offset.
    For cover_crop: cover-scale source and crop at crop_x/y.
    Other strategies: resize to target (best-effort).

    This is the correct canonical for pixel restoration in Stage 4:
    canonical_at_target.size == (target_w, target_h) == ai_result.size,
    eliminating the size mismatch that caused allowedGenerationCoverage=1.0.
    """
    from PIL import Image

    if source_img is None or canvas_transform is None:
        raise RuntimeError("CANONICAL_BUILD_NULL_INPUT: source_img and canvas_transform are required")

    tw = int(getattr(canvas_transform, "canvas_w", 0))
    th = int(getattr(canvas_transform, "canvas_h", 0))
    if tw <= 0 or th <= 0:
        raise RuntimeError(f"CANONICAL_BUILD_INVALID_TARGET: canvas={tw}x{th}")

    strategy = str(getattr(canvas_transform, "strategy", ""))
    scale = float(getattr(canvas_transform, "scale", 1.0))

    if not isinstance(source_img, Image.Image):
        raise RuntimeError("CANONICAL_BUILD_NOT_PIL_IMAGE")

    src_rgb = source_img.convert("RGB")

    if strategy == "subject_preserving_outpaint":
        sw = int(getattr(canvas_transform, "scaled_width", 0))
        sh = int(getattr(canvas_transform, "scaled_height", 0))
        px = int(getattr(canvas_transform, "paste_offset_x", -1))
        py = int(getattr(canvas_transform, "paste_offset_y", -1))

        if sw <= 0 or sh <= 0:
            sw = max(int(src_rgb.width * scale + 0.5), 1)
            sh = max(int(src_rgb.height * scale + 0.5), 1)
        if px < 0:
            px = (tw - sw) // 2
        if py < 0:
            py = (th - sh) // 2

        scaled = src_rgb.resize((sw, sh), Image.LANCZOS)
        canvas = Image.new("RGB", (tw, th), (0, 0, 0))
        canvas.paste(scaled, (px, py))
        return canvas

    elif strategy == "cover_crop":
        cx = int(getattr(canvas_transform, "crop_x", 0))
        cy = int(getattr(canvas_transform, "crop_y", 0))
        sw_full = max(int(src_rgb.width * scale + 0.5), tw)
        sh_full = max(int(src_rgb.height * scale + 0.5), th)
        scaled_full = src_rgb.resize((sw_full, sh_full), Image.LANCZOS)
        return scaled_full.crop((cx, cy, cx + tw, cy + th))

    else:
        return src_rgb.resize((tw, th), Image.LANCZOS)
