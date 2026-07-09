"""3단계: blur 없는 target 배경 생성기.

배경 생성 우선순위:
  1. role=background PSD asset → cover crop        (psd_background_cover)
  2. role=background PSD asset → edge-stretch      (psd_background_extend)
  3. flattened 원본 → 컨텐츠 반대편 clean area crop (flattened_clean_area_cover)
  4. 이미지 dominant color 2-stop 수직 그래디언트   (dominant_gradient)
  5. 이미지 단일 dominant color                     (solid_brand_color)
  6. 중립 회색 (긴급 fallback)                      (emergency_neutral)

불변 규칙:
  - blurUsed 는 항상 False
  - 반환 이미지 크기는 항상 target_width × target_height
  - 모든 mode에서 metadata["backgroundMode"]가 채워져야 함
  - 예외가 발생해도 job을 죽이지 않고 emergency_neutral 반환
"""

import os
from PIL import Image, ImageDraw, ImageStat

# 비율 차이가 이 배수 이상이면 cover crop 대신 edge-stretch 선택
_RATIO_EXTEND_THRESHOLD = 2.0

# 배경으로 쓸 역할 이름 세트
_BACKGROUND_ROLES = frozenset({"background"})

# 컨텐츠(배경 제외) 역할 중 크롭 앵커 계산에 사용할 주요 역할
_CONTENT_ROLES = frozenset({
    "main_image", "person", "headline", "cta", "logo",
    "price", "discount", "body_text",
})


# ─── public functions ─────────────────────────────────────────────────────────

def find_background_object(creative_object_set: dict) -> dict | None:
    """CreativeObjectSet에서 role='background'인 첫 번째 객체 반환.

    imagePath가 있고 실제로 파일이 존재하는 것을 우선.

    >>> find_background_object({"objects": [], "canvas": {}, "warnings": []}) is None
    True
    >>> obj = {"role": "background", "imagePath": None}
    >>> find_background_object({"objects": [obj], "canvas": {}, "warnings": []}) == obj
    True
    """
    objects = (creative_object_set or {}).get("objects", [])
    # imagePath 있고 파일 존재하는 것 우선
    for obj in objects:
        if obj.get("role") in _BACKGROUND_ROLES:
            p = obj.get("imagePath")
            if p and os.path.exists(p):
                return obj
    # 파일이 없어도 role만 맞으면 반환 (caller가 fallback 처리)
    for obj in objects:
        if obj.get("role") in _BACKGROUND_ROLES:
            return obj
    return None


def choose_crop_anchor(
    objects: list[dict],
    source_w: int,
    source_h: int,
    target_w: int,
    target_h: int,
    invert: bool = False,
) -> str:
    """컨텐츠 객체의 무게중심을 기반으로 최적 crop anchor를 결정.

    invert=False (기본): 컨텐츠 쪽으로 앵커 (PSD background layer용)
    invert=True         : 컨텐츠 반대편으로 앵커 (flattened image clean area용)

    반환 형식: "center" | "left" | "right" | "top" | "bottom"
               | "top_left" | "top_right" | "bottom_left" | "bottom_right"

    >>> # 와이드(2000x800) source, 컨텐츠가 왼쪽에 집중된 경우 → 수평 crop 필요
    >>> objects = [{"role": "main_image", "bbox": {"x": 0, "y": 0, "width": 200, "height": 600}}]
    >>> choose_crop_anchor(objects, 2000, 800, 1200, 628, invert=False)
    'left'
    >>> choose_crop_anchor(objects, 2000, 800, 1200, 628, invert=True)
    'right'
    """
    content = [o for o in objects if o.get("role") in _CONTENT_ROLES]
    if not content:
        return "center"

    # 넓이 가중 중심 계산
    total_area = 0.0
    cx_sum = cy_sum = 0.0
    for obj in content:
        bb = obj.get("bbox") or {}
        w = float(bb.get("width", 0))
        h = float(bb.get("height", 0))
        area = w * h
        cx_sum += (bb.get("x", 0) + w / 2) * area
        cy_sum += (bb.get("y", 0) + h / 2) * area
        total_area += area

    if total_area <= 0:
        return "center"

    cx_n = (cx_sum / total_area) / max(source_w, 1)   # 0~1
    cy_n = (cy_sum / total_area) / max(source_h, 1)   # 0~1

    src_ratio = source_w / max(source_h, 1)
    tgt_ratio = target_w / max(target_h, 1)

    h_anchor = "center"
    v_anchor = "center"

    # 수평: source가 target보다 넓으면 좌/우 중 하나를 잘라냄
    if src_ratio > tgt_ratio * 1.05:
        if cx_n < 0.38:
            h_anchor = "right" if invert else "left"
        elif cx_n > 0.62:
            h_anchor = "left" if invert else "right"

    # 수직: source가 target보다 높으면 상/하 중 하나를 잘라냄
    inv_src = source_h / max(source_w, 1)
    inv_tgt = target_h / max(target_w, 1)
    if inv_src > inv_tgt * 1.05:
        if cy_n < 0.38:
            v_anchor = "bottom" if invert else "top"
        elif cy_n > 0.62:
            v_anchor = "top" if invert else "bottom"

    if v_anchor == "center" and h_anchor == "center":
        return "center"
    if v_anchor == "center":
        return h_anchor
    if h_anchor == "center":
        return v_anchor
    return f"{v_anchor}_{h_anchor}"


