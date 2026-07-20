"""Stage 19D — Shadow and Lighting Harmonization.

Generates a separate shadow layer to ground the product on the new background.
Product pixels are NEVER modified.

Shadow strategies:
  - contact_shadow   : thin drop shadow below product alpha bbox
  - soft_ellipse     : ellipsoidal soft shadow
  - alpha_projected  : derived from actual alpha mask shape
  - no_shadow        : explicit no-shadow candidate
"""
from __future__ import annotations

import math
import os
import time
from PIL import Image, ImageDraw, ImageFilter

from .schemas import BackgroundCandidate

# ── environment config ────────────────────────────────────────────────────────
_SHADOW_ENABLED     = os.environ.get("SHADOW_ENABLED", "true").lower() == "true"
_SHADOW_MAX_OPACITY = float(os.environ.get("SHADOW_MAX_OPACITY", "0.28"))
_SHADOW_MAX_BLUR    = float(os.environ.get("SHADOW_MAX_BLUR_RATIO", "0.035"))
_SHADOW_OFFSET      = float(os.environ.get("SHADOW_OFFSET_RATIO", "0.01"))


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


# ── shadow generators ─────────────────────────────────────────────────────────

def _contact_shadow(
    canvas_w: int,
    canvas_h: int,
    product_bbox: dict,
    opacity: float,
    blur_radius: int,
    offset_y: int,
) -> Image.Image:
    """Thin shadow band just below the product bounding box."""
    shadow = Image.new("L", (canvas_w, canvas_h), 0)
    x0 = max(0, int(product_bbox.get("x", 0)))
    y0 = max(0, int(product_bbox.get("y", 0)))
    w  = int(product_bbox.get("width", 0))
    h  = int(product_bbox.get("height", 0))
    x1 = min(canvas_w, x0 + w)
    shadow_y = min(canvas_h - 1, y0 + h + offset_y)
    shadow_h = max(1, blur_radius * 2)

    draw = ImageDraw.Draw(shadow)
    draw.ellipse(
        [x0, shadow_y, x1, shadow_y + shadow_h],
        fill=int(round(opacity * 255)),
    )
    if blur_radius > 0:
        shadow = shadow.filter(ImageFilter.GaussianBlur(radius=blur_radius))
    return shadow.convert("L")


