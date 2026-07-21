"""Stage 20C: Layout templates for all spec types.

Supports: horizontal (ratio>=1.3), square (0.85~1.15), vertical (0.4~0.85),
ultra-vertical (<0.4), ultra-wide (>1.8).
"""
from __future__ import annotations
from .schemas import LayoutSlot


def _spec_type(target_w: int, target_h: int) -> str:
    ratio = target_w / max(target_h, 1)
    if ratio > 2.0:
        return "ultrawide"
    elif ratio > 1.3:
        return "horizontal"
    elif ratio >= 0.85:
        return "square"
    elif ratio >= 0.4:
        return "vertical"
    else:
        return "ultravertical"


def _slots(**kwargs: dict) -> list[LayoutSlot]:
    result = []
    z = 0
    for role, pos in kwargs.items():
        result.append(LayoutSlot(
            role=role,
            x=pos["x"], y=pos["y"], w=pos["w"], h=pos["h"],
            mode=pos.get("mode", "contain"),
            safe=pos.get("safe", True),
            z_order=pos.get("z", z),
        ))
        z += 1
    return result


# ── 1250×560 (exact) ─────────────────────────────────────────────────────────

def _template_1250x560_image_left() -> tuple[str, list[LayoutSlot]]:
    return "layout_1250x560_image_left", _slots(
        background=dict(x=0,   y=0,   w=1250, h=560, mode="cover", z=0),
        overlay   =dict(x=0,   y=0,   w=1250, h=560, mode="cover", z=1),
        main_image=dict(x=260, y=80,  w=360,  h=420, mode="contain", z=2),
        logo      =dict(x=260, y=35,  w=160,  h=60,  mode="contain", z=3),
        title     =dict(x=650, y=100, w=500,  h=100, mode="contain", z=4),
        body_text =dict(x=650, y=215, w=500,  h=80,  mode="contain", z=5),
        cta       =dict(x=650, y=320, w=220,  h=65,  mode="contain", z=6),
        badge     =dict(x=650, y=415, w=200,  h=65,  mode="contain", z=7),
        decoration=dict(x=950, y=420, w=200,  h=100, mode="contain", z=8),
        brand_name=dict(x=650, y=50,  w=300,  h=40,  mode="contain", z=3),
        legal_text=dict(x=260, y=510, w=960,  h=40,  mode="contain", z=9),
    )


def _template_1250x560_image_right() -> tuple[str, list[LayoutSlot]]:
    return "layout_1250x560_image_right", _slots(
        background=dict(x=0,   y=0,   w=1250, h=560, mode="cover", z=0),
        overlay   =dict(x=0,   y=0,   w=1250, h=560, mode="cover", z=1),
        logo      =dict(x=260, y=35,  w=160,  h=60,  mode="contain", z=3),
        title     =dict(x=260, y=100, w=500,  h=100, mode="contain", z=4),
        body_text =dict(x=260, y=215, w=500,  h=80,  mode="contain", z=5),
        cta       =dict(x=260, y=320, w=220,  h=65,  mode="contain", z=6),
        badge     =dict(x=260, y=415, w=200,  h=65,  mode="contain", z=7),
        main_image=dict(x=780, y=80,  w=360,  h=420, mode="contain", z=2),
        brand_name=dict(x=260, y=50,  w=300,  h=40,  mode="contain", z=3),
        legal_text=dict(x=260, y=510, w=960,  h=40,  mode="contain", z=9),
    )


# ── Horizontal (ratio 1.3–2.0) ────────────────────────────────────────────────

def _template_horizontal_image_left(w: int, h: int) -> tuple[str, list[LayoutSlot]]:
    lm = int(w * 0.18)
    img_w = int(w * 0.40)
    txt_x = lm + img_w + int(w * 0.04)
    txt_w = w - txt_x - int(w * 0.03)
    img_y = int(h * 0.10)
    img_h = int(h * 0.80)
    return "horizontal_image_left", _slots(
        background=dict(x=0,   y=0,   w=w, h=h, mode="cover", z=0),
        overlay   =dict(x=0,   y=0,   w=w, h=h, mode="cover", z=1),
        main_image=dict(x=lm,  y=img_y, w=img_w, h=img_h, mode="contain", z=2),
        logo      =dict(x=txt_x, y=int(h*0.06), w=int(txt_w*0.45), h=int(h*0.12), mode="contain", z=3),
        title     =dict(x=txt_x, y=int(h*0.25), w=txt_w, h=int(h*0.18), mode="contain", z=4),
        body_text =dict(x=txt_x, y=int(h*0.47), w=txt_w, h=int(h*0.16), mode="contain", z=5),
        cta       =dict(x=txt_x, y=int(h*0.67), w=int(txt_w*0.55), h=int(h*0.14), mode="contain", z=6),
        badge     =dict(x=txt_x, y=int(h*0.83), w=int(txt_w*0.45), h=int(h*0.12), mode="contain", z=7),
        legal_text=dict(x=lm, y=int(h*0.92), w=w-lm*2, h=int(h*0.07), mode="contain", z=9),
    )


