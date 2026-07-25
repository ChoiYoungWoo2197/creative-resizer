"""Render advertisement shapes (rect / rounded-rect) to RGBA images."""
from __future__ import annotations

from PIL import Image, ImageDraw


def render_shape_object(
    bbox: dict,
    fill_color: str | None,
    opacity: float = 1.0,
    border_radius: int = 0,
    job_id: str = "",
    object_id: str = "",
) -> tuple:
    """Render a filled rectangle to RGBA at bbox dimensions.

    Args:
        bbox:          {x, y, width, height} — only width/height used here.
        fill_color:    CSS hex string, e.g. "#000000".
        opacity:       0.0–1.0; mapped to alpha channel.
        border_radius: corner radius in pixels (0 = sharp corners).

    Returns:
        (rgba_img, metrics) — rgba_img is None on failure.
    """
    bw = int(bbox.get("width", 0))
    bh = int(bbox.get("height", 0))
    if bw <= 0 or bh <= 0:
        return None, {"error": "INVALID_BBOX"}

    rgb = _parse_color(fill_color or "#000000")
    alpha = int(max(0, min(1.0, opacity)) * 255)
    fill_rgba = rgb + (alpha,)

    canvas = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    if border_radius > 0:
        try:
            draw.rounded_rectangle(
                [(0, 0), (bw - 1, bh - 1)],
                radius=border_radius,
                fill=fill_rgba,
            )
        except Exception:
            draw.rectangle([(0, 0), (bw - 1, bh - 1)], fill=fill_rgba)
    else:
        draw.rectangle([(0, 0), (bw - 1, bh - 1)], fill=fill_rgba)

    return canvas, {
        "rendered": True,
        "fillColor": fill_color,
        "opacity": opacity,
        "borderRadius": border_radius,
    }


def _parse_color(color_str: str) -> tuple:
    try:
        if color_str.startswith("#") and len(color_str) >= 7:
            return (int(color_str[1:3], 16), int(color_str[3:5], 16), int(color_str[5:7], 16))
    except Exception:
        pass
    return (0, 0, 0)
