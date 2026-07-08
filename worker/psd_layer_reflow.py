from PIL import Image, ImageFilter
from psd_compat import open_psd_safe_with_patch
import os


ROLE_KEYWORDS = {
    "background": ["bg", "background", "배경", "bkg", "back", "backdrop"],
    "logo":       ["logo", "로고", "brand", "브랜드", "기관", "emblem", "ci", "bi"],
    "headline":   ["title", "headline", "main", "제목", "타이틀", "main_title", "maintitle",
                   "헤드라인", "카피", "copy", "maintext", "main_text"],
    "subcopy":    ["sub", "desc", "description", "설명", "문구", "subcopy", "서브",
                   "body", "subtext", "sub_text", "subhead"],
    "cta":        ["cta", "button", "date", "기간", "신청", "구매", "btn", "기한", "일정",
                   "apply", "buy", "order"],
    "product":    ["product", "상품", "제품", "tube", "cream", "패키지", "bottle", "item",
                   "goods", "pack", "package"],
    "person":     ["person", "model", "모델", "사람", "human", "people", "face"],
    "visual":     ["visual", "main visual", "object", "mainvisual", "key_visual", "keyvisual",
                   "main_visual"],
    "decoration": ["decoration", "icon", "star", "heart", "장식", "deco", "ornament",
                   "shape", "pattern", "effect"],
    "badge":      ["badge", "sale", "discount", "혜택", "해택", "이벤트", "event", "ribbon",
                   "sticker", "seal"],
    "price":      ["price", "가격", "원", "할인율", "won", "cost"],
}

IMPORTANCE_MAP = {
    "background": "required",
    "headline":   "required",
    "product":    "required",
    "person":     "required",
    "visual":     "required",
    "cta":        "required",
    "logo":       "priority",
    "price":      "priority",
    "badge":      "priority",
    "subcopy":    "priority",
    "decoration": "optional",
    "unknown":    "optional",
}

LAYER_RENDER_ORDER = [
    "background", "visual", "product", "person",
    "decoration", "badge", "price", "logo", "subcopy", "cta", "headline",
]


def infer_layer_role(name: str) -> str:
    n = (name or "").lower()
    for role, keywords in ROLE_KEYWORDS.items():
        if any(k in n for k in keywords):
            return role
    return "unknown"


def infer_role_by_bbox(bbox: list, canvas_w: int, canvas_h: int) -> str | None:
    if len(bbox) < 4:
        return None
    x1, y1, x2, y2 = bbox
    w = max(0, x2 - x1)
    h = max(0, y2 - y1)
    if w <= 0 or h <= 0:
        return None

    area_ratio = (w * h) / max(canvas_w * canvas_h, 1)
    cy = (y1 + y2) / 2 / max(canvas_h, 1)

    if area_ratio > 0.7:
        return "background"
    if cy < 0.2 and w > h * 2 and area_ratio < 0.1:
        return "logo"
    if cy > 0.75 and area_ratio < 0.15:
        return "cta"
    if area_ratio > 0.15 and w / max(h, 1) < 3:
        return "visual"
    return None


def _detect_layer_type(layer) -> str:
    try:
        kind = str(layer.kind)
        for t in ("pixel", "type", "shape", "smartobject", "group"):
            if t in kind.lower():
                return t
    except Exception:
        pass
    return "pixel"


def extract_renderable_layers(psd) -> list:
    canvas_w, canvas_h = psd.width, psd.height
    layers = []

    for idx, layer in enumerate(psd.descendants()):
        try:
            if not layer.is_visible():
                continue
            bbox = [int(layer.left), int(layer.top), int(layer.right), int(layer.bottom)]
            w = bbox[2] - bbox[0]
            h = bbox[3] - bbox[1]
            if w <= 0 or h <= 0:
                continue

            name = layer.name or f"layer_{idx}"
            role = infer_layer_role(name)
            if role == "unknown":
                inferred = infer_role_by_bbox(bbox, canvas_w, canvas_h)
                if inferred:
                    role = inferred

            layers.append({
                "id":         f"layer_{idx:03d}",
                "name":       name,
                "type":       _detect_layer_type(layer),
                "role":       role,
                "importance": IMPORTANCE_MAP.get(role, "optional"),
                "visible":    True,
                "bbox":       bbox,
                "_layer_obj": layer,
            })
        except Exception:
            continue

    return layers


def render_layer_to_image(layer_obj) -> Image.Image | None:
    try:
        img = layer_obj.composite()
        if img is None:
            return None
        img = img.convert("RGBA")
        if img.width < 1 or img.height < 1:
            return None
        return img
    except Exception as e:
        print(f"[LayerReflow] layer render failed: {e}")
        return None


