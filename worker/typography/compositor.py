"""Stage 20D: Typography-aware layer compositor.

Builds final canvas from:
  - Layout template slots (role → position/size)
  - Classified + deduplicated layers
  - Respects layer z-order from template
  - No duplicate text (dedupSkip=True layers are not rendered)
  - No non-uniform scale on product layers
"""
from __future__ import annotations
from PIL import Image, ImageFilter

from .schemas import LayoutSlot


def _load_layer_image(layer: dict) -> Image.Image | None:
    """Load layer image from previewPath or _layer_obj composite."""
    pp = layer.get("previewPath")
    if pp:
        try:
            img = Image.open(pp)
            img.load()
            return img.convert("RGBA")
        except Exception:
            pass
    lo = layer.get("_layer_obj")
    if lo is not None:
        try:
            img = lo.composite()
            if img and img.width > 0 and img.height > 0:
                return img.convert("RGBA")
        except Exception:
            pass
    return None


def _fit_layer(layer_img: Image.Image, slot_w: int, slot_h: int,
               mode: str = "contain") -> Image.Image:
    """Fit layer into slot. 'cover' fills slot; 'contain' preserves aspect ratio."""
    src_w, src_h = layer_img.size
    if src_w <= 0 or src_h <= 0:
        return Image.new("RGBA", (slot_w, slot_h), (0, 0, 0, 0))

    if mode == "cover":
        scale = max(slot_w / src_w, slot_h / src_h)
    else:
        scale = min(slot_w / src_w, slot_h / src_h)

    new_w = max(1, int(src_w * scale))
    new_h = max(1, int(src_h * scale))
    resized = layer_img.resize((new_w, new_h), Image.LANCZOS)

    if mode == "cover":
        left = max(0, (new_w - slot_w) // 2)
        top = max(0, (new_h - slot_h) // 2)
        return resized.crop((left, top, left + slot_w, top + slot_h))

    canvas = Image.new("RGBA", (slot_w, slot_h), (0, 0, 0, 0))
    x = max(0, (slot_w - new_w) // 2)
    y = max(0, (slot_h - new_h) // 2)
    canvas.alpha_composite(resized, (x, y))
    return canvas


def _build_fallback_bg(psd, target_w: int, target_h: int) -> Image.Image | None:
    """Try composite() for fallback blur background."""
    try:
        composite = psd.composite()
        if composite:
            bg = composite.convert("RGBA").resize((target_w, target_h), Image.LANCZOS)
            return bg.filter(ImageFilter.GaussianBlur(radius=25))
    except Exception:
        pass
    return None


def compose(
    classified: list[dict],
    slots: list[LayoutSlot],
    target_w: int,
    target_h: int,
    fallback_bg: Image.Image | None = None,
) -> Image.Image:
    """Compose final banner canvas from classified layers + layout slots.

    Rendering rules:
    1. Layers with dedupSkip=True are not rendered.
    2. Background slot is placed first (z=0).
    3. Remaining slots follow template z_order.
    4. Per role, first matching non-deduped layer is used.
    5. Unknown layers are not rendered (prevents composite+individual double-render).
    """
    canvas = Image.new("RGBA", (target_w, target_h), (255, 255, 255, 255))
    slot_map: dict[str, LayoutSlot] = {s.role: s for s in sorted(slots, key=lambda s: s.z_order)}

    # Layer map: role → list of eligible layers (not dedupSkip, not unknown)
    role_to_layers: dict[str, list[dict]] = {}
    for layer in classified:
        if layer.get("dedupSkip"):
            continue
        role = layer.get("role", "unknown")
        role_to_layers.setdefault(role, []).append(layer)

    # Render in z_order
    slots_sorted = sorted(slots, key=lambda s: s.z_order)

    # Background first
    bg_rendered = False
    bg_slots = [s for s in slots_sorted if s.role == "background"]
    if bg_slots:
        s = bg_slots[0]
        bg_layers = role_to_layers.get("background", [])
        if bg_layers:
            img = _load_layer_image(bg_layers[0])
            if img:
                fitted = _fit_layer(img, s.w, s.h, mode="cover")
                canvas.alpha_composite(fitted, (s.x, s.y))
                bg_rendered = True
        if not bg_rendered and fallback_bg:
            canvas.alpha_composite(fallback_bg, (0, 0))
            bg_rendered = True
    elif fallback_bg:
        canvas.alpha_composite(fallback_bg, (0, 0))

    # Rest of slots in z_order
    for s in slots_sorted:
        if s.role == "background":
            continue
        layers_for_role = role_to_layers.get(s.role, [])
        if not layers_for_role:
            continue
        layer = layers_for_role[0]
        img = _load_layer_image(layer)
        if img is None:
            continue
        fitted = _fit_layer(img, s.w, s.h, mode=s.mode)
        # Clip to canvas bounds before pasting
        paste_x = max(0, s.x)
        paste_y = max(0, s.y)
        if paste_x >= target_w or paste_y >= target_h:
            continue
        canvas.alpha_composite(fitted, (paste_x, paste_y))

    return canvas


def save_result(canvas: Image.Image, out_path: str, output_format: str = "jpg") -> None:
    """Save composed canvas to file."""
    if output_format in ("jpg", "jpeg"):
        canvas.convert("RGB").save(out_path, format="JPEG", quality=92)
    else:
        canvas.save(out_path, format="PNG")
