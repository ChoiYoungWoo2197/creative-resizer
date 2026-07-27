"""Contain (letterbox) projection: scale source to fit target without cropping.

Rules:
- Source is never cropped
- Aspect ratio is never distorted
- Letterbox padding is black (0) for RGB, zero (0) for masks
- Same ContainTransform instance must be applied to image and all masks
"""
from __future__ import annotations

from dataclasses import dataclass

from PIL import Image


@dataclass(frozen=True)
class ContainTransform:
    src_width: int
    src_height: int
    target_width: int
    target_height: int
    proj_width: int     # width of the scaled source inside the target canvas
    proj_height: int    # height of the scaled source inside the target canvas
    offset_x: int       # left padding (center-aligned)
    offset_y: int       # top padding (center-aligned)


def compute(src_w: int, src_h: int, target_w: int, target_h: int) -> ContainTransform:
    """Compute contain transform so src fits entirely within target canvas."""
    scale = min(target_w / src_w, target_h / src_h)
    proj_w = max(1, round(src_w * scale))
    proj_h = max(1, round(src_h * scale))
    off_x = (target_w - proj_w) // 2
    off_y = (target_h - proj_h) // 2
    return ContainTransform(src_w, src_h, target_w, target_h, proj_w, proj_h, off_x, off_y)


def apply_rgb(src: Image.Image, tx: ContainTransform) -> Image.Image:
    """Project an RGB(A) source image onto a black target canvas."""
    scaled = src.convert("RGB").resize((tx.proj_width, tx.proj_height), Image.LANCZOS)
    canvas = Image.new("RGB", (tx.target_width, tx.target_height), (0, 0, 0))
    canvas.paste(scaled, (tx.offset_x, tx.offset_y))
    return canvas


def apply_mask(mask: Image.Image, tx: ContainTransform) -> Image.Image:
    """Project an L-mode mask onto a zero-padded target canvas (NEAREST for hard edges)."""
    scaled = mask.convert("L").resize((tx.proj_width, tx.proj_height), Image.NEAREST)
    canvas = Image.new("L", (tx.target_width, tx.target_height), 0)
    canvas.paste(scaled, (tx.offset_x, tx.offset_y))
    return canvas