def _get_layout_variant(layers: list) -> str:
    roles = {l["role"] for l in layers}
    if "product" in roles or "person" in roles:
        return "product"
    return "poster"


def build_1250x560_slots(variant: str) -> dict:
    if variant == "product":
        return {
            "background": {"x": 0,   "y": 0,   "w": 1250, "h": 560, "mode": "cover"},
            "logo":       {"x": 60,  "y": 30,  "w": 260,  "h": 50,  "mode": "contain"},
            "headline":   {"x": 60,  "y": 110, "w": 620,  "h": 210, "mode": "contain"},
            "subcopy":    {"x": 60,  "y": 320, "w": 620,  "h": 50,  "mode": "contain"},
            "cta":        {"x": 60,  "y": 390, "w": 620,  "h": 90,  "mode": "contain"},
            "product":    {"x": 720, "y": 70,  "w": 430,  "h": 400, "mode": "contain"},
            "person":     {"x": 720, "y": 70,  "w": 430,  "h": 400, "mode": "contain"},
            "visual":     {"x": 720, "y": 70,  "w": 430,  "h": 400, "mode": "contain"},
            "decoration": {"x": 900, "y": 420, "w": 200,  "h": 100, "mode": "contain"},
            "badge":      {"x": 60,  "y": 470, "w": 200,  "h": 70,  "mode": "contain"},
            "price":      {"x": 60,  "y": 460, "w": 300,  "h": 80,  "mode": "contain"},
        }
    else:
        return {
            "background": {"x": 0,   "y": 0,   "w": 1250, "h": 560, "mode": "cover"},
            "logo":       {"x": 60,  "y": 25,  "w": 360,  "h": 50,  "mode": "contain"},
            "headline":   {"x": 80,  "y": 90,  "w": 720,  "h": 260, "mode": "contain"},
            "cta":        {"x": 80,  "y": 365, "w": 720,  "h": 90,  "mode": "contain"},
            "visual":     {"x": 840, "y": 90,  "w": 320,  "h": 340, "mode": "contain"},
            "decoration": {"x": 840, "y": 90,  "w": 320,  "h": 340, "mode": "contain"},
            "subcopy":    {"x": 80,  "y": 465, "w": 720,  "h": 60,  "mode": "contain"},
            "badge":      {"x": 60,  "y": 470, "w": 200,  "h": 70,  "mode": "contain"},
            "price":      {"x": 60,  "y": 460, "w": 300,  "h": 80,  "mode": "contain"},
        }