def _soft_ellipse_shadow(
    canvas_w: int,
    canvas_h: int,
    product_bbox: dict,
    opacity: float,
    blur_radius: int,
    offset_y: int,
) -> Image.Image:
    """Soft elliptical shadow centered below product."""
    shadow = Image.new("L", (canvas_w, canvas_h), 0)
    x0 = max(0, int(product_bbox.get("x", 0)))
    y0 = max(0, int(product_bbox.get("y", 0)))
    w  = int(product_bbox.get("width", 1))
    h  = int(product_bbox.get("height", 1))

    cx = x0 + w // 2
    cy = min(canvas_h - 1, y0 + h + offset_y + blur_radius)
    ew = max(1, int(w * 0.7))
    eh = max(1, blur_radius * 2)

    draw = ImageDraw.Draw(shadow)
    draw.ellipse(
        [cx - ew // 2, cy - eh // 2, cx + ew // 2, cy + eh // 2],
        fill=int(round(opacity * 255)),
    )
    if blur_radius > 0:
        shadow = shadow.filter(ImageFilter.GaussianBlur(radius=max(1, blur_radius * 2)))
    return shadow.convert("L")


def _alpha_projected_shadow(
    canvas_w: int,
    canvas_h: int,
    product_mask: Image.Image,
    opacity: float,
    blur_radius: int,
    offset_x: int,
    offset_y: int,
) -> Image.Image:
    """Shadow derived from actual product alpha mask, offset and blurred."""
    shifted = Image.new("L", (canvas_w, canvas_h), 0)
    src = product_mask.convert("L")
    if src.size != (canvas_w, canvas_h):
        src = src.resize((canvas_w, canvas_h), Image.LANCZOS)
    # apply opacity
    src_scaled = src.point(lambda v: int(v * opacity))
    # paste with offset (shadow falls offset_y below product)
    px = offset_x
    py = offset_y
    # crop to canvas bounds
    src_crop = src_scaled.crop((
        max(0, -px), max(0, -py),
        canvas_w - min(0, px), canvas_h - min(0, py),
    ))
    paste_x = max(0, px)
    paste_y = max(0, py)
    paste_w = min(src_crop.width, canvas_w - paste_x)
    paste_h = min(src_crop.height, canvas_h - paste_y)
    if paste_w > 0 and paste_h > 0:
        shifted.paste(src_crop.crop((0, 0, paste_w, paste_h)), (paste_x, paste_y))
    if blur_radius > 0:
        shifted = shifted.filter(ImageFilter.GaussianBlur(radius=blur_radius))
    return shifted.convert("L")


def _check_shadow_overlap_with_product(
    shadow: Image.Image,
    product_mask: Image.Image,
) -> float:
    """Fraction of shadow pixels that overlap with product area (0~1)."""
    try:
        import numpy as np
        sh = np.array(shadow.convert("L"), dtype=float) / 255.0
        pm = np.array(product_mask.convert("L"), dtype=bool)
        overlap = float(sh[pm].sum())
        total = float(sh.sum())
        return round(overlap / max(total, 1.0), 4)
    except Exception:
        return 0.0


def _check_heavy_shadow(opacity: float, blur_radius: int, canvas_h: int) -> bool:
    """True if shadow parameters are suspiciously heavy."""
    max_blur = int(_SHADOW_MAX_BLUR * canvas_h)
    return opacity > _SHADOW_MAX_OPACITY or blur_radius > max_blur


# ── composite helper ──────────────────────────────────────────────────────────

def apply_shadow_to_background(
    background: Image.Image,
    shadow_mask: Image.Image,
    shadow_color: tuple[int, int, int] = (0, 0, 0),
) -> Image.Image:
    """Blend shadow layer onto background. Returns new image; product pixels untouched."""
    bg_rgba = background.convert("RGBA")
    sh = shadow_mask.convert("L")
    shadow_layer = Image.new("RGBA", bg_rgba.size, shadow_color + (0,))
    # Build RGBA shadow layer
    for y in range(bg_rgba.height):
        for x in range(bg_rgba.width):
            a = sh.getpixel((x, y))
            shadow_layer.putpixel((x, y), shadow_color + (a,))
    result = Image.alpha_composite(bg_rgba, shadow_layer)
    return result.convert("RGB")


# ── public entry point ────────────────────────────────────────────────────────

def generate_shadow_candidates(
    background: Image.Image,
    product_bbox: dict,
    product_mask: Image.Image | None = None,
    allow_shadow: bool = True,
) -> list[BackgroundCandidate]:
    """Generate shadow candidates for Stage 19D.

    Returns list of BackgroundCandidate instances including a no-shadow candidate.
    Product pixels are never touched (shadow is a separate layer).
    """
    canvas_w, canvas_h = background.size

    offset_y = max(1, int(_SHADOW_OFFSET * canvas_h))
    max_blur  = max(2, int(_SHADOW_MAX_BLUR * canvas_h))
    opacity   = _SHADOW_MAX_OPACITY

    candidates: list[BackgroundCandidate] = []

    # Always provide no-shadow candidate
    no_shadow_bg = background.convert("RGB")
    c_none = BackgroundCandidate(
        candidate_id="shadow_none",
        provider="local",
        method="no_shadow",
        image=no_shadow_bg,
        score=65.0,
        accepted=False,
        shadow_applied=False,
        shadow_opacity=0.0,
        shadow_naturalness_score=65.0,
        extras={"shadowDirection": "none"},
    )
    candidates.append(c_none)

    if not allow_shadow or not _SHADOW_ENABLED:
        return candidates

    strategies: list[tuple[str, callable]] = [
        ("contact_shadow",  lambda: _contact_shadow(
            canvas_w, canvas_h, product_bbox, opacity, max_blur // 2, offset_y)),
        ("soft_ellipse",    lambda: _soft_ellipse_shadow(
            canvas_w, canvas_h, product_bbox, opacity, max_blur, offset_y)),
    ]
    if product_mask is not None:
        strategies.append(("alpha_projected", lambda: _alpha_projected_shadow(
            canvas_w, canvas_h, product_mask, opacity, max_blur,
            offset_x=offset_y // 2, offset_y=offset_y,
        )))

    for method, fn in strategies:
        t0 = time.time()
        try:
            shadow_layer = fn()
        except Exception as exc:
            candidates.append(BackgroundCandidate(
                candidate_id=f"shadow_{method}",
                provider="local",
                method=method,
                accepted=False,
                rejection_reasons=[f"shadow_error:{exc}"],
                elapsed_ms=int((time.time() - t0) * 1000),
            ))
            continue

        heavy = _check_heavy_shadow(opacity, max_blur, canvas_h)
        overlap = 0.0
        if product_mask is not None:
            overlap = _check_shadow_overlap_with_product(shadow_layer, product_mask)

        # reject excessively heavy or product-overlapping shadow
        reasons: list[str] = []
        if heavy:
            reasons.append("heavy_shadow_risk")
        if overlap > 0.4:
            reasons.append(f"shadow_overlaps_product:{overlap:.2f}")

        score = 78.0 - overlap * 30.0
        if heavy:
            score -= 15.0

        # compose background + shadow
        bg_with_shadow = apply_shadow_to_background(background, shadow_layer)

        c = BackgroundCandidate(
            candidate_id=f"shadow_{method}",
            provider="local",
            method=method,
            image=bg_with_shadow,
            score=round(max(0.0, score), 2),
            accepted=False,
            rejection_reasons=reasons,
            shadow_applied=True,
            shadow_opacity=round(opacity, 3),
            shadow_naturalness_score=round(max(0.0, score), 2),
            floating_object_risk=round(max(0.0, 0.3 - score / 250.0), 3),
            extras={
                "shadowDirection": "bottom",
                "shadowBlurRadius": max_blur,
                "shadowOverlapWithProduct": overlap,
                "heavyShadowRisk": heavy,
            },
            elapsed_ms=int((time.time() - t0) * 1000),
        )
        candidates.append(c)

    return candidates