def cover_resize_no_blur(
    image: "Image.Image",
    target_w: int,
    target_h: int,
    anchor: str = "center",
) -> "Image.Image":
    """cover 방식으로 리사이즈 후 anchor 기준으로 크롭.

    blur 미사용 (LANCZOS downsample 후 정밀 크롭).
    반환: 정확히 target_w × target_h RGBA 이미지

    >>> from PIL import Image
    >>> img = Image.new("RGBA", (200, 100))
    >>> out = cover_resize_no_blur(img, 100, 100, "center")
    >>> out.size
    (100, 100)
    >>> out = cover_resize_no_blur(img, 300, 50, "right")
    >>> out.size
    (300, 50)
    """
    src_w, src_h = image.size
    if src_w <= 0 or src_h <= 0:
        return Image.new("RGBA", (target_w, target_h), (220, 220, 220, 255))

    scale = max(target_w / src_w, target_h / src_h)
    scaled_w = max(1, round(src_w * scale))
    scaled_h = max(1, round(src_h * scale))

    scaled = image.resize((scaled_w, scaled_h), Image.LANCZOS).convert("RGBA")
    ox, oy = _crop_offset(scaled_w, scaled_h, target_w, target_h, anchor)
    return scaled.crop((ox, oy, ox + target_w, oy + target_h))