def fit_layer_to_slot(layer_img: Image.Image, slot: dict) -> Image.Image:
    slot_w, slot_h = slot["w"], slot["h"]
    src_w, src_h = layer_img.size

    if src_w == 0 or src_h == 0:
        return Image.new("RGBA", (slot_w, slot_h), (0, 0, 0, 0))

    mode = slot.get("mode", "contain")

    if mode == "cover":
        scale = max(slot_w / src_w, slot_h / src_h)
    else:
        scale = min(slot_w / src_w, slot_h / src_h)

    new_w = max(1, int(src_w * scale))
    new_h = max(1, int(src_h * scale))
    resized = layer_img.resize((new_w, new_h), Image.Resampling.LANCZOS)

    if mode == "cover":
        left = max(0, (new_w - slot_w) // 2)
        top = max(0, (new_h - slot_h) // 2)
        return resized.crop((left, top, left + slot_w, top + slot_h))

    canvas = Image.new("RGBA", (slot_w, slot_h), (0, 0, 0, 0))
    x = max(0, (slot_w - new_w) // 2)
    y = max(0, (slot_h - new_h) // 2)
    canvas.alpha_composite(resized, (x, y))
    return canvas


def compose_reflow_canvas(psd, layers: list, slots: dict, target_w: int, target_h: int) -> tuple:
    canvas = Image.new("RGBA", (target_w, target_h), (255, 255, 255, 255))
    used_roles = []

    # background 없으면 PSD composite blur로 채움
    bg_layers = [l for l in layers if l["role"] == "background" and l.get("img")]
    bg_slot = slots.get("background")

    if bg_layers and bg_slot:
        fitted = fit_layer_to_slot(bg_layers[0]["img"], bg_slot)
        canvas.alpha_composite(fitted, (bg_slot["x"], bg_slot["y"]))
        used_roles.append("background")
    else:
        try:
            composed = psd.composite()
            if composed:
                bg = composed.convert("RGBA").resize((target_w, target_h), Image.Resampling.LANCZOS)
                bg = bg.filter(ImageFilter.GaussianBlur(radius=20))
                canvas.alpha_composite(bg, (0, 0))
        except Exception as e:
            print(f"[LayerReflow] blur background failed: {e}")

    # 나머지 레이어 배치 (role별로 가장 큰 레이어 1개씩)
    placed_roles: set = set()
    for role in LAYER_RENDER_ORDER:
        if role == "background":
            continue
        slot = slots.get(role)
        if not slot:
            continue
        role_layers = [l for l in layers if l["role"] == role and l.get("img")]
        if not role_layers:
            continue
        if role in placed_roles:
            continue

        # 같은 role 중 가장 큰 레이어 선택
        best = max(role_layers, key=lambda l: (l["bbox"][2] - l["bbox"][0]) * (l["bbox"][3] - l["bbox"][1]))
        fitted = fit_layer_to_slot(best["img"], slot)
        canvas.alpha_composite(fitted, (slot["x"], slot["y"]))
        placed_roles.add(role)
        used_roles.append(role)

    return canvas, used_roles


def validate_required_roles(layers: list) -> str | None:
    """필수 role 검증. 실패 사유 문자열 반환, 통과 시 None."""
    roles = set(l.get("role") for l in layers)
    has_headline = "headline" in roles
    has_visual = any(r in roles for r in ["product", "person", "visual"])
    has_cta = any(r in roles for r in ["cta", "subcopy", "price", "badge"])

    if not has_headline:
        return "required role not found: headline"
    if not has_visual and not has_cta:
        return "required role not found: visual or cta/subcopy"
    return None


def generate_psd_layer_reflow(file_path: str, target_w: int, target_h: int,
                               output_path: str, debug_dir: str = None) -> dict:
    """PSD 레이어 재배치 배너 생성. 항상 dict 반환 — success/error 필드로 판별."""
    result = {
        "success": False,
        "error": None,
        "template": None,
        "usedLayerRoles": [],
        "detectedRoles": [],
        "extractedLayerCount": 0,
        "outputPath": None,
    }

    # MVP: 1250×560만 지원
    if not (target_w == 1250 and target_h == 560):
        result["error"] = f"unsupported target size: {target_w}x{target_h}"
        print(f"[LayerReflow] {target_w}x{target_h} not supported in MVP, skipping")
        return result

    psd, open_meta = open_psd_safe_with_patch(file_path)
    if not open_meta["success"]:
        result["error"] = f"PSD open failed: {open_meta.get('error', 'unknown')} [{open_meta.get('errorCode')}]"
        print(f"[LayerReflow] PSD open failed: {result['error']}")
        return result

    layers = extract_renderable_layers(psd)
    result["extractedLayerCount"] = len(layers)
    result["detectedRoles"] = sorted(list(set(l["role"] for l in layers)))

    if not layers:
        result["error"] = "no renderable layers found"
        print("[LayerReflow] No renderable layers found")
        return result

    # 필수 role 검증
    validation_error = validate_required_roles(layers)
    if validation_error:
        result["error"] = validation_error
        print(f"[LayerReflow] required role validation failed: {validation_error}")
        return result

    # 레이어별 이미지 렌더링
    for layer in layers:
        layer_obj = layer.pop("_layer_obj", None)
        layer["img"] = render_layer_to_image(layer_obj) if layer_obj else None

        if debug_dir and layer["img"]:
            os.makedirs(debug_dir, exist_ok=True)
            debug_name = f"{layer['id']}_{layer['role']}.png"
            try:
                layer["img"].save(os.path.join(debug_dir, debug_name))
            except Exception:
                pass

    variant = _get_layout_variant(layers)
    slots = build_1250x560_slots(variant)
    template_name = f"horizontal-1250x560-{variant}"

    try:
        canvas, used_roles = compose_reflow_canvas(psd, layers, slots, target_w, target_h)
    except Exception as e:
        result["error"] = f"compose failed: {e}"
        print(f"[LayerReflow] compose failed: {e}")
        return result

    if not used_roles:
        result["error"] = "no layers placed on canvas"
        print("[LayerReflow] No layers placed, aborting")
        return result

    try:
        canvas.save(output_path, "PNG")
    except Exception as e:
        result["error"] = f"save failed: {e}"
        print(f"[LayerReflow] save failed: {e}")
        return result

    result["success"] = True
    result["template"] = template_name
    result["usedLayerRoles"] = used_roles
    result["outputPath"] = output_path
    return result