def _template_horizontal_image_right(w: int, h: int) -> tuple[str, list[LayoutSlot]]:
    lm = int(w * 0.18)
    txt_w = int(w * 0.36)
    img_x = lm + txt_w + int(w * 0.04)
    img_w = w - img_x - int(w * 0.03)
    img_y = int(h * 0.10)
    img_h = int(h * 0.80)
    return "horizontal_image_right", _slots(
        background=dict(x=0,   y=0, w=w, h=h, mode="cover", z=0),
        overlay   =dict(x=0,   y=0, w=w, h=h, mode="cover", z=1),
        logo      =dict(x=lm,  y=int(h*0.06), w=int(txt_w*0.45), h=int(h*0.12), mode="contain", z=3),
        title     =dict(x=lm,  y=int(h*0.25), w=txt_w, h=int(h*0.18), mode="contain", z=4),
        body_text =dict(x=lm,  y=int(h*0.47), w=txt_w, h=int(h*0.16), mode="contain", z=5),
        cta       =dict(x=lm,  y=int(h*0.67), w=int(txt_w*0.55), h=int(h*0.14), mode="contain", z=6),
        badge     =dict(x=lm,  y=int(h*0.83), w=int(txt_w*0.45), h=int(h*0.12), mode="contain", z=7),
        main_image=dict(x=img_x, y=img_y, w=img_w, h=img_h, mode="contain", z=2),
        legal_text=dict(x=lm, y=int(h*0.92), w=w-lm*2, h=int(h*0.07), mode="contain", z=9),
    )


# ── Square (ratio 0.85–1.15) ──────────────────────────────────────────────────

def _template_square_image_top(w: int, h: int) -> tuple[str, list[LayoutSlot]]:
    img_h = int(h * 0.55)
    txt_y = img_h + int(h * 0.04)
    txt_h = h - txt_y - int(h * 0.04)
    m = int(w * 0.08)
    return "square_image_top", _slots(
        background=dict(x=0, y=0, w=w, h=h, mode="cover", z=0),
        main_image=dict(x=m, y=int(h*0.05), w=w-m*2, h=img_h-int(h*0.05), mode="contain", z=2),
        logo      =dict(x=m, y=txt_y, w=int(w*0.25), h=int(h*0.07), mode="contain", z=3),
        title     =dict(x=m, y=txt_y+int(h*0.09), w=w-m*2, h=int(h*0.12), mode="contain", z=4),
        body_text =dict(x=m, y=txt_y+int(h*0.23), w=w-m*2, h=int(h*0.09), mode="contain", z=5),
        cta       =dict(x=m, y=txt_y+int(h*0.34), w=int(w*0.40), h=int(h*0.09), mode="contain", z=6),
        badge     =dict(x=w-m-int(w*0.28), y=int(h*0.05), w=int(w*0.28), h=int(h*0.10), mode="contain", z=7),
        legal_text=dict(x=m, y=h-int(h*0.06), w=w-m*2, h=int(h*0.05), mode="contain", z=9),
    )


def _template_square_image_bottom(w: int, h: int) -> tuple[str, list[LayoutSlot]]:
    img_y = int(h * 0.45)
    img_h = h - img_y - int(h * 0.04)
    m = int(w * 0.08)
    return "square_image_bottom", _slots(
        background=dict(x=0, y=0, w=w, h=h, mode="cover", z=0),
        logo      =dict(x=m, y=int(h*0.04), w=int(w*0.25), h=int(h*0.08), mode="contain", z=3),
        title     =dict(x=m, y=int(h*0.15), w=w-m*2, h=int(h*0.14), mode="contain", z=4),
        body_text =dict(x=m, y=int(h*0.31), w=w-m*2, h=int(h*0.09), mode="contain", z=5),
        cta       =dict(x=m, y=int(h*0.42), w=int(w*0.40), h=int(h*0.08), mode="contain", z=6),
        main_image=dict(x=m, y=img_y, w=w-m*2, h=img_h, mode="contain", z=2),
        badge     =dict(x=w-m-int(w*0.28), y=int(h*0.04), w=int(w*0.28), h=int(h*0.10), mode="contain", z=7),
        legal_text=dict(x=m, y=h-int(h*0.06), w=w-m*2, h=int(h*0.05), mode="contain", z=9),
    )