def extend_edges(
    image: "Image.Image",
    target_w: int,
    target_h: int,
) -> "Image.Image":
    """비율 차이가 클 때 가장자리 색상 복제로 target 크기까지 채움 (blur 없음).

    source를 contain-fit으로 중앙 배치 후,
    빈 영역을 각 가장자리의 평균 색상 수직/수평 스트라이프로 채운다.

    >>> from PIL import Image
    >>> img = Image.new("RGBA", (100, 200), (30, 80, 150, 255))
    >>> out = extend_edges(img, 400, 200)
    >>> out.size
    (400, 200)
    >>> out.mode
    'RGBA'
    """
    src_w, src_h = image.size
    if src_w <= 0 or src_h <= 0:
        return Image.new("RGBA", (target_w, target_h), (220, 220, 220, 255))

    # contain-fit: 잘리지 않게 축소
    scale = min(target_w / src_w, target_h / src_h)
    sw = max(1, round(src_w * scale))
    sh = max(1, round(src_h * scale))
    scaled = image.resize((sw, sh), Image.LANCZOS).convert("RGBA")

    px = (target_w - sw) // 2   # paste x offset
    py = (target_h - sh) // 2   # paste y offset
    samp = max(1, min(6, sw // 8, sh // 8))  # edge 샘플 폭

    canvas = Image.new("RGBA", (target_w, target_h))

    # ── 좌우 채우기 ──────────────────────────────────────────────────────────
    if px > 0:
        # left: scaled 왼쪽 edge 평균 → 1px 스트립 → 수평 복제
        left_avg = scaled.crop((0, 0, samp, sh)).resize((1, sh), Image.LANCZOS)
        canvas.paste(left_avg.resize((px, sh), Image.NEAREST), (0, py))
        # right
        right_x = px + sw
        right_w = target_w - right_x
        if right_w > 0:
            right_avg = scaled.crop((sw - samp, 0, sw, sh)).resize((1, sh), Image.LANCZOS)
            canvas.paste(right_avg.resize((right_w, sh), Image.NEAREST), (right_x, py))

    # ── 상하 채우기 (코너 포함) ───────────────────────────────────────────────
    if py > 0:
        top_avg = scaled.crop((0, 0, sw, samp)).resize((sw, 1), Image.LANCZOS)
        top_center = top_avg.resize((sw, py), Image.NEAREST)

        top_fill = Image.new("RGBA", (target_w, py))
        top_fill.paste(top_center, (px, 0))
        if px > 0:
            tl = scaled.getpixel((0, 0))
            top_fill.paste(Image.new("RGBA", (px, py), tl), (0, 0))
            right_x = px + sw
            right_w = target_w - right_x
            if right_w > 0:
                tr = scaled.getpixel((sw - 1, 0))
                top_fill.paste(Image.new("RGBA", (right_w, py), tr), (right_x, 0))
        canvas.paste(top_fill, (0, 0))

    bot_y = py + sh
    bot_h = target_h - bot_y
    if bot_h > 0:
        bot_avg = scaled.crop((0, sh - samp, sw, sh)).resize((sw, 1), Image.LANCZOS)
        bot_center = bot_avg.resize((sw, bot_h), Image.NEAREST)

        bot_fill = Image.new("RGBA", (target_w, bot_h))
        bot_fill.paste(bot_center, (px, 0))
        if px > 0:
            bl = scaled.getpixel((0, sh - 1))
            bot_fill.paste(Image.new("RGBA", (px, bot_h), bl), (0, 0))
            right_x = px + sw
            right_w = target_w - right_x
            if right_w > 0:
                br = scaled.getpixel((sw - 1, sh - 1))
                bot_fill.paste(Image.new("RGBA", (right_w, bot_h), br), (right_x, 0))
        canvas.paste(bot_fill, (0, bot_y))

    # ── 원본(scaled) 중앙 부착 ────────────────────────────────────────────────
    canvas.paste(scaled, (px, py))
    return canvas


def build_dominant_gradient(
    image: "Image.Image",
    target_w: int,
    target_h: int,
) -> "Image.Image":
    """이미지 상단/하단 dominant color로 수직 그래디언트 배경 생성 (blur 없음).

    >>> from PIL import Image
    >>> img = Image.new("RGB", (100, 100), (255, 0, 0))
    >>> out = build_dominant_gradient(img, 300, 200)
    >>> out.size
    (300, 200)
    >>> out.mode
    'RGBA'
    """
    try:
        w, h = image.size
        region_h = max(1, h // 4)

        top_stat = ImageStat.Stat(image.crop((0, 0, w, region_h)).convert("RGB"))
        bot_stat = ImageStat.Stat(image.crop((0, h - region_h, w, h)).convert("RGB"))

        c1 = tuple(int(m) for m in top_stat.mean[:3]) + (255,)
        c2 = tuple(int(m) for m in bot_stat.mean[:3]) + (255,)
    except Exception:
        c1, c2 = (220, 220, 220, 255), (180, 180, 180, 255)

    result = Image.new("RGBA", (target_w, target_h))
    draw = ImageDraw.Draw(result)

    for y in range(target_h):
        t = y / max(target_h - 1, 1)
        r = int(c1[0] * (1 - t) + c2[0] * t)
        g = int(c1[1] * (1 - t) + c2[1] * t)
        b = int(c1[2] * (1 - t) + c2[2] * t)
        draw.line([(0, y), (target_w - 1, y)], fill=(r, g, b, 255))

    return result


# ─── orchestration ────────────────────────────────────────────────────────────

def build_background(
    creative_object_set: dict,
    original_image,   # PIL Image | str path | None
    target_w: int,
    target_h: int,
    output_dir: str | None = None,
    job_id: str | None = None,
) -> tuple["Image.Image", dict]:
    """blur 없는 target 배경 이미지 + metadata 반환.

    creative_object_set : build_creative_object_set() 결과
    original_image      : 원본 PSD flatten 이미지 (PIL Image 또는 파일 경로)
    target_w / target_h : 출력 크기
    output_dir          : 중간 결과 저장 경로 (None이면 저장 안 함)
    job_id              : 로그 prefix

    반환: (RGBA Image, metadata dict)
    metadata keys: backgroundMode, cropAnchor, fallbackUsed, fallbackReason, blurUsed
    """
    prefix = f"[{job_id or 'job'}][Background]"

    def _ok(img: "Image.Image", mode: str, anchor: str = "center",
            fallback_used: bool = False, fallback_reason: str | None = None) -> tuple:
        out = img.convert("RGBA")
        if out.size != (target_w, target_h):
            out = out.resize((target_w, target_h), Image.LANCZOS)
        return out, {
            "backgroundMode": mode,
            "cropAnchor":     anchor,
            "fallbackUsed":   fallback_used,
            "fallbackReason": fallback_reason,
            "blurUsed":       False,
        }

    objects = (creative_object_set or {}).get("objects", [])
    original_img = _load_image(original_image)

    # ── Step 1: PSD background asset ─────────────────────────────────────────
    bg_obj = find_background_object(creative_object_set)
    bg_img = None
    if bg_obj:
        img_path = bg_obj.get("imagePath")
        if img_path and os.path.exists(img_path):
            bg_img = _load_image(img_path)

    if bg_img is not None:
        src_w, src_h = bg_img.size
        src_ratio = src_w / max(src_h, 1)
        tgt_ratio = target_w / max(target_h, 1)
        anchor = choose_crop_anchor(objects, src_w, src_h, target_w, target_h, invert=False)

        if (src_ratio / max(tgt_ratio, 1e-9) >= _RATIO_EXTEND_THRESHOLD
                or tgt_ratio / max(src_ratio, 1e-9) >= _RATIO_EXTEND_THRESHOLD):
            # 비율 차이 너무 큼 → edge-stretch
            print(f"{prefix} psd_background_extend anchor={anchor} "
                  f"src={src_ratio:.2f} tgt={tgt_ratio:.2f}")
            try:
                result = extend_edges(bg_img, target_w, target_h)
                return _ok(result, "psd_background_extend", anchor)
            except Exception as e:
                print(f"{prefix} extend_edges failed: {e}")
        else:
            print(f"{prefix} psd_background_cover anchor={anchor}")
            try:
                result = cover_resize_no_blur(bg_img, target_w, target_h, anchor)
                return _ok(result, "psd_background_cover", anchor)
            except Exception as e:
                print(f"{prefix} cover_resize failed: {e}")

    # ── Step 2: original flattened image clean area ───────────────────────────
    if original_img is not None:
        src_w, src_h = original_img.size
        # clean area = 컨텐츠 반대편 (invert=True)
        anchor = choose_crop_anchor(objects, src_w, src_h, target_w, target_h, invert=True)
        print(f"{prefix} flattened_clean_area_cover anchor={anchor}")
        try:
            result = cover_resize_no_blur(original_img, target_w, target_h, anchor)
            return _ok(result, "flattened_clean_area_cover", anchor, fallback_used=True,
                       fallback_reason="background_object_missing")
        except Exception as e:
            print(f"{prefix} flattened cover failed: {e}")

    # ── Step 3: dominant gradient ─────────────────────────────────────────────
    src_img = bg_img or original_img
    if src_img is not None:
        print(f"{prefix} dominant_gradient")
        try:
            result = build_dominant_gradient(src_img, target_w, target_h)
            return _ok(result, "dominant_gradient", "center", fallback_used=True,
                       fallback_reason="cover_resize_failed")
        except Exception as e:
            print(f"{prefix} dominant_gradient failed: {e}")

    # ── Step 4: solid brand color ─────────────────────────────────────────────
    if src_img is not None:
        print(f"{prefix} solid_brand_color")
        try:
            result = _solid_color(src_img, target_w, target_h)
            return _ok(result, "solid_brand_color", "center", fallback_used=True,
                       fallback_reason="gradient_failed")
        except Exception as e:
            print(f"{prefix} solid_color failed: {e}")

    # ── Step 5 (emergency): neutral gray ─────────────────────────────────────
    print(f"{prefix} emergency_neutral")
    return _ok(
        Image.new("RGBA", (target_w, target_h), (220, 220, 220, 255)),
        "emergency_neutral", "center", fallback_used=True,
        fallback_reason="no_source_image",
    )


# ─── private helpers ──────────────────────────────────────────────────────────

def _crop_offset(
    scaled_w: int, scaled_h: int,
    target_w: int, target_h: int,
    anchor: str,
) -> tuple[int, int]:
    """anchor 문자열로부터 crop 시작 좌표 계산."""
    max_ox = max(0, scaled_w - target_w)
    max_oy = max(0, scaled_h - target_h)
    parts = set(anchor.lower().replace("-", "_").split("_"))

    ox = 0 if "left"   in parts else (max_ox if "right"  in parts else max_ox // 2)
    oy = 0 if "top"    in parts else (max_oy if "bottom" in parts else max_oy // 2)
    return ox, oy


def _load_image(source) -> "Image.Image | None":
    """PIL Image 또는 파일 경로에서 이미지 로드. 실패 시 None."""
    if source is None:
        return None
    if isinstance(source, Image.Image):
        return source
    if isinstance(source, str) and os.path.exists(source):
        try:
            return Image.open(source).copy()
        except Exception as e:
            print(f"[Background] image load failed {source}: {e}")
    return None


def _solid_color(
    image: "Image.Image",
    target_w: int,
    target_h: int,
) -> "Image.Image":
    """이미지 전체 평균색으로 단색 배경 생성."""
    try:
        stat = ImageStat.Stat(image.convert("RGB"))
        r, g, b = (int(m) for m in stat.mean[:3])
        return Image.new("RGBA", (target_w, target_h), (r, g, b, 255))
    except Exception:
        return Image.new("RGBA", (target_w, target_h), (200, 200, 200, 255))
