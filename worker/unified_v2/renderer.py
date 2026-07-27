"""Unified Pipeline V2: font-matching text render and final composite.

Composites extracted fg_layers (RGBA) onto the AI-generated scene plate.
Text role layers use font_resolver for best-match system font rendering.
Non-text layers are pasted directly from their extracted RGBA crops.
"""
from __future__ import annotations

from PIL import Image


def composite_on_scene_plate(
    scene_plate: Image.Image,
    fg_layers: list,
    job_id: str = "",
    spec_id: str = "",
) -> Image.Image:
    """Paste all fg_layers onto scene_plate at their layout-assigned bbox positions.

    Args:
        scene_plate: AI-generated background plate (RGB or RGBA) at target size.
        fg_layers:   list[V2FgLayer] — each .bbox holds the target canvas position.
        job_id:      For structured log output.
        spec_id:     For structured log output.

    Returns:
        Final composite as RGB PIL Image at scene_plate.size.
    """
    canvas = scene_plate.convert("RGBA")
    placed = 0
    skipped = 0

    for layer in fg_layers:
        img = getattr(layer, "image", None)
        if img is None:
            skipped += 1
            continue

        bbox = getattr(layer, "bbox", {})
        x = int(bbox.get("x", 0))
        y = int(bbox.get("y", 0))
        w = int(bbox.get("width", img.width))
        h = int(bbox.get("height", img.height))

        if w <= 0 or h <= 0:
            skipped += 1
            continue

        if img.mode != "RGBA":
            img = img.convert("RGBA")

        # Resize to target bbox if dimensions differ
        if img.size != (w, h):
            img = img.resize((w, h), Image.LANCZOS)

        # Use text rendering for text-role layers that have text content
        role = getattr(layer, "role", "")
        text = ""
        detected_obj = getattr(layer, "_detected_obj", None)
        if detected_obj is not None:
            text = getattr(detected_obj, "text_content", "")

        if role in ("title", "headline", "body_text", "cta") and text:
            rendered = _render_text_layer(text, role, w, h, job_id=job_id, object_id=layer.object_id)
            if rendered is not None:
                img = rendered
                print(
                    f"[V2_TEXT_RENDER] jobId={job_id} specId={spec_id}"
                    f" objectId={layer.object_id} role={role!r}"
                    f" text={text[:40]!r}",
                    flush=True,
                )

        canvas.paste(img, (x, y), mask=img.split()[3])
        placed += 1

    print(
        f"[V2_RECOMPOSE] jobId={job_id} specId={spec_id}"
        f" placedLayers={placed} skippedLayers={skipped}",
        flush=True,
    )

    return canvas.convert("RGB")


def _render_text_layer(
    text: str,
    role: str,
    w: int,
    h: int,
    job_id: str = "",
    object_id: str = "",
) -> Image.Image | None:
    """Render text into a transparent RGBA image at (w, h).

    Uses font_resolver for best system font match.
    Returns None if rendering fails (caller falls back to extracted RGBA crop).
    """
    try:
        from PIL import ImageDraw, ImageFont
        from typography.font_resolver import resolve_font

        is_korean = any("가" <= ch <= "힣" for ch in text)
        font_path = resolve_font("", is_korean=is_korean)

        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Font size: heuristic based on bbox height and role
        font_size = max(10, int(h * 0.55))
        if role == "body_text":
            font_size = max(10, int(h * 0.35))
        elif role == "cta":
            font_size = max(10, int(h * 0.45))

        try:
            font = ImageFont.truetype(font_path, size=font_size) if font_path else ImageFont.load_default()
        except Exception:
            font = ImageFont.load_default()

        # Center text in bbox
        bbox_text = draw.textbbox((0, 0), text, font=font)
        tx = (w - (bbox_text[2] - bbox_text[0])) // 2
        ty = (h - (bbox_text[3] - bbox_text[1])) // 2
        draw.text((tx, ty), text, font=font, fill=(255, 255, 255, 255))

        print(
            f"[V2_FONT_MATCH] jobId={job_id} objectId={object_id}"
            f" role={role!r} fontPath={font_path!r} fontSize={font_size}",
            flush=True,
        )
        return img
    except Exception as exc:
        print(
            f"[V2_FONT_MATCH] jobId={job_id} objectId={object_id}"
            f" role={role!r} status=FALLBACK_TO_EXTRACTED error={exc}",
            flush=True,
        )
        return None
