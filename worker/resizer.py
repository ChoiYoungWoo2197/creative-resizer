from psd_tools import PSDImage
from PIL import Image
import os
import subprocess
from io import BytesIO


def _load_psd_via_imagemagick(psd_path: str) -> Image.Image:
    """psd-tools 미지원 버전일 때 ImageMagick으로 폴백."""
    try:
        result = subprocess.run(
            ['convert', f'{psd_path}[0]', '-flatten', 'PNG:-'],
            capture_output=True, timeout=120, check=True
        )
        img = Image.open(BytesIO(result.stdout))
        img.load()
        return img
    except subprocess.CalledProcessError as e:
        raise ValueError(f"PSD 파일을 열 수 없습니다 (ImageMagick): {e.stderr.decode(errors='replace')}")
    except FileNotFoundError:
        raise ValueError("ImageMagick(convert)이 설치되지 않았습니다.")


def load_psd_as_image(psd_path: str) -> Image.Image:
    ext = os.path.splitext(psd_path)[1].lower()

    if ext in ('.psd', '.psb'):
        try:
            psd = PSDImage.open(psd_path)
            img = psd.composite()
            if img is None:
                raise ValueError("PSD 합성 이미지를 생성할 수 없습니다. 레이어가 비어있거나 잠겨 있을 수 있습니다.")
        except ValueError:
            raise
        except Exception:
            # psd-tools 미지원 버전(v8 등) → ImageMagick 폴백
            img = _load_psd_via_imagemagick(psd_path)
    else:
        try:
            img = Image.open(psd_path)
            img.load()
        except Exception as e:
            raise ValueError(f"이미지 파일을 열 수 없습니다: {e}")

    if img.mode in ("CMYK", "P", "LAB"):
        img = img.convert("RGBA")
    elif img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA")
    return img


def resize_cover(img: Image.Image, width: int, height: int) -> Image.Image:
    src_ratio = img.width / img.height
    dst_ratio = width / height

    if src_ratio > dst_ratio:
        new_h = height
        new_w = int(img.width * height / img.height)
    else:
        new_w = width
        new_h = int(img.height * width / img.width)

    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (img.width - width) // 2
    top = (img.height - height) // 2
    return img.crop((left, top, left + width, top + height))


