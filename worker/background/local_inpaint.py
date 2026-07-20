"""Stage 19A — Local Background Repair.

Generates multiple inpaint candidates for small removal areas
without external API calls.

Strategy order:
  1. telea      — OpenCV INPAINT_TELEA (or skimage biharmonic fallback)
  2. ns         — OpenCV INPAINT_NS (or skimage biharmonic fallback)
  3. gradient   — PIL-based gradient fill from surrounding edges
  4. edge_color — average of surrounding border pixels
"""
from __future__ import annotations

import io
import time
import math
from PIL import Image, ImageFilter, ImageStat, ImageDraw

from .schemas import BackgroundCandidate


# ── local inpaint trigger conditions ──────────────────────────────────────────
LOCAL_MAX_AREA_RATIO: float = 0.025    # skip local if area > 2.5% of canvas
LOCAL_PROMOTE_AREA_RATIO: float = 0.10  # always promote to external if > 10%


def should_use_local(removal_mask_area_ratio: float) -> bool:
    return removal_mask_area_ratio <= LOCAL_MAX_AREA_RATIO


def should_promote_to_external(removal_mask_area_ratio: float) -> bool:
    return removal_mask_area_ratio > LOCAL_MAX_AREA_RATIO


# ── cv2 optional ──────────────────────────────────────────────────────────────
try:
    import cv2 as _cv2
    _CV2_AVAILABLE = True
except ImportError:
    _cv2 = None
    _CV2_AVAILABLE = False


def _pil_to_cv2_rgb(img: Image.Image):
    """PIL RGB → numpy uint8 BGR (cv2 convention)."""
    import numpy as np
    arr = np.array(img.convert("RGB"), dtype=np.uint8)
    return arr[:, :, ::-1]  # RGB → BGR


def _pil_mask_to_cv2(mask: Image.Image):
    """PIL L-mode → numpy uint8."""
    import numpy as np
    return np.array(mask.convert("L"), dtype=np.uint8)


def _cv2_to_pil(arr) -> Image.Image:
    """numpy BGR → PIL RGB."""
    return Image.fromarray(arr[:, :, ::-1])


# ── skimage fallback ──────────────────────────────────────────────────────────

def _skimage_inpaint(img: Image.Image, mask: Image.Image) -> Image.Image:
    """Biharmonic inpaint using scikit-image (slow but dependency-free of cv2)."""
    try:
        import numpy as np
        from skimage.restoration import inpaint_biharmonic

        rgb = np.array(img.convert("RGB"), dtype=np.float64) / 255.0
        mk = np.array(mask.convert("L"), dtype=bool)
        result = inpaint_biharmonic(rgb, mk, channel_axis=-1)
        result = np.clip(result * 255, 0, 255).astype(np.uint8)
        return Image.fromarray(result, mode="RGB")
    except Exception as e:
        return img.copy()


# ── strategy implementations ─────────────────────────────────────────────────

def _inpaint_telea(img: Image.Image, mask: Image.Image, radius: int = 5) -> Image.Image:
    """OpenCV Telea inpaint. Falls back to skimage if cv2 unavailable."""
    if _CV2_AVAILABLE:
        try:
            bgr = _pil_to_cv2_rgb(img)
            mk = _pil_mask_to_cv2(mask)
            result = _cv2.inpaint(bgr, mk, radius, _cv2.INPAINT_TELEA)
            return _cv2_to_pil(result)
        except Exception:
            pass
    return _skimage_inpaint(img, mask)


def _inpaint_ns(img: Image.Image, mask: Image.Image, radius: int = 5) -> Image.Image:
    """OpenCV Navier-Stokes inpaint. Falls back to skimage if cv2 unavailable."""
    if _CV2_AVAILABLE:
        try:
            bgr = _pil_to_cv2_rgb(img)
            mk = _pil_mask_to_cv2(mask)
            result = _cv2.inpaint(bgr, mk, radius, _cv2.INPAINT_NS)
            return _cv2_to_pil(result)
        except Exception:
            pass
    return _skimage_inpaint(img, mask)