def _template_square_full_bleed(w: int, h: int) -> tuple[str, list[LayoutSlot]]:
    m = int(w * 0.06)
    return "square_full_bleed", _slots(
        background=dict(x=0, y=0, w=w, h=h, mode="cover", z=0),
        main_image=dict(x=0, y=0, w=w, h=h, mode="cover", z=1),
        overlay   =dict(x=0, y=0, w=w, h=h, mode="cover", z=2),
        logo      =dict(x=m, y=m, w=int(w*0.25), h=int(h*0.08), mode="contain", z=3),
        title     =dict(x=m, y=int(h*0.55), w=w-m*2, h=int(h*0.16), mode="contain", z=4),
        body_text =dict(x=m, y=int(h*0.73), w=w-m*2, h=int(h*0.08), mode="contain", z=5),
        cta       =dict(x=m, y=int(h*0.83), w=int(w*0.40), h=int(h*0.09), mode="contain", z=6),
        badge     =dict(x=w-m-int(w*0.28), y=m, w=int(w*0.28), h=int(h*0.10), mode="contain", z=7),
        legal_text=dict(x=m, y=h-int(h*0.06), w=w-m*2, h=int(h*0.05), mode="contain", z=9),
    )


# ── Vertical (ratio 0.4–0.85) ─────────────────────────────────────────────────

def _template_vertical_standard(w: int, h: int) -> tuple[str, list[LayoutSlot]]:
    m = int(w * 0.07)
    img_h = int(h * 0.42)
    return "vertical_standard", _slots(
        background=dict(x=0, y=0, w=w, h=h, mode="cover", z=0),
        logo      =dict(x=m, y=int(h*0.03), w=int(w*0.30), h=int(h*0.06), mode="contain", z=3),
        badge     =dict(x=w-m-int(w*0.30), y=int(h*0.03), w=int(w*0.30), h=int(h*0.08), mode="contain", z=7),
        main_image=dict(x=m, y=int(h*0.12), w=w-m*2, h=img_h, mode="contain", z=2),
        title     =dict(x=m, y=int(h*0.58), w=w-m*2, h=int(h*0.11), mode="contain", z=4),
        body_text =dict(x=m, y=int(h*0.71), w=w-m*2, h=int(h*0.08), mode="contain", z=5),
        cta       =dict(x=m, y=int(h*0.81), w=int(w*0.55), h=int(h*0.09), mode="contain", z=6),
        legal_text=dict(x=m, y=h-int(h*0.05), w=w-m*2, h=int(h*0.04), mode="contain", z=9),
    )


def _template_vertical_text_top(w: int, h: int) -> tuple[str, list[LayoutSlot]]:
    m = int(w * 0.07)
    return "vertical_text_top", _slots(
        background=dict(x=0, y=0, w=w, h=h, mode="cover", z=0),
        logo      =dict(x=m, y=int(h*0.03), w=int(w*0.30), h=int(h*0.06), mode="contain", z=3),
        title     =dict(x=m, y=int(h*0.12), w=w-m*2, h=int(h*0.12), mode="contain", z=4),
        body_text =dict(x=m, y=int(h*0.26), w=w-m*2, h=int(h*0.08), mode="contain", z=5),
        cta       =dict(x=m, y=int(h*0.36), w=int(w*0.55), h=int(h*0.08), mode="contain", z=6),
        main_image=dict(x=m, y=int(h*0.48), w=w-m*2, h=int(h*0.44), mode="contain", z=2),
        badge     =dict(x=w-m-int(w*0.30), y=int(h*0.03), w=int(w*0.30), h=int(h*0.08), mode="contain", z=7),
        legal_text=dict(x=m, y=h-int(h*0.05), w=w-m*2, h=int(h*0.04), mode="contain", z=9),
    )


# ── Ultra-vertical (ratio < 0.4) ──────────────────────────────────────────────

def _template_ultravert_story(w: int, h: int) -> tuple[str, list[LayoutSlot]]:
    m = int(w * 0.06)
    return "ultravert_story", _slots(
        background=dict(x=0, y=0, w=w, h=h, mode="cover", z=0),
        logo      =dict(x=m, y=int(h*0.02), w=int(w*0.35), h=int(h*0.04), mode="contain", z=3),
        main_image=dict(x=m, y=int(h*0.10), w=w-m*2, h=int(h*0.38), mode="contain", z=2),
        title     =dict(x=m, y=int(h*0.51), w=w-m*2, h=int(h*0.10), mode="contain", z=4),
        body_text =dict(x=m, y=int(h*0.63), w=w-m*2, h=int(h*0.07), mode="contain", z=5),
        cta       =dict(x=m, y=int(h*0.72), w=int(w*0.60), h=int(h*0.07), mode="contain", z=6),
        badge     =dict(x=w-m-int(w*0.32), y=int(h*0.02), w=int(w*0.32), h=int(h*0.07), mode="contain", z=7),
        legal_text=dict(x=m, y=h-int(h*0.05), w=w-m*2, h=int(h*0.04), mode="contain", z=9),
    )