def resize_contain(img: Image.Image, width: int, height: int) -> Image.Image:
    img = img.copy()
    img.thumbnail((width, height), Image.LANCZOS)
    canvas = Image.new("RGBA", (width, height), (255, 255, 255, 255))
    offset = ((width - img.width) // 2, (height - img.height) // 2)
    canvas.paste(img, offset)
    return canvas


def make_blur_background(img: Image.Image, width: int, height: int) -> Image.Image:
    """블러 배경 캔버스만 생성 (전경 미포함)."""
    from PIL import ImageFilter
    bg = img.copy()
    if bg.mode != "RGBA":
        bg = bg.convert("RGBA")
    bg = bg.resize((width, height), Image.LANCZOS)
    return bg.filter(ImageFilter.GaussianBlur(radius=24))


def resize_blur_bg(img: Image.Image, width: int, height: int) -> Image.Image:
    from PIL import ImageFilter

    bg = img.copy().resize((width, height), Image.LANCZOS)
    bg = bg.filter(ImageFilter.GaussianBlur(radius=20))

    img.thumbnail((width, height), Image.LANCZOS)
    offset = ((width - img.width) // 2, (height - img.height) // 2)

    if bg.mode != "RGBA":
        bg = bg.convert("RGBA")
    if img.mode != "RGBA":
        img = img.convert("RGBA")

    bg.paste(img, offset, img)
    return bg


def get_smart_zoom(src_w: int, src_h: int, dst_w: int, dst_h: int, strength: str = "balanced") -> float:
    src_ratio = src_w / src_h
    dst_ratio = dst_w / dst_h
    ratio_gap = abs(src_ratio - dst_ratio) / max(src_ratio, dst_ratio)

    if strength == "safe":
        if ratio_gap > 0.55:
            return 1.00
        if ratio_gap > 0.35:
            return 1.02
        if ratio_gap > 0.20:
            return 1.04
        return 1.06

    if strength == "fill":
        if ratio_gap > 0.55:
            return 1.08
        if ratio_gap > 0.35:
            return 1.14
        if ratio_gap > 0.20:
            return 1.20
        return 1.25

    # balanced (default)
    if ratio_gap > 0.55:
        return 1.03
    if ratio_gap > 0.35:
        return 1.08
    if ratio_gap > 0.20:
        return 1.12
    return 1.16


def get_anchor_position(canvas_w: int, canvas_h: int, fg_w: int, fg_h: int, focal_position: str) -> tuple[int, int]:
    cx = (canvas_w - fg_w) // 2
    cy = (canvas_h - fg_h) // 2
    positions = {
        "center":       (cx, cy),
        "top":          (cx, 0),
        "bottom":       (cx, canvas_h - fg_h),
        "left":         (0, cy),
        "right":        (canvas_w - fg_w, cy),
        "left-top":     (0, 0),
        "right-top":    (canvas_w - fg_w, 0),
        "left-bottom":  (0, canvas_h - fg_h),
        "right-bottom": (canvas_w - fg_w, canvas_h - fg_h),
    }
    return positions.get(focal_position, positions["center"])


def resize_smart_fit(img: Image.Image, width: int, height: int,
                     strength: str = "balanced", focal_position: str = "center") -> Image.Image:
    from PIL import ImageFilter, ImageEnhance

    src = img.copy()
    if src.mode != "RGBA":
        src = src.convert("RGBA")

    # 배경: cover로 꽉 채운 뒤 블러 + 약간 어둡게 (전경이 더 잘 보이도록)
    bg = resize_cover(src, width, height)
    bg = bg.filter(ImageFilter.GaussianBlur(radius=28))
    bg = ImageEnhance.Brightness(bg).enhance(0.9)
    bg = ImageEnhance.Contrast(bg).enhance(0.95)

    # 전경: contain 기준 축소 + 비율 차이에 따른 adaptive zoom
    scale = min(width / src.width, height / src.height)
    scale *= get_smart_zoom(src.width, src.height, width, height, strength)

    new_w = int(src.width * scale)
    new_h = int(src.height * scale)
    fg = src.resize((new_w, new_h), Image.LANCZOS)

    x, y = get_anchor_position(width, height, new_w, new_h, focal_position)

    # fg가 canvas보다 커지는 경우에도 안전하게 합성
    canvas = bg.convert("RGBA")
    paste_x = max(x, 0)
    paste_y = max(y, 0)
    crop_left = max(-x, 0)
    crop_top = max(-y, 0)
    crop_right = min(crop_left + width, fg.width)
    crop_bottom = min(crop_top + height, fg.height)
    fg_crop = fg.crop((crop_left, crop_top, crop_right, crop_bottom))
    canvas.alpha_composite(fg_crop, (paste_x, paste_y))

    return canvas


RESIZE_FUNCS = {
    "cover": resize_cover,
    "contain": resize_contain,
    "blur-bg": resize_blur_bg,
    "smart-fit": resize_smart_fit,
}


def collect_focus_boxes(elements: list, required_groups: list, priority_groups: list,
                        img_w: int = 0, img_h: int = 0) -> list:
    """requiredGroups/priority 요소 bbox 수집.
    required 있으면 required + priority 모두 반환 (Point 3: 핵심 정보 손실 방지).
    img_w/img_h 가 주어지면 이미지 범위 밖 bbox를 클램핑."""
    required_boxes = []
    priority_boxes = []
    for el in elements:
        bbox = el.get("bbox")
        if not bbox:
            continue
        x = max(0, bbox.get("x", 0))
        y = max(0, bbox.get("y", 0))
        w = bbox.get("width", 0)
        h = bbox.get("height", 0)
        if w <= 0 or h <= 0:
            continue
        x2 = x + w
        y2 = y + h
        if img_w > 0:
            x = min(x, img_w)
            x2 = min(x2, img_w)
        if img_h > 0:
            y = min(y, img_h)
            y2 = min(y2, img_h)
        if x2 <= x or y2 <= y:
            continue
        box = (x, y, x2, y2)
        group = el.get("group", "")
        importance = el.get("importance", "")
        if group in required_groups or importance == "required":
            required_boxes.append(box)
        elif group in priority_groups or importance == "priority":
            priority_boxes.append(box)
    # required 있으면 priority도 함께 포함 (날짜/CTA 등 하단 정보 손실 방지)
    if required_boxes:
        return required_boxes + priority_boxes
    return priority_boxes


def get_text_density(elements: list) -> float:
    """텍스트 계열 요소 비율 반환 (0.0~1.0)."""
    if not elements:
        return 0.0
    text_types = {"text", "cta", "price", "discount"}
    text_count = sum(1 for el in elements if el.get("type", "") in text_types)
    return text_count / len(elements)


def get_focal_from_union(union: tuple, img_w: int, img_h: int) -> str:
    """union box 중심 좌표를 focal_position 문자열로 변환."""
    x1, y1, x2, y2 = union
    cx = (x1 + x2) / 2 / img_w if img_w > 0 else 0.5
    cy = (y1 + y2) / 2 / img_h if img_h > 0 else 0.5

    h = "left" if cx < 0.35 else ("right" if cx > 0.65 else "center")
    v = "top" if cy < 0.35 else ("bottom" if cy > 0.65 else "center")

    if h == "center" and v == "center":
        return "center"
    if h == "center":
        return v
    if v == "center":
        return h
    return f"{h}-{v}"


def union_boxes(boxes: list) -> tuple:
    x1 = min(b[0] for b in boxes)
    y1 = min(b[1] for b in boxes)
    x2 = max(b[2] for b in boxes)
    y2 = max(b[3] for b in boxes)
    return (x1, y1, x2, y2)


def add_padding(box: tuple, img_w: int, img_h: int, padding_ratio: float = 0.12) -> tuple:
    x1, y1, x2, y2 = box
    bw = x2 - x1
    bh = y2 - y1
    px = int(bw * padding_ratio)
    py = int(bh * padding_ratio)
    return (max(0, x1 - px), max(0, y1 - py), min(img_w, x2 + px), min(img_h, y2 + py))


def shift_inside(a: float, b: float, max_val: int) -> tuple:
    if b > max_val:
        a -= (b - max_val)
        b = max_val
    if a < 0:
        b += -a
        a = 0
    return a, min(b, max_val)


def expand_to_ratio(box: tuple, target_ratio: float, img_w: int, img_h: int) -> tuple:
    x1, y1, x2, y2 = box
    bw = x2 - x1
    bh = y2 - y1
    box_ratio = bw / bh if bh > 0 else 1.0
    if box_ratio < target_ratio:
        new_w = bh * target_ratio
        cx = (x1 + x2) / 2
        x1, x2 = cx - new_w / 2, cx + new_w / 2
    else:
        new_h = bw / target_ratio
        cy = (y1 + y2) / 2
        y1, y2 = cy - new_h / 2, cy + new_h / 2
    x1, x2 = shift_inside(x1, x2, img_w)
    y1, y2 = shift_inside(y1, y2, img_h)
    return int(x1), int(y1), int(x2), int(y2)


def resize_focus_fill(img: Image.Image, dst_w: int, dst_h: int,
                      detected_elements: list, required_groups: list, priority_groups: list) -> Image.Image:
    """AI 분석 bbox 기준으로 crop → blur 없이 꽉 찬 배너 생성. 실패 시 balanced fallback."""
    boxes = collect_focus_boxes(detected_elements, required_groups, priority_groups, img.width, img.height)
    if not boxes:
        return resize_smart_fit(img, dst_w, dst_h, strength="balanced", focal_position="center")

    union = union_boxes(boxes)

    # Point 2: 텍스트 밀도 high → padding 더 넓게, crop 덜 공격적
    text_density = get_text_density(detected_elements)
    if text_density > 0.6:
        padding_ratio = 0.20
    elif text_density > 0.3:
        padding_ratio = 0.15
    else:
        padding_ratio = 0.12

    padded = add_padding(union, img.width, img.height, padding_ratio=padding_ratio)
    target_ratio = dst_w / dst_h
    crop_box = expand_to_ratio(padded, target_ratio, img.width, img.height)

    # crop box가 union box를 포함하는지 검증
    ux1, uy1, ux2, uy2 = union
    cx1, cy1, cx2, cy2 = crop_box
    if not (cx1 <= ux1 and cy1 <= uy1 and cx2 >= ux2 and cy2 >= uy2):
        # Point 4: fallback 시 union 위치 기반 focal_position 사용 (항상 center 아님)
        focal = get_focal_from_union(union, img.width, img.height)
        return resize_smart_fit(img, dst_w, dst_h, strength="balanced", focal_position=focal)

    cropped = img.crop(crop_box)
    return cropped.resize((dst_w, dst_h), Image.LANCZOS)


def get_target_layout_type(dst_w: int, dst_h: int) -> str:
    """타겟 규격의 비율로 레이아웃 유형 판단."""
    ratio = dst_w / max(dst_h, 1)
    if ratio >= 2.5:
        return "extreme_horizontal"   # 728×90, 320×100
    elif ratio >= 1.3:
        return "horizontal"           # 1250×560, 1200×628
    elif ratio >= 0.8:
        return "square"               # 1080×1080
    else:
        return "vertical"             # 300×600, 1080×1920


def normalize_content_bands(content_bands: list, img_height: int) -> list:
    result = []
    for b in content_bands:
        y1 = b.get("y1")
        y2 = b.get("y2")
        if not isinstance(y1, (int, float)) or not isinstance(y2, (int, float)):
            continue
        y1 = max(0, int(y1))
        y2 = min(img_height, int(y2))
        if y2 <= y1:
            continue
        result.append({**b, "y1": y1, "y2": y2})
    return sorted(result, key=lambda b: b["y1"])


def _infer_reflow_priority(band: dict) -> str:
    """reflowPriority 필드가 없으면 role로 추정."""
    role = band.get("role", "")
    if role in ("headline", "main_title"):
        return "hero"
    elif role in ("date_cta", "date_info", "cta", "logo"):
        return "support"
    else:
        return "optional"


def select_reflow_bands(bands: list, layout_type: str) -> list:
    """role/reflowPriority 기준으로 band를 선택한다.
    - extreme_horizontal: hero + date_cta 계열 support 1개만
    - horizontal: hero + support (canDrop=False optional 1개 허용)
    - square/vertical: 모두 포함 시도
    """
    def priority(b):
        rp = b.get("reflowPriority") or _infer_reflow_priority(b)
        return rp

    hero_bands    = [b for b in bands if priority(b) == "hero"]
    support_bands = [b for b in bands if priority(b) == "support"]
    optional_bands = [b for b in bands if priority(b) == "optional"]

    # hero가 없으면 importance=required 중에서 선택
    if not hero_bands:
        hero_bands = [b for b in bands if b.get("importance") == "required"]
        support_bands = [b for b in bands if b not in hero_bands]
        optional_bands = []

    if layout_type == "extreme_horizontal":
        selected = hero_bands[:1]
        date_cta = [b for b in support_bands
                    if b.get("role") in ("date_cta", "date_info", "cta")]
        if date_cta:
            selected.append(date_cta[0])
    elif layout_type == "horizontal":
        selected = hero_bands + support_bands
        non_droppable = [b for b in optional_bands if not b.get("canDrop", True)]
        selected.extend(non_droppable[:1])
    else:
        selected = hero_bands + support_bands + optional_bands

    return selected


def _build_horizontal_banner_slots(dst_w: int, dst_h: int, selected: list) -> list:
    """horizontal(1250×560 등): targetPlacement/role 기준 배너형 슬롯 배치.
    logo → top(12%), hero/headline → center(크게), support/date_cta → bottom(25%).
    """
    top_bands, center_bands, bottom_bands = [], [], []
    for b in selected:
        tp   = b.get("targetPlacement", "")
        role = b.get("role", "")
        rp   = b.get("reflowPriority") or _infer_reflow_priority(b)
        if tp == "top" or role == "logo":
            top_bands.append(b)
        elif tp == "bottom" or role in ("date_cta", "date_info", "cta"):
            bottom_bands.append(b)
        elif tp == "center" or rp == "hero":
            center_bands.append(b)
        else:
            bottom_bands.append(b)

    top_h    = int(dst_h * 0.12) if top_bands    else 0
    bottom_h = int(dst_h * 0.25) if bottom_bands else 0
    center_h = max(1, dst_h - top_h - bottom_h)

    slots = []
    y = 0
    if top_bands:
        unit = top_h // len(top_bands)
        for i, b in enumerate(top_bands):
            h = unit if i < len(top_bands) - 1 else max(1, top_h - unit * i)
            slots.append({"band": b, "x": 0, "y": y, "w": dst_w, "h": h, "position": (0, y)})
            y += h

    cy = y
    if center_bands:
        unit = center_h // len(center_bands)
        for i, b in enumerate(center_bands):
            h = unit if i < len(center_bands) - 1 else max(1, center_h - unit * i)
            slots.append({"band": b, "x": 0, "y": cy, "w": dst_w, "h": h, "position": (0, cy)})
            cy += h

    by = dst_h - bottom_h
    if bottom_bands:
        unit = bottom_h // len(bottom_bands)
        for i, b in enumerate(bottom_bands):
            h = unit if i < len(bottom_bands) - 1 else max(1, bottom_h - unit * i)
            slots.append({"band": b, "x": 0, "y": by, "w": dst_w, "h": h, "position": (0, by)})
            by += h

    return slots


def build_reflow_slots(dst_w: int, dst_h: int, layout_type: str, selected: list) -> list:
    """선택된 band마다 (x, y, w, h) 슬롯을 계산한다."""
    if not selected:
        return []

    def area_ratio(band):
        rp = band.get("reflowPriority") or _infer_reflow_priority(band)
        if rp == "hero":
            return 0.60
        role = band.get("role", "")
        if role == "logo":
            return 0.10
        if rp == "support":
            return 0.22
        return 0.08

    # extreme_horizontal: 가로 분할
    if layout_type == "extreme_horizontal":
        hero = [b for b in selected if (b.get("reflowPriority") or _infer_reflow_priority(b)) == "hero"]
        others = [b for b in selected if b not in hero]
        slots = []
        if hero and others:
            hero_w = int(dst_w * 0.60)
            slots.append({"band": hero[0],   "x": 0,      "y": 0, "w": hero_w,         "h": dst_h, "position": (0, 0)})
            slots.append({"band": others[0], "x": hero_w, "y": 0, "w": dst_w - hero_w, "h": dst_h, "position": (hero_w, 0)})
        else:
            slots.append({"band": selected[0], "x": 0, "y": 0, "w": dst_w, "h": dst_h, "position": (0, 0)})
        return slots

    # horizontal: targetPlacement/role 기반 배너형 슬롯 배치
    if layout_type == "horizontal":
        return _build_horizontal_banner_slots(dst_w, dst_h, selected)

    # 세로 배치 (square/vertical)
    ratios = [area_ratio(b) for b in selected]
    total = sum(ratios)

    slots = []
    y_cursor = 0
    for i, (band, ratio) in enumerate(zip(selected, ratios)):
        if i == len(selected) - 1:
            h = max(1, dst_h - y_cursor)
        else:
            h = max(1, int(dst_h * ratio / total))
        slots.append({"band": band, "x": 0, "y": y_cursor, "w": dst_w, "h": h, "position": (0, y_cursor)})
        y_cursor += h

    return slots


def crop_band(img: Image.Image, band: dict) -> Image.Image:
    y1 = max(0, int(band["y1"]))
    y2 = min(img.height, int(band["y2"]))
    return img.crop((0, y1, img.width, y2))


def fit_band_to_slot(band_img: Image.Image, slot: dict) -> Image.Image:
    slot_w, slot_h = slot["w"], slot["h"]
    src_w, src_h = band_img.size
    if src_w == 0 or src_h == 0:
        return Image.new("RGBA", (slot_w, slot_h), (0, 0, 0, 0))
    can_crop = slot.get("band", {}).get("canCrop", False)
    src_rgba = band_img.convert("RGBA") if band_img.mode != "RGBA" else band_img.copy()
    if can_crop:
        # cover 방식: slot을 꽉 채우고 중앙 crop
        scale = max(slot_w / src_w, slot_h / src_h)
        new_w = max(1, int(src_w * scale))
        new_h = max(1, int(src_h * scale))
        resized = src_rgba.resize((new_w, new_h), Image.LANCZOS)
        cx = (new_w - slot_w) // 2
        cy = (new_h - slot_h) // 2
        return resized.crop((cx, cy, cx + slot_w, cy + slot_h))
    else:
        # contain 방식: 비율 유지하며 축소, 투명 여백
        scale = min(slot_w / src_w, slot_h / src_h)
        new_w = max(1, int(src_w * scale))
        new_h = max(1, int(src_h * scale))
        resized = src_rgba.resize((new_w, new_h), Image.LANCZOS)
        canvas = Image.new("RGBA", (slot_w, slot_h), (0, 0, 0, 0))
        x = (slot_w - new_w) // 2
        y = (slot_h - new_h) // 2
        canvas.alpha_composite(resized, (x, y))
        return canvas


def resize_poster_reflow(img: Image.Image, dst_w: int, dst_h: int,
                          content_bands: list) -> Image.Image:
    """재구성형 poster-reflow: band를 role/reflowPriority 기준으로 분류 → 슬롯 배치.
    원본 전체 보존이 목표가 아니라, 타겟 배너 규격에 맞게 핵심 메시지를 재구성.
    """
    layout_type = get_target_layout_type(dst_w, dst_h)
    bands = normalize_content_bands(content_bands, img.height)

    if not bands:
        return resize_letterbox(img, dst_w, dst_h)

    selected = select_reflow_bands(bands, layout_type)
    if not selected:
        return resize_letterbox(img, dst_w, dst_h)

    slots = build_reflow_slots(dst_w, dst_h, layout_type, selected)
    bg = make_blur_background(img, dst_w, dst_h)

    for slot in slots:
        band_img = crop_band(img, slot["band"])
        fitted = fit_band_to_slot(band_img, slot)
        bg.alpha_composite(fitted, slot["position"])

    return bg


def adjust_offset(paste_x: int, paste_y: int, resized_w: int, resized_h: int,
                  dst_w: int, dst_h: int) -> tuple[int, int]:
    """이미지가 캔버스보다 작으면 중앙, 크면 캔버스 범위로 클램핑."""
    if resized_w <= dst_w:
        paste_x = (dst_w - resized_w) // 2
    else:
        if paste_x > 0:
            paste_x = 0
        if paste_x + resized_w < dst_w:
            paste_x = dst_w - resized_w
    if resized_h <= dst_h:
        paste_y = (dst_h - resized_h) // 2
    else:
        if paste_y > 0:
            paste_y = 0
        if paste_y + resized_h < dst_h:
            paste_y = dst_h - resized_h
    return paste_x, paste_y


def resize_center_crop(img: Image.Image, dst_w: int, dst_h: int) -> Image.Image:
    """꽉 찬 기준 후보: 목표 규격을 완전히 채우도록 확대 후 중앙 crop."""
    src_w, src_h = img.size
    scale = max(dst_w / src_w, dst_h / src_h)
    new_w = max(1, int(src_w * scale))
    new_h = max(1, int(src_h * scale))
    resized = img.resize((new_w, new_h), Image.LANCZOS)
    left = max(0, (new_w - dst_w) // 2)
    top = max(0, (new_h - dst_h) // 2)
    return resized.crop((left, top, left + dst_w, top + dst_h))


def resize_letterbox(img: Image.Image, dst_w: int, dst_h: int) -> Image.Image:
    """무손실 후보: 원본 전체 보존, blur 배경으로 여백 채움."""
    src_w, src_h = img.size
    scale = min(dst_w / src_w, dst_h / src_h)
    new_w = max(1, int(src_w * scale))
    new_h = max(1, int(src_h * scale))
    src_rgba = img.convert("RGBA") if img.mode != "RGBA" else img.copy()
    resized = src_rgba.resize((new_w, new_h), Image.LANCZOS)
    bg = make_blur_background(img, dst_w, dst_h)
    x = (dst_w - new_w) // 2
    y = (dst_h - new_h) // 2
    bg.alpha_composite(resized, (x, y))
    return bg


def resize_object_aware_fit(img: Image.Image, dst_w: int, dst_h: int,
                             detected_elements: list, required_groups: list,
                             priority_groups: list) -> Image.Image:
    """필수/우선순위 요소가 캔버스 안에 들어오도록 scale + 위치 조정.
    focus-fill보다 덜 공격적 — crop 없이 blur 배경 사용."""
    boxes = collect_focus_boxes(detected_elements, required_groups, priority_groups, img.width, img.height)
    if not boxes:
        return resize_letterbox(img, dst_w, dst_h)

    union = union_boxes(boxes)
    src_w, src_h = img.size
    ux1, uy1, ux2, uy2 = union
    union_w = max(1, ux2 - ux1)
    union_h = max(1, uy2 - uy1)

    padding_ratio = 0.12
    fit_w = dst_w * (1 - padding_ratio * 2)
    fit_h = dst_h * (1 - padding_ratio * 2)

    scale_by_union = min(fit_w / union_w, fit_h / union_h)
    contain_scale = min(dst_w / src_w, dst_h / src_h)
    max_scale = contain_scale * 1.25
    scale = min(scale_by_union, max_scale)
    scale = max(scale, contain_scale)

    resized_w = max(1, int(src_w * scale))
    resized_h = max(1, int(src_h * scale))
    src_rgba = img.convert("RGBA") if img.mode != "RGBA" else img.copy()
    resized = src_rgba.resize((resized_w, resized_h), Image.LANCZOS)

    union_cx = ((ux1 + ux2) / 2) * scale
    union_cy = ((uy1 + uy2) / 2) * scale
    paste_x = int(dst_w / 2 - union_cx)
    paste_y = int(dst_h / 2 - union_cy)

    paste_x, paste_y = adjust_offset(paste_x, paste_y, resized_w, resized_h, dst_w, dst_h)

    bg = make_blur_background(img, dst_w, dst_h)
    bg.alpha_composite(resized, (paste_x, paste_y))
    return bg


def generate_candidates(input_path: str, output_dir: str, spec: dict,
                        resize_mode: str = "smart-fit", focal_position: str = "center",
                        strengths: list = None, detected_elements: list = None,
                        required_groups: list = None, priority_groups: list = None,
                        content_bands: list = None) -> tuple[str, list]:
    if strengths is None:
        strengths = ["safe", "balanced", "fill"]

    os.makedirs(output_dir, exist_ok=True)
    img = load_psd_as_image(input_path)

    w = spec["width"]
    h = spec["height"]

    # 원본 contain 썸네일 저장 (OpenAI 비교 기준용)
    original_name = f"original_{w}x{h}.png"
    original_path = os.path.join(output_dir, original_name)
    resize_contain(img, w, h).save(original_path)

    results = []
    for strength in strengths:
        safe_strength = strength.replace("-", "_")
        file_name = f"candidate_{safe_strength}_{w}x{h}.png"
        output_path = os.path.join(output_dir, file_name)

        if strength == "focus-fill":
            resized = resize_focus_fill(img, w, h,
                                        detected_elements or [],
                                        required_groups or [],
                                        priority_groups or [])
        elif strength == "poster-reflow":
            resized = resize_poster_reflow(img, w, h, content_bands or [])
        elif strength == "center-crop":
            resized = resize_center_crop(img, w, h)
        elif strength == "letterbox":
            resized = resize_letterbox(img, w, h)
        elif strength == "object-aware-fit":
            resized = resize_object_aware_fit(img, w, h,
                                              detected_elements or [],
                                              required_groups or [],
                                              priority_groups or [])
        elif resize_mode == "smart-fit":
            resized = resize_smart_fit(img, w, h, strength, focal_position)
        else:
            resize_fn = RESIZE_FUNCS.get(resize_mode, resize_cover)
            resized = resize_fn(img, w, h)

        resized.save(output_path)
        results.append({
            "strength": strength,
            "fileName": file_name,
            "filePath": output_path,
            "width": w,
            "height": h,
        })

    return original_path, results


def generate(psd_path: str, specs: list[dict], resize_mode: str,
             output_format: str, output_dir: str, smart_fit_strength: str = "balanced",
             focal_position: str = "center", source_type: str = "image",
             psd_mode: str = "artboard-first") -> list[dict]:

    os.makedirs(output_dir, exist_ok=True)
    results = []

    # PSD 레이어 재배치 모드 (4차-2)
    if source_type == "psd" and psd_mode == "layer-reflow":
        import psd_layer_reflow

        for spec in specs:
            media = spec["media"]
            w = spec["width"]
            h = spec["height"]
            slug = spec.get("slug", "")
            name = spec.get("name", "")

            ext = "jpg" if output_format in ("jpg", "jpeg") else output_format
            slug_part = f"_{slug}" if slug else ""
            filename = f"{media}{slug_part}_{w}x{h}.{ext}"
            out_path = os.path.join(output_dir, filename)
            debug_dir = os.path.join(output_dir, "debug_layers")

            reflow_result = psd_layer_reflow.generate_psd_layer_reflow(
                psd_path, w, h, out_path, debug_dir
            )

            if reflow_result is not None:
                # layer-reflow 성공
                actual_render_mode = "layer-reflow"
                layer_reflow_template = reflow_result.get("template")
                used_layer_roles = reflow_result.get("usedLayerRoles", [])
                if output_format in ("jpg", "jpeg"):
                    img = Image.open(out_path).convert("RGB")
                    img.save(out_path)
            else:
                # fallback → artboard-first 체인 재사용
                import psd_analyzer
                analysis = psd_analyzer.analyze_psd_file(psd_path)
                best_ab = psd_analyzer.select_best_artboard(analysis["artboards"], w, h)
                is_full_canvas = (best_ab is None or best_ab.get("id") == "full_canvas")
                ab_img = None

                if not is_full_canvas:
                    ab_img, actual_render_mode = psd_analyzer.safe_render_artboard(psd_path, best_ab)
                if ab_img is None:
                    ab_img, actual_render_mode = psd_analyzer.fallback_flatten_psd(psd_path)
                if ab_img is None:
                    ab_img = Image.new("RGBA", (w, h), (200, 200, 200, 255))
                    actual_render_mode = "failed"

                resized = resize_smart_fit(ab_img, w, h,
                                           strength=smart_fit_strength or "balanced",
                                           focal_position=focal_position or "center")
                if output_format in ("jpg", "jpeg"):
                    resized = resized.convert("RGB")
                resized.save(out_path)
                layer_reflow_template = None
                used_layer_roles = []

            file_size = os.path.getsize(out_path)
            with Image.open(out_path) as check_img:
                actual_w, actual_h = check_img.size
            valid = (actual_w == w and actual_h == h)
            validation_message = "정상" if valid else f"expected={w}x{h}, actual={actual_w}x{actual_h}"

            results.append({
                "media": media,
                "name": name,
                "slug": slug,
                "width": w,
                "height": h,
                "fileName": filename,
                "filePath": out_path,
                "fileSize": file_size,
                "valid": valid,
                "validationMessage": validation_message,
                "selectedArtboardId": None,
                "selectedArtboardName": None,
                "actualPsdRenderMode": actual_render_mode,
                "layerReflowTemplate": layer_reflow_template,
                "usedLayerRoles": used_layer_roles,
            })
        return results

    # PSD 아트보드 모드: 각 spec마다 최적 아트보드를 선택해 렌더링 (안전 렌더링 + fallback 체인)
    if source_type == "psd" and psd_mode == "artboard-first":
        import psd_analyzer

        analysis = psd_analyzer.analyze_psd_file(psd_path)
        artboards = analysis["artboards"]

        for spec in specs:
            media = spec["media"]
            w = spec["width"]
            h = spec["height"]
            slug = spec.get("slug", "")
            name = spec.get("name", "")

            best_ab = psd_analyzer.select_best_artboard(artboards, w, h)
            is_full_canvas = (best_ab is None or best_ab.get("id") == "full_canvas")

            selected_ab_id = None
            selected_ab_name = None
            actual_render_mode = None
            ab_img = None

            if not is_full_canvas:
                ab_img, actual_render_mode = psd_analyzer.safe_render_artboard(psd_path, best_ab)
                if actual_render_mode == "artboard":
                    selected_ab_id = best_ab["id"]
                    selected_ab_name = best_ab["name"]

            if ab_img is None:
                # 아트보드 렌더 실패 또는 full_canvas → flatten fallback 체인
                ab_img, actual_render_mode = psd_analyzer.fallback_flatten_psd(psd_path)

            if ab_img is None:
                # 최종 실패: 회색 빈 이미지로 대체
                ab_img = Image.new("RGBA", (w, h), (200, 200, 200, 255))
                actual_render_mode = "failed"

            if actual_render_mode == "artboard" or resize_mode == "smart-fit":
                resized = resize_smart_fit(ab_img, w, h,
                                           strength=smart_fit_strength or "balanced",
                                           focal_position=focal_position or "center")
            else:
                resized = RESIZE_FUNCS.get(resize_mode, resize_cover)(ab_img, w, h)

            if output_format in ("jpg", "jpeg"):
                resized = resized.convert("RGB")
                ext = "jpg"
            else:
                ext = output_format

            slug_part = f"_{slug}" if slug else ""
            filename = f"{media}{slug_part}_{w}x{h}.{ext}"
            out_path = os.path.join(output_dir, filename)
            resized.save(out_path)

            file_size = os.path.getsize(out_path)
            with Image.open(out_path) as check_img:
                actual_w, actual_h = check_img.size
            valid = (actual_w == w and actual_h == h)
            validation_message = "정상" if valid else f"expected={w}x{h}, actual={actual_w}x{actual_h}"

            results.append({
                "media": media,
                "name": name,
                "slug": slug,
                "width": w,
                "height": h,
                "fileName": filename,
                "filePath": out_path,
                "fileSize": file_size,
                "valid": valid,
                "validationMessage": validation_message,
                "selectedArtboardId": selected_ab_id,
                "selectedArtboardName": selected_ab_name,
                "actualPsdRenderMode": actual_render_mode,
                "layerReflowTemplate": None,
                "usedLayerRoles": [],
            })
        return results

    # 기존 이미지/PSD flatten 처리
    img = load_psd_as_image(psd_path)
    resize_fn = RESIZE_FUNCS.get(resize_mode, resize_cover)

    for spec in specs:
        media = spec["media"]
        w = spec["width"]
        h = spec["height"]
        slug = spec.get("slug", "")
        name = spec.get("name", "")

        if resize_mode == "smart-fit":
            resized = resize_smart_fit(img, w, h, smart_fit_strength, focal_position)
        else:
            resized = resize_fn(img, w, h)

        if output_format in ("jpg", "jpeg"):
            resized = resized.convert("RGB")
            ext = "jpg"
        else:
            ext = output_format

        slug_part = f"_{slug}" if slug else ""
        filename = f"{media}{slug_part}_{w}x{h}.{ext}"
        out_path = os.path.join(output_dir, filename)
        resized.save(out_path)

        file_size = os.path.getsize(out_path)
        with Image.open(out_path) as check_img:
            actual_w, actual_h = check_img.size
        valid = (actual_w == w and actual_h == h)
        validation_message = "정상" if valid else f"expected={w}x{h}, actual={actual_w}x{actual_h}"

        results.append({
            "media": media,
            "name": name,
            "slug": slug,
            "width": w,
            "height": h,
            "fileName": filename,
            "filePath": out_path,
            "fileSize": file_size,
            "valid": valid,
            "validationMessage": validation_message,
            "selectedArtboardId": None,
            "selectedArtboardName": None,
            "actualPsdRenderMode": None,
            "layerReflowTemplate": None,
            "usedLayerRoles": [],
        })

    return results
