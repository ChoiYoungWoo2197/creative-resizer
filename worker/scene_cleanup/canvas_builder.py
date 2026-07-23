"""Bundle D-1: Build provider canvas for semantic scene cleanup.

cover-crop: scale source to fill target completely, then center-crop.
mask: full white (255 everywhere) — AI edits entire image semantically.

NO foreground bbox mask.  NO split BG/FG scales.  NO repair mask.
"""
from __future__ import annotations

from scene_cleanup.models import (
    FullImageSource, SceneCanvasTransform,
    TRANSFORM_STRATEGY_COVER_CROP, MASK_STRATEGY_FULL_CANVAS,
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
