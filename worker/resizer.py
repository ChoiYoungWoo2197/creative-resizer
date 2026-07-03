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


def collect_focus_boxes(elements: list, required_groups: list, priority_groups: list) -> list:
    """requiredGroups/importance=required bbox 수집, 없으면 priority 사용"""
    required_boxes = []
    priority_boxes = []
    for el in elements:
        bbox = el.get("bbox")
        if not bbox:
            continue
        x = bbox.get("x", 0)
        y = bbox.get("y", 0)
        w = bbox.get("width", 0)
        h = bbox.get("height", 0)
        if w <= 0 or h <= 0:
            continue
        box = (x, y, x + w, y + h)
        group = el.get("group", "")
        importance = el.get("importance", "")
        if group in required_groups or importance == "required":
            required_boxes.append(box)
        elif group in priority_groups or importance == "priority":
            priority_boxes.append(box)
    return required_boxes or priority_boxes


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
    boxes = collect_focus_boxes(detected_elements, required_groups, priority_groups)
    if not boxes:
        return resize_smart_fit(img, dst_w, dst_h, strength="balanced", focal_position="center")

    union = union_boxes(boxes)
    padded = add_padding(union, img.width, img.height, padding_ratio=0.12)
    target_ratio = dst_w / dst_h
    crop_box = expand_to_ratio(padded, target_ratio, img.width, img.height)

    # crop box가 union box를 포함하는지 검증
    ux1, uy1, ux2, uy2 = union
    cx1, cy1, cx2, cy2 = crop_box
    if not (cx1 <= ux1 and cy1 <= uy1 and cx2 >= ux2 and cy2 >= uy2):
        return resize_smart_fit(img, dst_w, dst_h, strength="balanced", focal_position="center")

    cropped = img.crop(crop_box)
    return cropped.resize((dst_w, dst_h), Image.LANCZOS)


def generate_candidates(input_path: str, output_dir: str, spec: dict,
                        resize_mode: str = "smart-fit", focal_position: str = "center",
                        strengths: list = None, detected_elements: list = None,
                        required_groups: list = None, priority_groups: list = None) -> tuple[str, list]:
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
             focal_position: str = "center") -> list[dict]:
    img = load_psd_as_image(psd_path)
    resize_fn = RESIZE_FUNCS.get(resize_mode, resize_cover)
    results = []

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
        os.makedirs(output_dir, exist_ok=True)
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
        })

    return results