def _inpaint_gradient(img: Image.Image, mask: Image.Image) -> Image.Image:
    """Fill masked region with a smooth gradient derived from surrounding edges.

    Algorithm:
    1. Compute mean colors at each edge of the mask's bounding box
    2. Interpolate across the fill region
    3. Blend with original at mask boundary
    """
    img_rgb = img.convert("RGB")
    mk = mask.convert("L")
    w, h = img_rgb.size

    # bounding box of mask
    bbox = mk.getbbox()
    if bbox is None:
        return img_rgb

    x0, y0, x1, y1 = bbox
    x0, y0 = max(0, x0 - 5), max(0, y0 - 5)
    x1, y1 = min(w, x1 + 5), min(h, y1 + 5)

    # sample surrounding border colors
    def _sample_strip(strip: Image.Image) -> tuple[int, int, int]:
        stat = ImageStat.Stat(strip)
        return tuple(int(round(m)) for m in stat.mean[:3])

    left_strip   = img_rgb.crop((max(0, x0 - 8), y0, x0 + 1, y1))
    right_strip  = img_rgb.crop((x1 - 1, y0, min(w, x1 + 8), y1))
    top_strip    = img_rgb.crop((x0, max(0, y0 - 8), x1, y0 + 1))
    bottom_strip = img_rgb.crop((x0, y1 - 1, x1, min(h, y1 + 8)))

    c_left   = _sample_strip(left_strip)   if left_strip.width > 0   else (128, 128, 128)
    c_right  = _sample_strip(right_strip)  if right_strip.width > 0  else (128, 128, 128)
    c_top    = _sample_strip(top_strip)    if top_strip.height > 0   else (128, 128, 128)
    c_bottom = _sample_strip(bottom_strip) if bottom_strip.height > 0 else (128, 128, 128)

    region_w = x1 - x0
    region_h = y1 - y0
    if region_w <= 0 or region_h <= 0:
        return img_rgb

    # build gradient fill for the bounding region
    gradient = Image.new("RGB", (region_w, region_h))
    for py in range(region_h):
        ty = py / max(region_h - 1, 1)
        for px in range(region_w):
            tx = px / max(region_w - 1, 1)
            r = int(c_left[0] * (1 - tx) * (1 - ty) + c_right[0] * tx * (1 - ty) +
                    c_top[0]  * (1 - tx) * ty        + c_bottom[0] * tx * ty)
            g = int(c_left[1] * (1 - tx) * (1 - ty) + c_right[1] * tx * (1 - ty) +
                    c_top[1]  * (1 - tx) * ty        + c_bottom[1] * tx * ty)
            b = int(c_left[2] * (1 - tx) * (1 - ty) + c_right[2] * tx * (1 - ty) +
                    c_top[2]  * (1 - tx) * ty        + c_bottom[2] * tx * ty)
            gradient.putpixel((px, py), (
                max(0, min(255, r)),
                max(0, min(255, g)),
                max(0, min(255, b)),
            ))

    # composite gradient over original using mask
    result = img_rgb.copy()
    region_mask = mk.crop((x0, y0, x1, y1))
    result.paste(gradient, (x0, y0), region_mask)
    return result


def _inpaint_edge_color(img: Image.Image, mask: Image.Image) -> Image.Image:
    """Fill masked region with the average color of surrounding border pixels."""
    img_rgb = img.convert("RGB")
    mk = mask.convert("L")
    w, h = img_rgb.size

    bbox = mk.getbbox()
    if bbox is None:
        return img_rgb

    x0, y0, x1, y1 = bbox
    # dilate bbox by ~8px to capture surround
    bw = 8
    sx0, sy0 = max(0, x0 - bw), max(0, y0 - bw)
    sx1, sy1 = min(w, x1 + bw), min(h, y1 + bw)

    surround = img_rgb.crop((sx0, sy0, sx1, sy1))
    try:
        stat = ImageStat.Stat(surround)
        fill_color = tuple(int(round(m)) for m in stat.mean[:3])
    except Exception:
        fill_color = (128, 128, 128)

    fill_img = Image.new("RGB", img_rgb.size, fill_color)
    result = img_rgb.copy()
    result.paste(fill_img, (0, 0), mk)
    return result


# ── seam / blur / repetition estimators ─────────────────────────────────────

def _compute_boundary_color_delta(
    result: Image.Image,
    mask: Image.Image,
    border_px: int = 3,
) -> float:
    """Estimate color discontinuity at mask boundary (0=seamless, 1=very visible)."""
    try:
        import numpy as np
        arr = np.array(result.convert("RGB"), dtype=float)
        mk = np.array(mask.convert("L"), dtype=bool)

        from PIL import ImageFilter
        dilated = mask.filter(ImageFilter.MaxFilter(size=border_px * 2 + 1))
        eroded  = mask.filter(ImageFilter.MinFilter(size=border_px * 2 + 1))
        boundary = np.array(dilated) > np.array(eroded)

        inner  = mk & boundary
        outer  = (~mk) & boundary

        if inner.sum() == 0 or outer.sum() == 0:
            return 0.0

        mean_inner = arr[inner].mean(axis=0)
        mean_outer = arr[outer].mean(axis=0)
        delta = float(np.abs(mean_inner - mean_outer).mean()) / 255.0
        return round(min(1.0, delta), 4)
    except Exception:
        return 0.0


def _compute_blur_risk(result: Image.Image, mask: Image.Image) -> float:
    """Estimate if the inpainted region looks blurry vs surrounding (0~1)."""
    try:
        import numpy as np
        arr = np.array(result.convert("L"), dtype=float)
        mk = np.array(mask.convert("L"), dtype=bool)

        def _laplacian_var(region):
            if len(region) == 0:
                return 0.0
            # simple variance as sharpness proxy
            return float(np.var(region))

        inside_var  = _laplacian_var(arr[mk])
        outside_var = _laplacian_var(arr[~mk])
        if outside_var == 0:
            return 0.0
        ratio = inside_var / (outside_var + 1e-9)
        # ratio < 0.3 means inpainted region is much blurrier
        blur_risk = max(0.0, 1.0 - ratio * 2.0)
        return round(min(1.0, blur_risk), 4)
    except Exception:
        return 0.0


