"""Render advertisement text using installed system fonts."""
from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFont

_FALLBACK_PRIORITY = [
    "Pretendard",
    "NanumBarunGothic",
    "NanumGothic",
    "NotoSansKR",
    "NotoSans",
    "DejaVuSans",
    "Arial",
]

_FONT_SEARCH_DIRS = [
    "/usr/share/fonts",
    "/usr/local/share/fonts",
    "/app/fonts",
    "/fonts",
]

_FONT_CACHE: dict[str, str | None] = {}


def find_font(family: str) -> str | None:
    """Return a .ttf/.otf path for the given family name, or None."""
    if family in _FONT_CACHE:
        return _FONT_CACHE[family]

    target = family.lower().replace(" ", "").replace("-", "").replace("_", "")

    for search_dir in _FONT_SEARCH_DIRS:
        if not os.path.isdir(search_dir):
            continue
        for root, _, files in os.walk(search_dir):
            for fname in files:
                if not fname.lower().endswith((".ttf", ".otf")):
                    continue
                stem = fname.lower().replace("-", "").replace("_", "").replace(" ", "")
                stem = stem.rsplit(".", 1)[0]
                if target in stem or stem.startswith(target[:6]):
                    path = os.path.join(root, fname)
                    _FONT_CACHE[family] = path
                    return path

    _FONT_CACHE[family] = None
    return None


def choose_font(font_family_guess: str | None) -> tuple[str | None, bool]:
    """Return (font_path_or_None, fallback_used)."""
    if font_family_guess:
        path = find_font(font_family_guess)
        if path:
            return path, False
    for fallback in _FALLBACK_PRIORITY:
        path = find_font(fallback)
        if path:
            return path, True
    return None, True


def render_text_object(
    text: str,
    font_family_guess: str | None,
    font_size: int,
    font_weight: str | None = None,
    bbox: dict | None = None,
    text_color: str | None = None,
    text_align: str | None = None,
    segments: list | None = None,
    job_id: str = "",
    object_id: str = "",
) -> tuple:
    """Render text to a transparent RGBA image at bbox (width × height).

    Tries requested font, falls back through _FALLBACK_PRIORITY.
    Auto-shrinks font size to fit bbox.

    Returns:
        (rgba_img, metrics) — rgba_img is None on failure.
    """
    if bbox is None:
        bbox = {}
    bw = int(bbox.get("width", 0))
    bh = int(bbox.get("height", 0))
    text = str(text or "").strip()
    if bw <= 0 or bh <= 0 or not text:
        return None, {"error": "INVALID_BBOX_OR_EMPTY_TEXT"}

    font_path, fallback_used = choose_font(font_family_guess)
    chosen_font_name = os.path.basename(font_path) if font_path else "builtin-default"

    current_size = max(8, int(font_size))
    font_obj = _load_font(font_path, current_size)

    # Use segment color if provided; else fall back to text_color arg
    fill = _parse_color(
        (segments[0].get("color") if segments else None)
        or (segments[0].get("fillColor") if segments else None)
        or text_color
        or "#FFFFFF"
    )

    # Auto-shrink to fit
    if font_path:
        probe_canvas = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
        probe_draw = ImageDraw.Draw(probe_canvas)
        for trial in range(current_size, 7, -2):
            fo = _load_font(font_path, trial)
            try:
                tb = probe_draw.textbbox((0, 0), text, font=fo)
                if (tb[2] - tb[0]) <= bw and (tb[3] - tb[1]) <= bh:
                    font_obj = fo
                    current_size = trial
                    break
            except Exception:
                break

    canvas = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    try:
        draw.text((0, 0), text, font=font_obj, fill=fill)
    except Exception as exc:
        return None, {"error": f"TEXT_DRAW_FAILED:{exc}"}

    return canvas, {
        "chosenFont": chosen_font_name,
        "fallbackUsed": fallback_used,
        "fontSize": current_size,
        "rendered": True,
    }


def _load_font(path: str | None, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if path:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    try:
        return ImageFont.load_default()
    except Exception:
        return ImageFont.load_default()


def _parse_color(color_str: str | None) -> tuple:
    if color_str and color_str.startswith("#"):
        try:
            r = int(color_str[1:3], 16)
            g = int(color_str[3:5], 16)
            b = int(color_str[5:7], 16)
            return (r, g, b, 255)
        except Exception:
            pass
    return (255, 255, 255, 255)