# ── Ultra-wide (ratio > 2.0) ──────────────────────────────────────────────────

def _template_ultrawide_panorama(w: int, h: int) -> tuple[str, list[LayoutSlot]]:
    lm = int(w * 0.05)
    img_w = int(w * 0.35)
    txt_x = lm + img_w + int(w * 0.04)
    txt_w = int(w * 0.28)
    return "ultrawide_panorama", _slots(
        background=dict(x=0,   y=0, w=w, h=h, mode="cover", z=0),
        main_image=dict(x=lm,  y=int(h*0.08), w=img_w, h=int(h*0.84), mode="contain", z=2),
        logo      =dict(x=txt_x, y=int(h*0.08), w=int(txt_w*0.45), h=int(h*0.15), mode="contain", z=3),
        title     =dict(x=txt_x, y=int(h*0.28), w=txt_w, h=int(h*0.22), mode="contain", z=4),
        body_text =dict(x=txt_x, y=int(h*0.54), w=txt_w, h=int(h*0.15), mode="contain", z=5),
        cta       =dict(x=txt_x, y=int(h*0.72), w=int(txt_w*0.55), h=int(h*0.16), mode="contain", z=6),
        badge     =dict(x=w-lm-int(w*0.14), y=int(h*0.08), w=int(w*0.14), h=int(h*0.20), mode="contain", z=7),
        legal_text=dict(x=lm, y=h-int(h*0.08), w=w-lm*2, h=int(h*0.07), mode="contain", z=9),
    )


def _select_image_side(classified: list[dict]) -> str:
    """Determine if main_image is on left or right based on original bbox."""
    main_layers = [l for l in classified if l.get("role") == "main_image"]
    if not main_layers:
        return "left"
    canvas_w = main_layers[0].get("canvasWidth", 1) or 1
    cx = (main_layers[0]["bbox"]["x"] + main_layers[0]["bbox"]["width"] / 2)
    return "left" if cx <= canvas_w / 2 else "right"


def get_template(target_w: int, target_h: int,
                 classified: list[dict]) -> tuple[str, list[LayoutSlot]]:
    """Return (template_name, slots) for the given target spec and classified layers."""
    # Exact 1250×560 match
    if target_w == 1250 and target_h == 560:
        side = _select_image_side(classified)
        if side == "left":
            return _template_1250x560_image_left()
        else:
            return _template_1250x560_image_right()

    spec = _spec_type(target_w, target_h)
    side = _select_image_side(classified)

    if spec == "ultrawide":
        return _template_ultrawide_panorama(target_w, target_h)
    elif spec == "horizontal":
        if side == "left":
            return _template_horizontal_image_left(target_w, target_h)
        else:
            return _template_horizontal_image_right(target_w, target_h)
    elif spec == "square":
        # If main_image occupies top half → image_top; else image_bottom or full_bleed
        main_layers = [l for l in classified if l.get("role") == "main_image"]
        if main_layers:
            cy = (main_layers[0]["bbox"]["y"] + main_layers[0]["bbox"]["height"] / 2)
            ch = main_layers[0].get("canvasHeight", target_h) or target_h
            if cy / ch < 0.5:
                return _template_square_image_top(target_w, target_h)
            else:
                return _template_square_image_bottom(target_w, target_h)
        return _template_square_full_bleed(target_w, target_h)
    elif spec == "vertical":
        # Check if text tends to be at top
        text_layers = [l for l in classified if l.get("role") in ("title", "body_text")]
        if text_layers:
            avg_cy = sum(
                (l["bbox"]["y"] + l["bbox"]["height"] / 2)
                / (l.get("canvasHeight", target_h) or target_h)
                for l in text_layers
            ) / len(text_layers)
            if avg_cy < 0.5:
                return _template_vertical_text_top(target_w, target_h)
        return _template_vertical_standard(target_w, target_h)
    else:  # ultravertical
        return _template_ultravert_story(target_w, target_h)


def slots_as_dict(slots: list[LayoutSlot]) -> dict[str, LayoutSlot]:
    """Convert slot list to role→slot mapping (last slot wins on duplicate role)."""
    return {s.role: s for s in slots}