def _compute_repetition_risk(result: Image.Image, mask: Image.Image) -> float:
    """Estimate visible repetition/tiling inside mask region (0~1)."""
    try:
        import numpy as np
        arr = np.array(result.convert("L"), dtype=float)
        mk = np.array(mask.convert("L"), dtype=bool)
        region = arr[mk]
        if len(region) < 4:
            return 0.0
        # simple: low variance inside → likely solid fill (OK)
        # high periodicity → hard to detect simply, use std/mean ratio
        std = float(np.std(region))
        mean = float(np.mean(region))
        if mean < 1e-6:
            return 0.0
        cv = std / (mean + 1e-9)
        # very low cv can indicate repetition or solid fill
        repetition_risk = max(0.0, 0.4 - cv) * 2.5
        return round(min(1.0, repetition_risk), 4)
    except Exception:
        return 0.0


def _score_candidate(
    color_delta: float,
    blur_risk: float,
    repetition_risk: float,
    mask_area_ratio: float,
) -> float:
    """Compute candidate quality score 0–100."""
    seam_score = max(0.0, 100.0 - color_delta * 200.0)
    blur_score = max(0.0, 100.0 - blur_risk * 150.0)
    rep_score = max(0.0, 100.0 - repetition_risk * 100.0)
    # penalty for large mask area (harder to inpaint well locally)
    area_penalty = min(30.0, mask_area_ratio * 300.0)
    score = (seam_score * 0.45 + blur_score * 0.35 + rep_score * 0.20) - area_penalty
    return round(max(0.0, min(100.0, score)), 2)


# ── public entry point ────────────────────────────────────────────────────────

def generate_local_candidates(
    source_image: Image.Image,
    removal_mask: Image.Image,
    max_candidates: int = 4,
    inpaint_radius: int = 5,
) -> list[BackgroundCandidate]:
    """Generate local inpaint candidates for Stage 19A.

    Returns list of BackgroundCandidate (image attached as .image).
    Empty list if mask is empty or area exceeds threshold.
    """
    mask = removal_mask.convert("L")
    w, h = source_image.size

    # empty mask — nothing to inpaint
    total_px = w * h
    white_px = sum(1 for v in mask.getdata() if v > 127)
    if white_px == 0:
        return []

    area_ratio = white_px / max(total_px, 1)
    if area_ratio > LOCAL_PROMOTE_AREA_RATIO:
        # area too large for any local method — caller promotes to external
        return []

    # threshold mask to binary
    binary_mask = mask.point(lambda v: 255 if v > 127 else 0)
    img_rgb = source_image.convert("RGB")

    strategies = [
        ("telea",      lambda: _inpaint_telea(img_rgb, binary_mask, inpaint_radius)),
        ("ns",         lambda: _inpaint_ns(img_rgb, binary_mask, inpaint_radius)),
        ("gradient",   lambda: _inpaint_gradient(img_rgb, binary_mask)),
        ("edge_color", lambda: _inpaint_edge_color(img_rgb, binary_mask)),
    ]

    candidates: list[BackgroundCandidate] = []
    for idx, (method, fn) in enumerate(strategies):
        if len(candidates) >= max_candidates:
            break
        t0 = time.time()
        try:
            result_img = fn()
            elapsed = int((time.time() - t0) * 1000)
        except Exception as exc:
            c = BackgroundCandidate(
                candidate_id=f"local_{method}",
                provider="local",
                method=method,
                accepted=False,
                rejection_reasons=[f"strategy_error:{exc}"],
                elapsed_ms=int((time.time() - t0) * 1000),
            )
            candidates.append(c)
            continue

        color_delta = _compute_boundary_color_delta(result_img, binary_mask)
        blur_risk = _compute_blur_risk(result_img, binary_mask)
        rep_risk = _compute_repetition_risk(result_img, binary_mask)
        score = _score_candidate(color_delta, blur_risk, rep_risk, area_ratio)

        c = BackgroundCandidate(
            candidate_id=f"local_{method}",
            provider="local",
            method=method,
            image=result_img,
            score=score,
            mask_area_ratio=round(area_ratio, 4),
            boundary_color_delta=color_delta,
            blur_band_risk=blur_risk,
            repetition_risk=rep_risk,
            seam_risk=color_delta,
            seam_score=round(max(0.0, 100.0 - color_delta * 200.0), 2),
            naturalness_score=round(score, 2),
            accepted=False,  # acceptance set by QualityGate
            elapsed_ms=elapsed,
        )
        candidates.append(c)

    return candidates
