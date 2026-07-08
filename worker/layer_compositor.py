"""4차-5: Layer Compositor.
compute_layout() 배치 결과 + 레이어 이미지 → 최종 canvas 합성.
"""
from PIL import Image, ImageFilter


def _fit_layer(layer_img: Image.Image, slot_w: int, slot_h: int, mode: str = "contain") -> Image.Image:
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


def render_layer_image(layer_obj) -> Image.Image | None:
    """psd-tools layer 오브젝트 → RGBA Image."""
    try:
        img = layer_obj.composite()
        if img and img.width > 0 and img.height > 0:
            return img.convert("RGBA")
    except Exception as e:
        print(f"[Compositor] layer render failed: {e}")
    return None


def compose_layers(placements: list, classified: list,
                   target_w: int, target_h: int,
                   fallback_bg: Image.Image | None = None) -> Image.Image:
    """
    배치 정보 + 레이어 목록으로 최종 canvas 합성.

    classified 목록의 _layer_obj 를 사용해 실시간 렌더링.
    previewPath(PNG)가 있으면 그것을 우선 사용 (속도↑).
    fallback_bg: background 레이어 없을 때 사용할 blur 배경 Image.
    """
    canvas = Image.new("RGBA", (target_w, target_h), (255, 255, 255, 255))

    # id → layer dict 맵
    layer_map = {l["id"]: l for l in classified}

    # background 먼저 처리
    bg_placements = [p for p in placements if p["role"] == "background"]
    non_bg = [p for p in placements if p["role"] != "background"]

    if bg_placements:
        p = bg_placements[0]
        layer = layer_map.get(p["layerId"])
        layer_img = _load_layer_image(layer)
        if layer_img:
            fitted = _fit_layer(layer_img, p["w"], p["h"], mode="cover")
            canvas.alpha_composite(fitted, (p["x"], p["y"]))
        elif fallback_bg:
            canvas.alpha_composite(fallback_bg.resize((target_w, target_h), Image.LANCZOS), (0, 0))
    elif fallback_bg:
        bg_resized = fallback_bg.resize((target_w, target_h), Image.LANCZOS)
        bg_blurred = bg_resized.filter(ImageFilter.GaussianBlur(radius=20))
        canvas.alpha_composite(bg_blurred, (0, 0))

    # 나머지 레이어 배치 (원본 순서 유지 — placements 순서가 classify 순서)
    for p in non_bg:
        layer = layer_map.get(p["layerId"])
        layer_img = _load_layer_image(layer)
        if layer_img is None:
            print(f"[Compositor] skip {p['layerId']} role={p['role']} — no image")
            continue
        fitted = _fit_layer(layer_img, p["w"], p["h"], mode=p.get("mode", "contain"))
        canvas.alpha_composite(fitted, (p["x"], p["y"]))

    return canvas


def _load_layer_image(layer: dict | None) -> Image.Image | None:
    """레이어 dict에서 이미지 로드: previewPath 우선, _layer_obj 차선."""
    if layer is None:
        return None
    # previewPath PNG 먼저 시도
    pp = layer.get("previewPath")
    if pp:
        try:
            img = Image.open(pp)
            img.load()
            return img.convert("RGBA")
        except Exception:
            pass
    # _layer_obj에서 composite() 렌더링
    lo = layer.get("_layer_obj")
    if lo is not None:
        return render_layer_image(lo)
    return None
