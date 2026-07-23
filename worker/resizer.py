from PIL import Image
import os
import subprocess
from io import BytesIO
from psd_compat import open_psd_safe_with_patch
from ai_render_context import AiRenderContext, sha256_image, sha256_file


def _try_imagemagick(cmd: list, label: str, errors: list):
    """단일 ImageMagick 명령 실행. 성공 시 RGBA Image, 실패 시 None."""
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=120, check=True)
        img = Image.open(BytesIO(result.stdout))
        img.load()
        print(f"[PSD_LOAD] {label} success")
        return img.convert("RGBA")
    except subprocess.CalledProcessError as e:
        err = e.stderr.decode(errors='replace')[:200] if e.stderr else str(e)
        errors.append({"step": label, "message": err})
        print(f"[PSD_LOAD] {label} failed: {err[:100]}")
    except FileNotFoundError:
        errors.append({"step": label, "message": f"{cmd[0]} not found"})
    except Exception as e:
        errors.append({"step": label, "message": str(e)})
    return None


def load_psd_as_flat_image(psd_path: str) -> tuple:
    """PSD에서 최종 합성 이미지 추출 — 4단계 fallback pipeline.
    반환: (Image|None, meta_dict)
    meta keys: renderSource, fallbackUsed, fallbackReason, sourceWidth, sourceHeight"""
    errors = []

    # 1. psd-tools composite
    try:
        psd, open_meta = open_psd_safe_with_patch(psd_path)
        if open_meta["success"]:
            img = psd.composite()
            if img and img.width > 0 and img.height > 0:
                img = img.convert("RGBA")
                print(f"[PSD_LOAD] psd_tools_composite success width={img.width} height={img.height}")
                return img, {
                    "renderSource": "psd_tools_composite",
                    "fallbackUsed": False,
                    "fallbackReason": None,
                    "fallbackErrors": [],
                    "sourceWidth": img.width,
                    "sourceHeight": img.height,
                }
            errors.append({"step": "psd_tools_composite", "message": "composite() returned None or empty"})
        else:
            errors.append({"step": "psd_tools_composite",
                           "message": open_meta.get("error", "PSD open failed")})
    except Exception as e:
        errors.append({"step": "psd_tools_composite", "message": str(e)})

    first_reason = errors[0]["message"] if errors else "psd-tools failed"
    print(f"[PSD_LOAD] psd_tools_composite failed reason={first_reason[:100]}")

    # 2~6. ImageMagick fallbacks (magick=IM7, convert=IM6 모두 시도)
    im_steps = [
        ("imagemagick_magick_first_page",
         ["magick", f"{psd_path}[0]", "-auto-orient", "-colorspace", "sRGB",
          "-background", "none", "-alpha", "on", "PNG:-"]),
        ("imagemagick_convert_first_page",
         ["convert", f"{psd_path}[0]", "-auto-orient", "-colorspace", "sRGB",
          "-background", "none", "-alpha", "on", "PNG:-"]),
        ("imagemagick_flatten",
         ["magick", psd_path, "-auto-orient", "-flatten", "-colorspace", "sRGB", "PNG:-"]),
        ("imagemagick_flatten",
         ["convert", psd_path, "-auto-orient", "-flatten", "-colorspace", "sRGB", "PNG:-"]),
        # 옵션 최소화 최후 시도
        ("imagemagick_magick_first_page",
         ["magick", f"{psd_path}[0]", "PNG:-"]),
        ("imagemagick_convert_first_page",
         ["convert", f"{psd_path}[0]", "PNG:-"]),
    ]

    for label, cmd in im_steps:
        img = _try_imagemagick(cmd, label, errors)
        if img is not None:
            # 이 단계 이전의 실패들만 기록 (현재 성공 step은 미포함)
            failed_so_far = [e for e in errors if e["step"] != label]
            return img, {
                "renderSource": label,
                "fallbackUsed": True,
                "fallbackReason": first_reason,
                "fallbackErrors": failed_so_far,
                "sourceWidth": img.width,
                "sourceHeight": img.height,
            }

    print(f"[PSD_LOAD] all fallback failed steps={[e['step'] for e in errors]}")
    return None, {
        "renderSource": "unknown",
        "fallbackUsed": True,
        "fallbackReason": first_reason,
        "fallbackErrors": errors,
        "sourceWidth": 0,
        "sourceHeight": 0,
    }


def load_source_image(input_path: str) -> tuple:
    """PSD/PSB → load_psd_as_flat_image, 일반 이미지 → Pillow.
    반환: (Image, meta_dict)"""
    ext = os.path.splitext(input_path)[1].lower()
    if ext in ('.psd', '.psb'):
        img, meta = load_psd_as_flat_image(input_path)
        if img is None:
            raise ValueError(f"PSD 로딩 실패: {meta.get('fallbackReason', 'all fallbacks failed')}")
        return img, meta
    try:
        img = Image.open(input_path)
        img.load()
        if img.mode in ("CMYK", "P", "LAB"):
            img = img.convert("RGBA")
        elif img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA")
        return img, {
            "renderSource": "pillow_image",
            "fallbackUsed": False,
            "fallbackReason": None,
            "sourceWidth": img.width,
            "sourceHeight": img.height,
        }
    except Exception as e:
        raise ValueError(f"이미지 파일 로딩 실패: {e}")


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


# ──────────── 4차-4: Wide-Banner Smart-Fit ────────────

def is_wide_banner_case(src_w: int, src_h: int, dst_w: int, dst_h: int) -> bool:
    """정사각형/세로형 소스 → 가로형 타겟 변환 여부."""
    src_ratio = src_w / max(src_h, 1)
    dst_ratio = dst_w / max(dst_h, 1)
    return src_ratio <= 1.3 and dst_ratio >= 1.8


def _wide_banner_metrics(src_w: int, src_h: int, dst_w: int, dst_h: int, actual_scale: float) -> dict:
    contain_scale = min(dst_w / src_w, dst_h / src_h)
    total_area = dst_w * dst_h

    actual_w = int(src_w * actual_scale)
    actual_h = int(src_h * actual_scale)
    # blurAreaRatio: actualScale 기준으로 전경이 채우지 못한 면적 비율
    fg_visible_w = min(actual_w, dst_w)
    fg_visible_h = min(actual_h, dst_h)
    blur_area_ratio = max(0.0, (total_area - fg_visible_w * fg_visible_h) / total_area)
    crop_w = max(0, actual_w - dst_w)
    crop_h = max(0, actual_h - dst_h)
    crop_ratio = min(1.0, (crop_w / max(actual_w, 1) + crop_h / max(actual_h, 1)) / 2)
    subject_scale = actual_scale / max(contain_scale, 1e-9)

    # 중요 영역(center 70%) 잘림 비율
    imp_x = src_w * 0.15
    imp_y = src_h * 0.15
    imp_w = src_w * 0.70
    imp_h = src_h * 0.70
    paste_x = (dst_w - actual_w) / 2.0
    paste_y = (dst_h - actual_h) / 2.0
    ic_x1 = paste_x + imp_x * actual_scale
    ic_y1 = paste_y + imp_y * actual_scale
    ic_x2 = paste_x + (imp_x + imp_w) * actual_scale
    ic_y2 = paste_y + (imp_y + imp_h) * actual_scale
    vis_x = max(0, min(ic_x2, dst_w) - max(ic_x1, 0))
    vis_y = max(0, min(ic_y2, dst_h) - max(ic_y1, 0))
    imp_area = imp_w * imp_h * (actual_scale ** 2)
    imp_crop = 1.0 - (vis_x * vis_y) / max(imp_area, 1)
    imp_crop = max(0.0, min(1.0, imp_crop))

    # 상단 20% / 하단 25% 텍스트 보호 zone 크롭 비율 (텍스트형 소재 보호)
    top_crop_px  = max(0.0, -paste_y)
    bot_crop_px  = max(0.0, paste_y + actual_h - dst_h)
    top_zone_px  = max(1.0, src_h * 0.20 * actual_scale)
    bot_zone_px  = max(1.0, src_h * 0.25 * actual_scale)
    top_zone_crop = min(1.0, top_crop_px / top_zone_px)
    bot_zone_crop = min(1.0, bot_crop_px / bot_zone_px)
    vertical_crop_ratio = max(top_zone_crop, bot_zone_crop)

    return {
        "blurAreaRatio":      round(blur_area_ratio, 4),
        "cropRatio":          round(crop_ratio, 4),
        "subjectScale":       round(subject_scale, 4),
        "impCropRatio":       round(imp_crop, 4),
        "verticalCropRatio":  round(vertical_crop_ratio, 4),
    }


def _wide_banner_score(m: dict) -> float:
    target_fill = max(0.0, 1.0 - m["blurAreaRatio"])
    subj_size = min(1.0, max(0.0, (m["subjectScale"] - 1.0) * 0.8 + 0.3))
    crop_pen = min(1.0, m["cropRatio"] + m["impCropRatio"] * 0.5)
    text_risk = min(1.0, m["cropRatio"] * 1.5 + m["impCropRatio"])
    # 상단/하단 텍스트 zone 크롭 강력 패널티 (-30점)
    v_crop_pen = min(1.0, m.get("verticalCropRatio", 0.0))
    score = (target_fill * 35 + subj_size * 25
             - m["blurAreaRatio"] * 20 - crop_pen * 25 - text_risk * 15
             - v_crop_pen * 30)
    return round(max(0.0, min(100.0, score)), 2)


# smartFitStrength 별 허용 후보 집합
_WIDE_BANNER_ALLOWED = {
    "safe":     frozenset(["safe", "balanced"]),
    "balanced": frozenset(["safe", "balanced", "fill"]),
    "fill":     frozenset(["safe", "balanced", "fill", "focus-crop"]),
}
_QUALITY_GATE_MIN     = 50.0   # 이 점수 미만은 best로 채택 불가
_FOCUS_CROP_MIN       = 70.0   # focus-crop 허용 최소 점수
_FOCUS_CROP_MAX_CROP  = 0.15   # focus-crop 허용 최대 cropRatio
_FOCUS_CROP_MAX_VCROP = 0.10   # focus-crop 허용 최대 verticalCropRatio
_SAFE_MODE_VCROP_MAX  = 0.02   # safe 모드: 상하 2% 초과 크롭 → 후보 탈락


def _build_safe_contain(src: "Image.Image", dst_w: int, dst_h: int) -> "Image.Image":
    """수직/수평 crop 없이 contain scale 배치. 배경은 gaussian blur (강도 높음).
    품질 게이트 발동 또는 safe 후보 미생성 시 최후 보장용."""
    from PIL import ImageFilter, ImageEnhance
    sw, sh = src.size
    scale = min(dst_w / sw, dst_h / sh)
    nw, nh = int(sw * scale), int(sh * scale)
    fg = src.resize((nw, nh), Image.LANCZOS)
    bg = resize_cover(src, dst_w, dst_h)
    bg = bg.filter(ImageFilter.GaussianBlur(radius=45))
    bg = ImageEnhance.Brightness(bg).enhance(0.88)
    bg = ImageEnhance.Contrast(bg).enhance(0.95)
    canvas = bg.convert("RGBA")
    ox, oy = (dst_w - nw) // 2, (dst_h - nh) // 2
    canvas.alpha_composite(fg, (max(ox, 0), max(oy, 0)))
    return canvas


def resize_wide_banner_smart_fit(img: "Image.Image", dst_w: int, dst_h: int,
                                  smart_fit_strength: str = "balanced") -> tuple:
    """정사각형/세로형 → 가로형 전용 smart-fit.
    smartFitStrength 기준으로 허용 후보를 제한하고 품질 게이트(50점)를 적용.
    safe 모드: 수직 crop 2% 초과 시 후보 탈락 (텍스트 완전 보존).
    반환: (best_image, enhance_meta_dict)
    """
    from PIL import ImageFilter, ImageEnhance

    src = img.convert("RGBA") if img.mode != "RGBA" else img.copy()
    src_w, src_h = src.size
    contain_scale = min(dst_w / src_w, dst_h / src_h)

    candidate_scales = {
        "safe":       contain_scale,
        "balanced":   contain_scale * 1.12,
        "fill":       contain_scale * 1.25,
        "focus-crop": contain_scale * 1.18,
    }
    allowed = _WIDE_BANNER_ALLOWED.get(smart_fit_strength,
                                        frozenset(["safe", "balanced", "fill", "focus-crop"]))
    candidates = []

    for ctype, scale in candidate_scales.items():
        if ctype not in allowed:
            continue
        try:
            # 상하 잘림 <= 18% 제한
            if int(src_h * scale) > dst_h:
                h_crop = (int(src_h * scale) - dst_h) / int(src_h * scale)
                if h_crop > 0.18:
                    scale = dst_h / src_h

            new_w = int(src_w * scale)
            new_h = int(src_h * scale)
            fg = src.resize((new_w, new_h), Image.LANCZOS)

            bg = resize_cover(src, dst_w, dst_h)
            bg = bg.filter(ImageFilter.GaussianBlur(radius=30))
            bg = ImageEnhance.Brightness(bg).enhance(0.92)
            bg = ImageEnhance.Contrast(bg).enhance(0.97)
            canvas = bg.convert("RGBA")

            x, y = (dst_w - new_w) // 2, (dst_h - new_h) // 2
            px, py = max(x, 0), max(y, 0)
            cl, ct = max(-x, 0), max(-y, 0)
            cr, cb = min(cl + dst_w, fg.width), min(ct + dst_h, fg.height)
            if cr > cl and cb > ct:
                canvas.alpha_composite(fg.crop((cl, ct, cr, cb)), (px, py))

            m = _wide_banner_metrics(src_w, src_h, dst_w, dst_h, scale)
            score = _wide_banner_score(m)

            # safe 모드: 수직 crop 완전 금지 (2% 초과 시 탈락)
            if smart_fit_strength == "safe" and m["verticalCropRatio"] > _SAFE_MODE_VCROP_MAX:
                print(f"[WideBanner] {ctype} excluded (safe+text protection):"
                      f" vcrop={m['verticalCropRatio']:.3f} > {_SAFE_MODE_VCROP_MAX}")
                continue

            # focus-crop 강화 제한: 점수 낮거나 상하 잘림 크면 탈락
            if ctype == "focus-crop" and (
                score < _FOCUS_CROP_MIN
                or m["cropRatio"] > _FOCUS_CROP_MAX_CROP
                or m["verticalCropRatio"] > _FOCUS_CROP_MAX_VCROP
            ):
                print(f"[WideBanner] focus-crop excluded:"
                      f" score={score:.1f} crop={m['cropRatio']:.3f}"
                      f" vcrop={m['verticalCropRatio']:.3f}")
                continue

            candidates.append({"type": ctype, "image": canvas, "score": score, "m": m})
        except Exception as e:
            print(f"[WideBanner] candidate {ctype} failed: {e}")

    # 후보가 없으면 _build_safe_contain으로 무조건 보장
    if not candidates:
        preserve_img = _build_safe_contain(src, dst_w, dst_h)
        m0 = _wide_banner_metrics(src_w, src_h, dst_w, dst_h, contain_scale)
        return preserve_img, {
            "resizeStrategy": "wide-banner-smart-fit", "candidateType": "safe",
            "candidateScore": _wide_banner_score(m0), "blurAreaRatio": m0["blurAreaRatio"],
            "cropRatio": m0["cropRatio"], "subjectScale": m0["subjectScale"],
            "qualityGate": True, "qualityLabel": "품질 낮음",
        }

    # 품질 게이트: 50점 이상 후보만 best 대상
    good = [c for c in candidates if c["score"] >= _QUALITY_GATE_MIN]
    quality_gate_triggered = False
    if good:
        best = max(good, key=lambda c: c["score"])
    else:
        # 모든 후보가 50점 미만 → _build_safe_contain으로 텍스트 완전 보존
        quality_gate_triggered = True
        preserve_img = _build_safe_contain(src, dst_w, dst_h)
        safe_m = _wide_banner_metrics(src_w, src_h, dst_w, dst_h, contain_scale)
        safe_score = _wide_banner_score(safe_m)
        print(f"[WideBanner] quality gate: all candidates < {_QUALITY_GATE_MIN}pt"
              f" → preserve-all (safe contain, score={safe_score:.1f})")
        return preserve_img, {
            "resizeStrategy": "wide-banner-smart-fit", "candidateType": "safe",
            "candidateScore": safe_score, "blurAreaRatio": safe_m["blurAreaRatio"],
            "cropRatio": safe_m["cropRatio"], "subjectScale": safe_m["subjectScale"],
            "qualityGate": True, "qualityLabel": "품질 낮음",
        }

    score_val = best["score"]
    if score_val >= 70:
        quality_label = "정상"
    elif score_val >= 50:
        quality_label = "주의"
    else:
        quality_label = "품질 낮음"

    print(f"[WideBanner] best={best['type']} score={score_val} quality={quality_label}"
          f" gate={quality_gate_triggered}"
          f" blur={best['m']['blurAreaRatio']:.3f} crop={best['m']['cropRatio']:.3f}"
          f" vcrop={best['m']['verticalCropRatio']:.3f}")

    return best["image"], {
        "resizeStrategy": "wide-banner-smart-fit",
        "candidateType":  best["type"],
        "candidateScore": score_val,
        "blurAreaRatio":  best["m"]["blurAreaRatio"],
        "cropRatio":      best["m"]["cropRatio"],
        "subjectScale":   best["m"]["subjectScale"],
        "qualityGate":    quality_gate_triggered,
        "qualityLabel":   quality_label,
    }


def _apply_resize(img: Image.Image, w: int, h: int,
                  resize_mode: str, smart_fit_strength: str, focal_position: str) -> tuple:
    """리사이징 적용 통합 헬퍼. wide-banner 조건이면 enhanced 처리.
    반환: (resized_image, enhance_meta)
    enhance_meta["blurFillUsed"]: True = smart-fit blur 배경이 최종 출력에 적용됨
    """
    if resize_mode == "smart-fit" and is_wide_banner_case(img.width, img.height, w, h):
        resized, meta = resize_wide_banner_smart_fit(img, w, h, smart_fit_strength)
        # wide-banner: quality≥50이면 crop-only(blur 없음), 미달이면 내부에서 resize_smart_fit fallback
        meta["blurFillUsed"] = meta.get("candidateScore") is None or meta.get("candidateScore", 50) < 50
        return resized, meta

    if resize_mode == "smart-fit":
        resized = resize_smart_fit(img, w, h, smart_fit_strength, focal_position)
        blur_used = True  # resize_smart_fit()는 항상 blur 배경 사용
    else:
        resize_fn = RESIZE_FUNCS.get(resize_mode, resize_cover)
        resized = resize_fn(img, w, h)
        blur_used = False

    return resized, {
        "resizeStrategy": resize_mode,
        "candidateType":  smart_fit_strength if resize_mode == "smart-fit" else None,
        "candidateScore": None,
        "blurAreaRatio":  None,
        "cropRatio":      None,
        "subjectScale":   None,
        "qualityGate":    None,
        "qualityLabel":   None,
        "blurFillUsed":   blur_used,
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


def _try_background_pipeline(
    source_img,
    target_w: int,
    target_h: int,
    job_id: str | None = None,
    output_dir: str | None = None,
) -> dict:
    """Run Stage 19 BackgroundPipeline for one spec. Never raises.

    Controlled by BACKGROUND_PIPELINE_ENABLED env var (default false).
    When compare_only=false and pipeline produced a (target_w×target_h) image,
    'resultImage' in the return dict is that PIL Image; otherwise None.
    """
    import os as _os
    enabled = _os.environ.get("BACKGROUND_PIPELINE_ENABLED", "false").lower() == "true"
    out: dict = {
        "executed": False,
        "enabled": enabled,
        "compareOnly": True,
        "resultImage": None,
        "bestEvaluatedBackgroundSource": None,
        "appliedBackgroundSource": "native",
        "appliedBackgroundScore": 0.0,
        "backgroundFallbackUsed": True,
        "backgroundFallbackReason": "pipeline_disabled",
        "externalInpaintAttempted": False,
        "outpaintAttempted": False,
        "shadowApplied": False,
    }
    if not enabled:
        return out
    try:
        from background import BackgroundPipeline
        from background.schemas import BackgroundRequest, BackgroundOptions
        opts = BackgroundOptions.from_env()
        req = BackgroundRequest(
            source_image=source_img.convert("RGB"),
            target_width=target_w,
            target_height=target_h,
            options=opts,
            request_id=f"{job_id or 'job'}_{target_w}x{target_h}",
        )
        artifact_dir = output_dir or f"/tmp/stage19-{target_w}x{target_h}"
        pipeline = BackgroundPipeline(output_dir=artifact_dir)
        bg = pipeline.process(req)
        pipeline_img = None
        if not bg.background_compare_only and bg.result_image is not None:
            if bg.result_image.size == (target_w, target_h):
                pipeline_img = bg.result_image
        out.update({
            "executed": True,
            "compareOnly": bg.background_compare_only,
            "resultImage": pipeline_img,
            "bestEvaluatedBackgroundSource": bg.best_evaluated_background_source,
            "appliedBackgroundSource": bg.applied_background_source,
            "appliedBackgroundScore": bg.best_evaluated_background_score,
            "backgroundFallbackUsed": bg.fallback_used,
            "backgroundFallbackReason": bg.fallback_reason,
            "externalInpaintAttempted": bg.external_inpaint_attempted,
            "outpaintAttempted": bg.outpaint_attempted,
            "shadowApplied": bg.shadow_applied,
        })
        print(
            f"[{job_id or 'job'}][Stage19] {target_w}x{target_h}"
            f" executed=True compareOnly={bg.background_compare_only}"
            f" source={bg.applied_background_source}"
            f" outpaint={bg.outpaint_attempted}"
            f" fallback={bg.fallback_used}"
        )
    except Exception as _exc:
        out["backgroundFallbackReason"] = f"pipeline_exception:{_exc}"
        print(f"[{job_id or 'job'}][Stage19] exception {target_w}x{target_h}: {_exc}")
    return out


def _generate_ai_only(
    psd_path: str,
    specs: list,
    resize_mode: str,
    output_format: str,
    output_dir: str,
    smart_fit_strength: str = "balanced",
    focal_position: str = "center",
    source_type: str = "image",
    psd_mode: str = "artboard-first",
    selected_artboard_ids: list | None = None,
    object_reflow_enabled: bool = False,
    object_analysis: dict | None = None,
    job_id: str | None = None,
    _provider_override=None,
) -> tuple:
    """Stage 20.3 AI-only rendering (Source-Faithful Repair).

    Fail-closed: raises RuntimeError on any AI failure. No legacy fallback.
    classify_layers=[] for all inputs — AI handles full canvas repair/outpaint.
    """
    from background.source_faithful_repair import run_source_faithful_repair
    from background.external_provider import ProviderFactory

    jid = job_id or "job"
    import time as _time
    t_all = _time.time()
    print(
        f"[AI_ONLY_START] jobId={jid} specCount={len(specs)}"
        f" source_type={source_type} resizeMode={resize_mode}",
        flush=True,
    )

    provider = _provider_override
    if provider is None:
        try:
            provider = ProviderFactory.create(enable_external=True, use_fake_for_test=False)
        except Exception as exc:
            raise RuntimeError(f"AI_ONLY_RENDERING: provider build failed: {exc}") from exc
        if provider is None:
            raise RuntimeError("AI_ONLY_RENDERING: no AI provider (BACKGROUND_AI_API_KEY not set)")

    img, source_meta = load_source_image(psd_path)

    # P0: Compute source provenance hashes immediately after load.
    # composite_sha256 is the single source-of-truth for this job's pixels.
    # AiRenderContext is created per-spec (inside the loop below) with the
    # correct target_w/h and an isolated work_dir.
    _source_file_sha256 = sha256_file(psd_path)
    _composite_sha256 = sha256_image(img)
    print(
        f"[AI_ONLY_PROVENANCE] jobId={jid}"
        f" sourceFileSha256={_source_file_sha256[:16]}"
        f" compositeSha256={_composite_sha256[:16]}",
        flush=True,
    )

    # Stage 21: Parse + classify PSD layers for foreground compositing.
    # Only attempted when source_type=="psd". PNG/JPG inputs skip this path.
    psd_layers_classified: list = []
    _object_map_apply_logs: list = []   # Bundle B: used by layout role resolver
    psd_canvas_w: int = img.width
    psd_canvas_h: int = img.height
    if source_type == "psd":
        try:
            from psd_compat import open_psd_safe_with_patch
            psd_obj, psd_open_meta = open_psd_safe_with_patch(psd_path)
            if psd_obj is not None and psd_open_meta.get("success"):
                from psd_layer_parser import parse_psd_layers
                from layer_role_classifier import classify_layers
                psd_canvas_w = psd_obj.width
                psd_canvas_h = psd_obj.height
                tmp_layer_dir = os.path.join(output_dir, "stage21_layers", jid)
                raw_layers = parse_psd_layers(psd_obj, tmp_layer_dir)
                psd_layers_classified = classify_layers(raw_layers)
                detected_roles = sorted({l.get("role") for l in psd_layers_classified})
                print(
                    f"[STAGE21] PSD layers parsed count={len(psd_layers_classified)}"
                    f" roles={detected_roles}",
                    flush=True,
                )

                # Stage 21.5: Apply Object Map if provided (overrides heuristic roles)
                _obj_results = (object_analysis or {}).get("objects") or []
                if _obj_results:
                    try:
                        # Source hash validation: stored snapshot must match current PSD
                        _oa_sha = (object_analysis or {}).get("sourceFileSha256", "")
                        if _oa_sha and not _oa_sha.startswith("__"):
                            if _oa_sha != _source_file_sha256:
                                raise RuntimeError(
                                    f"OBJECT_ANALYSIS_SOURCE_HASH_MISMATCH:"
                                    f" stored={_oa_sha[:16]}"
                                    f" actual={_source_file_sha256[:16]}"
                                )

                        from object_map_applicator import apply_object_map
                        from layer_role_classifier import _validate_roles

                        _analysis_id = (object_analysis or {}).get("id", "")
                        _analysis_version = (object_analysis or {}).get("analysisVersion", "")
                        _analysis_model = (object_analysis or {}).get("model", "")

                        # D-3: stale version guard — v1 snapshots are outdated
                        _CURRENT_ANALYSIS_VERSION = "psd-object-map-v2"
                        if _analysis_version and _analysis_version != _CURRENT_ANALYSIS_VERSION:
                            print(
                                f"[OBJECT_ANALYSIS_CACHE] STALE_VERSION"
                                f" jobId={jid}"
                                f" storedVersion={_analysis_version}"
                                f" currentVersion={_CURRENT_ANALYSIS_VERSION}"
                                f" cacheHit=false (stale)"
                                f" action=skip",
                                flush=True,
                            )
                        else:
                            print(
                                f"[OBJECT_ANALYSIS_CACHE]"
                                f" jobId={jid}"
                                f" analysisVersion={_analysis_version}"
                                f" cacheHit=true"
                                f" reused=true"
                                f" requestCount=0"
                                f" analysisId={_analysis_id}"
                                f" model={_analysis_model}",
                                flush=True,
                            )

                        # strict=True: production path uses exact layerId match only
                        psd_layers_classified, _apply_logs = apply_object_map(
                            psd_layers_classified, _obj_results, strict=True
                        )
                        _object_map_apply_logs = _apply_logs  # Bundle B: layout role resolver

                        # Re-run contradiction validator after role overrides
                        psd_layers_classified = _validate_roles(psd_layers_classified)

                        _applied = sum(1 for lg in _apply_logs if lg.get("applied"))
                        _strict_count = sum(
                            1 for lg in _apply_logs
                            if lg.get("applied") and lg.get("matchMethod") == "layerId_exact"
                        )
                        _fallback_count = sum(
                            1 for lg in _apply_logs
                            if lg.get("applied") and lg.get("matchMethod") != "layerId_exact"
                        )
                        _rejected_count = sum(1 for lg in _apply_logs if not lg.get("applied"))
                        _unmatched_layers = len(psd_layers_classified) - len(_apply_logs)

                        print(
                            f"[OBJECT_MAP_APPLY]"
                            f" analysisId={_analysis_id}"
                            f" roleSource=stored-object-map"
                            f" matchedLayerCount={len(_apply_logs)}"
                            f" strictMatchCount={_strict_count}"
                            f" fallbackMatchCount={_fallback_count}"
                            f" rejectedMatchCount={_rejected_count}"
                            f" unmatchedLayerCount={_unmatched_layers}"
                            f" newRoles={sorted({l.get('role') for l in psd_layers_classified})}",
                            flush=True,
                        )
                    except RuntimeError:
                        raise  # OBJECT_ANALYSIS_SOURCE_HASH_MISMATCH is fatal
                    except Exception as _omap_err:
                        print(
                            f"[PSD_OBJECT_ANALYSIS] apply_object_map failed: {_omap_err}",
                            flush=True,
                        )
        except Exception as _psd_err:
            print(f"[STAGE21] PSD layer parse failed, foreground compositor disabled: {_psd_err}", flush=True)
            psd_layers_classified = []

    # P1: default 1 (single attempt).  Override via BACKGROUND_AI_MAX_ATTEMPTS env var.
    max_attempts = int(os.environ.get("BACKGROUND_AI_MAX_ATTEMPTS", "1"))
    results = []
    actual_provider_request_count = 0

    # D-3: Background generation mode — semantic_scene_cleanup is the default.
    # source_faithful_repair is the legacy rollback path (explicit only).
    # Invalid mode → fail-closed (no silent fallback).
    _VALID_BG_MODES = ("semantic_scene_cleanup", "source_faithful_repair")
    _bg_mode_raw = os.environ.get("BACKGROUND_GENERATION_MODE", "").strip()
    _bg_mode_default_applied = False
    _bg_mode_explicit_rollback = False
    if not _bg_mode_raw:
        _bg_mode = "semantic_scene_cleanup"
        _bg_mode_default_applied = True
    elif _bg_mode_raw in _VALID_BG_MODES:
        _bg_mode = _bg_mode_raw
        _bg_mode_explicit_rollback = (_bg_mode_raw == "source_faithful_repair")
    else:
        print(
            f"[BG_MODE_INVALID]"
            f" jobId={jid}"
            f" requestedMode={_bg_mode_raw!r}"
            f" allowedModes={list(_VALID_BG_MODES)}"
            f" failClosed=true",
            flush=True,
        )
        raise RuntimeError(
            f"INVALID_BACKGROUND_GENERATION_MODE: {_bg_mode_raw!r}."
            f" Allowed: {_VALID_BG_MODES}."
            f" No automatic fallback (fail-closed)."
        )
    print(
        f"[BG_MODE]"
        f" jobId={jid}"
        f" requestedMode={_bg_mode_raw or '(default)'!r}"
        f" effectiveMode={_bg_mode}"
        f" defaultApplied={_bg_mode_default_applied}"
        f" explicitRollback={_bg_mode_explicit_rollback}"
        f" legacyFallbackUsed=false",
        flush=True,
    )

    _work_base = os.environ.get("AI_WORK_DIR", "/app/storage/work")

    # D-2: Virtual Foreground Extraction (job-level, once per job).
    # Applicable only when input has no native PSD layers (PNG/JPG flattened input).
    # Native layers always take priority — D-2 skipped when psd_layers_classified is set.
    _d2_result = None
    _d2_enabled = os.environ.get("VIRTUAL_FOREGROUND_D2_ENABLED", "true").lower() == "true"
    _is_flattened_input = (source_type != "psd") or not bool(psd_layers_classified)

    if _d2_enabled and _is_flattened_input:
        try:
            from virtual_foreground.manifest_assembler import run_virtual_foreground_extraction
            from virtual_foreground.object_analyzer import FakeObjectAnalysisProvider

            # In production: replace FakeObjectAnalysisProvider with real analysis provider.
            # Tests always use the fake provider (ACTUAL_OPENAI_REQUESTS=0).
            _analysis_provider_cls = os.environ.get("D2_ANALYSIS_PROVIDER", "fake")
            if _analysis_provider_cls == "fake":
                _analysis_provider = FakeObjectAnalysisProvider()
            else:
                _analysis_provider = FakeObjectAnalysisProvider()  # safe default

            _d2_result = run_virtual_foreground_extraction(
                source_image=img,
                source_path=psd_path,
                source_sha256=_source_file_sha256,
                source_type=source_type,
                native_layers=psd_layers_classified,
                background_provider=provider,
                analysis_provider=_analysis_provider,
                output_dir=os.path.join(output_dir, "stage21_d2", jid),
                job_id=jid,
            )
            if _d2_result.success and _d2_result.d2_applicable:
                print(
                    f"[D2_EXTRACTION] SUCCESS jobId={jid}"
                    f" extractedCount={_d2_result.virtual_extracted_count}"
                    f" fgLayers={len(_d2_result.fg_layers)}",
                    flush=True,
                )
            elif not _d2_result.d2_applicable:
                print(
                    f"[D2_EXTRACTION] NOT_APPLICABLE jobId={jid}"
                    f" reason={_d2_result.d2_reason}",
                    flush=True,
                )
        except Exception as _d2_pre_err:
            print(
                f"[D2_EXTRACTION] PRE_LOOP_FAILED jobId={jid}: {_d2_pre_err}",
                flush=True,
            )
            _d2_result = None
    elif not _d2_enabled:
        print(f"[D2_EXTRACTION] DISABLED jobId={jid}", flush=True)

    for spec_idx, spec in enumerate(specs):
        media = spec["media"]
        w = spec["width"]
        h = spec["height"]
        slug = spec.get("slug", "")
        name = spec.get("name", "")
        spec_id = f"{w}x{h}"

        t_spec = _time.time()
        print(
            f"[AI_SPEC_START] jobId={jid} specIndex={spec_idx + 1}/{len(specs)}"
            f" spec={name} size={w}x{h} maxAttempts={max_attempts}",
            flush=True,
        )

        # P0: Per-spec AiRenderContext with isolated work directory + provenance
        _spec_work_dir = os.path.join(_work_base, jid, spec_id)
        render_ctx = AiRenderContext(
            job_id=jid,
            spec_id=spec_id,
            source_path=psd_path,
            source_file_sha256=_source_file_sha256,
            composite_sha256=_composite_sha256,
            target_width=w,
            target_height=h,
            work_dir=_spec_work_dir,
        )
        # Save source composite as first debug artifact
        render_ctx.save_debug_artifact("01-source-composite", img)

        # D-1: Background pipeline — mode-conditional.
        # semantic_scene_cleanup → full composite → semantic scene edit
        # source_faithful_repair → background plate → targeted repair (legacy)
        sfr = None
        _scene_result = None
        _ai_scene_dir = os.path.join(output_dir, "stage20_3", spec_id)

        if _bg_mode == "semantic_scene_cleanup":
            from scene_cleanup.semantic_scene_cleanup import run_semantic_scene_cleanup
            _scene_result = run_semantic_scene_cleanup(
                source_path=psd_path,
                source_type=source_type,
                source_image=img,
                source_file_sha256=_source_file_sha256,
                composite_sha256=_composite_sha256,
                target_w=w,
                target_h=h,
                provider=provider,
                output_dir=_ai_scene_dir,
                render_ctx=render_ctx,
                has_native_layers=bool(psd_layers_classified),
                composite_render_method="psd_composite" if source_type == "psd" else source_type,
                max_attempts=max_attempts,
                job_id=jid,
                spec_id=spec_id,
            )
            actual_provider_request_count += _scene_result.actual_provider_request_count
            if not _scene_result.success or _scene_result.scene_plate_image is None:
                raise RuntimeError(
                    f"SEMANTIC_SCENE_CLEANUP_FAIL_CLOSED: spec={name} {w}x{h}"
                    f" reason={_scene_result.failure_reason}"
                )
            result_img = _scene_result.scene_plate_image
            if result_img.size != (w, h):
                result_img = result_img.resize((w, h), Image.LANCZOS)
        else:
            # Legacy: Bundle A background plate + Source Faithful Repair
            _bg_plate_img: object = None
            _bg_plate_removal_mask: object = None
            if psd_layers_classified and source_type == "psd":
                try:
                    from background.background_plate_builder import build_background_plate
                    _bg_plate_result = build_background_plate(
                        source_composite=img,
                        psd_layers=psd_layers_classified,
                        canvas_w=psd_canvas_w,
                        canvas_h=psd_canvas_h,
                        render_context=render_ctx,
                    )
                    if _bg_plate_result.success:
                        _bg_plate_img = _bg_plate_result.image
                        _bg_plate_removal_mask = _bg_plate_result.foreground_removal_mask
                        render_ctx.save_debug_artifact("02-background-plate", _bg_plate_result.image)
                        print(
                            f"[BACKGROUND_PLATE_OK] jobId={jid} specId={spec_id}"
                            f" strategy={_bg_plate_result.strategy!r}"
                            f" sha256={_bg_plate_result.background_pixel_sha256[:16]}"
                            f" excludedFg={len(_bg_plate_result.excluded_layer_ids)}",
                            flush=True,
                        )
                    else:
                        raise RuntimeError(
                            f"BACKGROUND_PLATE_BUILD_FAILED:"
                            f" reason={_bg_plate_result.failure_reason}"
                            f" warnings={_bg_plate_result.warnings}"
                        )
                except RuntimeError:
                    raise
                except Exception as _bp_err:
                    raise RuntimeError(f"BACKGROUND_PLATE_BUILD_FAILED: {_bp_err}") from _bp_err

            sfr = run_source_faithful_repair(
                source_image=img,
                classified_layers=psd_layers_classified,
                target_w=w,
                target_h=h,
                provider=provider,
                max_attempts=max_attempts,
                request_id=f"{jid}_{w}x{h}",
                output_dir=_ai_scene_dir,
                render_ctx=render_ctx,
                background_plate=_bg_plate_img,
                background_plate_removal_mask=_bg_plate_removal_mask,
            )
            actual_provider_request_count += sfr.background_ai_attempt_count

            spec_elapsed_ms = int((_time.time() - t_spec) * 1000)
            print(
                f"[AI_SPEC_END] jobId={jid} spec={name} size={w}x{h}"
                f" verdict={sfr.verdict} success={sfr.success}"
                f" provider={sfr.background_ai_provider} attempts={sfr.background_ai_attempt_count}"
                f" faithfulness={sfr.source_faithfulness_score:.1f}"
                f" elapsedMs={spec_elapsed_ms}",
                flush=True,
            )

            if not sfr.success or sfr.repair_image is None:
                raise RuntimeError(
                    f"AI_ONLY_RENDERING fail_closed: spec={name} {w}x{h}"
                    f" reason={sfr.failure_reason} verdict={sfr.verdict}"
                )

            result_img = sfr.repair_image
            if result_img.size != (w, h):
                result_img = result_img.resize((w, h), Image.LANCZOS)

        # Stage 21: Foreground compositor — place product/logo/text/CTA over AI background
        # P0: record AI background sha256 before foreground compositing
        if _bg_mode == "semantic_scene_cleanup":
            if _scene_result is not None and _scene_result.scene_plate_image is not None:
                render_ctx.record_ai_background(_scene_result.scene_plate_image)
        else:
            if sfr is not None and sfr.repair_image is not None:
                render_ctx.record_ai_background(sfr.repair_image)

        fg_result = None
        layout_plan = None
        _active_fg_layers: list = []     # populated by PSD or D-2 path
        _decorative_policy_report: dict = {}  # D-3: populated by decorative_policy

        # D-2: scale virtual fg_layers to this spec's target dimensions
        _virtual_fg_for_spec: list = []
        if (
            _d2_result is not None
            and _d2_result.success
            and _d2_result.d2_applicable
            and _d2_result.fg_layers
        ):
            try:
                from virtual_foreground.manifest_assembler import scale_virtual_fg_layers
                _virtual_fg_for_spec = scale_virtual_fg_layers(
                    _d2_result.fg_layers,
                    source_w=img.width,
                    source_h=img.height,
                    target_w=w,
                    target_h=h,
                )
            except Exception as _scale_err:
                print(
                    f"[D2_SCALE] failed spec={name}: {_scale_err}",
                    flush=True,
                )
                _virtual_fg_for_spec = []

        if psd_layers_classified:
            try:
                from foreground.layer_extractor import extract_foreground_layers
                from foreground.compositor import composite_foreground
                fg_layers = extract_foreground_layers(
                    psd_layers=psd_layers_classified,
                    canvas_w=psd_canvas_w,
                    canvas_h=psd_canvas_h,
                    target_w=w,
                    target_h=h,
                )

                # D-3: Decorative grouping and composition ownership policy.
                # Independent decorative layers are excluded from compositor.
                # title/CTA/logo-grouped decorative become group_child (not composited standalone).
                _decorative_policy_report: dict = {}
                try:
                    from foreground.decorative_policy import apply_decorative_policy
                    fg_layers, _decorative_policy_report = apply_decorative_policy(
                        fg_layers, canvas_w=psd_canvas_w, canvas_h=psd_canvas_h, job_id=jid
                    )
                except Exception as _dp_err:
                    print(f"[STAGE21] decorative_policy error spec={name}: {_dp_err}", flush=True)

                # Bundle B: deterministic reflow — plan safe-zone-aware positions
                # before compositing. Updates fg_layer["bbox"] in-place so the
                # compositor just pastes at planned coords without re-computing.
                try:
                    from layout.reflow_engine import plan_foreground_layout
                    layout_plan = plan_foreground_layout(
                        fg_layers=fg_layers,
                        spec=spec,
                        canvas_w=psd_canvas_w,
                        canvas_h=psd_canvas_h,
                        target_w=w,
                        target_h=h,
                        psd_layers=psd_layers_classified,
                        apply_logs=_object_map_apply_logs,
                        job_id=jid,
                        spec_id=spec_id,
                    )
                    render_ctx.save_debug_artifact(
                        "10-layout-plan",
                        {"selectedCandidateId": layout_plan.selectedCandidateId,
                         "success": layout_plan.success,
                         "safeZoneRect": layout_plan.safeZoneRect,
                         "safeZoneViolationCount": layout_plan.safeZoneViolationCount,
                         "allRequiredObjectsPlaced": layout_plan.allRequiredObjectsPlaced,
                         "hardFailReasons": layout_plan.hardFailReasons},
                    )
                except Exception as _lp_err:
                    print(f"[STAGE21] layout planner error spec={name}: {_lp_err}", flush=True)
                    layout_plan = None

                fg_result = composite_foreground(
                    result_img, fg_layers, job_id=jid, spec_id=spec_id
                )
                if fg_result.success and fg_result.composite_image is not None:
                    result_img = fg_result.composite_image
                    _active_fg_layers = fg_layers
                    print(
                        f"[STAGE21] foreground composited spec={name} {w}x{h}"
                        f" placed={fg_result.placed_roles}",
                        flush=True,
                    )
            except Exception as _fg_err:
                print(f"[STAGE21] foreground compositor error spec={name}: {_fg_err}", flush=True)
                fg_result = None
                layout_plan = None

        # D-2: Virtual foreground compositor path (flattened inputs without native PSD layers)
        elif _virtual_fg_for_spec:
            try:
                from foreground.compositor import composite_foreground
                from layout.reflow_engine import plan_foreground_layout

                # Bundle B reflow for virtual fg_layers
                try:
                    layout_plan = plan_foreground_layout(
                        fg_layers=_virtual_fg_for_spec,
                        spec=spec,
                        canvas_w=img.width,
                        canvas_h=img.height,
                        target_w=w,
                        target_h=h,
                        psd_layers=[],
                        apply_logs=[],
                        job_id=jid,
                        spec_id=spec_id,
                    )
                    render_ctx.save_debug_artifact(
                        "10-layout-plan",
                        {
                            "selectedCandidateId": layout_plan.selectedCandidateId,
                            "success": layout_plan.success,
                            "safeZoneRect": layout_plan.safeZoneRect,
                            "safeZoneViolationCount": layout_plan.safeZoneViolationCount,
                            "allRequiredObjectsPlaced": layout_plan.allRequiredObjectsPlaced,
                            "hardFailReasons": layout_plan.hardFailReasons,
                        },
                    )
                except Exception as _vfg_lp_err:
                    print(
                        f"[D2] layout planner error spec={name}: {_vfg_lp_err}",
                        flush=True,
                    )
                    layout_plan = None

                fg_result = composite_foreground(
                    result_img, _virtual_fg_for_spec, job_id=jid, spec_id=spec_id
                )
                if fg_result.success and fg_result.composite_image is not None:
                    result_img = fg_result.composite_image
                    _active_fg_layers = _virtual_fg_for_spec
                    print(
                        f"[D2] virtual foreground composited spec={name} {w}x{h}"
                        f" placed={fg_result.placed_roles}",
                        flush=True,
                    )
            except Exception as _vfg_err:
                print(
                    f"[D2] virtual fg compositor error spec={name}: {_vfg_err}",
                    flush=True,
                )
                fg_result = None
                layout_plan = None

        # P0: record final artifact SHA-256 and save debug artifact before format conversion
        render_ctx.record_final_artifact(result_img)
        render_ctx.save_debug_artifact("06-final", result_img)

        # D-1: Resolve mode-agnostic AI provider name for C-1 verdict
        _ai_provider_name = (
            (_scene_result.provider_name if _scene_result else "")
            or (sfr.background_ai_provider if sfr else "")
            or ""
        )

        # Bundle C-1: Stage21 verdict pipeline
        _verdict_summary = None
        _verdict_manifest = None
        try:
            from verdict.manifest_builder import build_manifest_from_fg_layers
            from verdict.technical_evaluator import evaluate_technical
            from verdict.extraction_evaluator import evaluate_extraction
            from verdict.composition_evaluator import evaluate_composition
            from verdict.layout_evaluator import evaluate_layout
            from verdict.stage21_aggregator import aggregate_stage21_verdict
            from verdict.serializer import serialize_verdict_summary, extract_provenance_fields
            from verdict.models import VerdictResult

            # D-2: determine manifest source type and fg_layers input
            _d2_active = bool(
                _d2_result and _d2_result.success and _d2_result.d2_applicable
                and _active_fg_layers
            )
            _manifest_source_type = (
                "psd_layer" if psd_layers_classified
                else ("ai_segmentation" if _d2_active else "unknown")
            )
            _verdict_manifest = build_manifest_from_fg_layers(
                _active_fg_layers,
                source_type=_manifest_source_type,
                job_id=jid, spec_id=spec_id,
            )

            _tech_verdict = evaluate_technical(
                output_path=None,      # evaluated after file write below
                output_size=None,      # placeholder — updated after write
                file_size=0,
                target_w=w, target_h=h,
                ai_provider=_ai_provider_name,
                fail_closed=True,
                exception_occurred=False,
                blurFillUsed=False,
                forcedSmartFit=False,
                job_id=jid, spec_id=spec_id,
            )
            # Mark as pending — will be finalized after file write
            _tech_verdict_pending = True

            # D-2: if D-2 succeeded, d2_required is resolved (not a failure)
            _d2_required_for_extraction = (
                (_scene_result.d2_required if _scene_result else False)
                and not _d2_active
            )
            _ext_verdict = evaluate_extraction(
                _verdict_manifest,
                source_type=_manifest_source_type,
                d2_required=_d2_required_for_extraction,
                job_id=jid, spec_id=spec_id,
            )
            _comp_verdict = evaluate_composition(
                fg_result, _verdict_manifest,
                source_type=_manifest_source_type,
                job_id=jid, spec_id=spec_id,
            )
            _layout_verdict = evaluate_layout(
                layout_plan,
                source_type=_manifest_source_type,
                safe_zone_status=spec.get("safeZoneParseStatus", ""),
                job_id=jid, spec_id=spec_id,
            )
            _visual_verdict = VerdictResult(
                name="visualVerdict", status="NOT_TESTED", required=False,
                reasonCodes=["VISUAL_NOT_TESTED"],
                messages=["C-1: visual quality assessment not implemented"],
            )
        except Exception as _vp_err:
            print(f"[STAGE21] verdict pipeline error spec={name}: {_vp_err}", flush=True)
            _verdict_summary = None
            _verdict_manifest = None
            _tech_verdict_pending = False

        if output_format in ("jpg", "jpeg"):
            result_img = result_img.convert("RGB")
            ext = "jpg"
        else:
            ext = output_format

        slug_part = f"_{slug}" if slug else ""
        filename = f"{media}{slug_part}_{w}x{h}.{ext}"
        out_path = os.path.join(output_dir, filename)
        os.makedirs(output_dir, exist_ok=True)
        result_img.save(out_path)

        file_size = os.path.getsize(out_path)
        with Image.open(out_path) as check_img:
            actual_w, actual_h = check_img.size
        valid = (actual_w == w and actual_h == h)

        # Bundle C-1: finalize technical verdict + aggregate
        if _verdict_manifest is not None and locals().get("_tech_verdict_pending"):
            try:
                _tech_verdict = evaluate_technical(
                    output_path=out_path,
                    output_size=(actual_w, actual_h),
                    file_size=file_size,
                    target_w=w, target_h=h,
                    ai_provider=_ai_provider_name,
                    fail_closed=True,
                    exception_occurred=False,
                    blurFillUsed=False,
                    forcedSmartFit=False,
                    background_generation_mode=_bg_mode,
                    provider_input_source=(
                        _scene_result.provider_input_source if _scene_result
                        else "background_plate"
                    ),
                    scene_plate_sha256=(
                        _scene_result.scene_plate_sha256 if _scene_result else ""
                    ),
                    background_plate_builder_used=(_bg_mode == "source_faithful_repair"),
                    legacy_repair_mask_used=False,
                    foreground_bbox_mask_used=False,
                    job_id=jid, spec_id=spec_id,
                )
                _verdict_summary = aggregate_stage21_verdict(
                    _tech_verdict, _ext_verdict, _comp_verdict,
                    _layout_verdict, _visual_verdict,
                    job_id=jid, spec_id=spec_id,
                )
                render_ctx.save_debug_artifact(
                    "23-stage21-verdict-summary",
                    serialize_verdict_summary(_verdict_summary),
                )
            except Exception as _vagg_err:
                print(f"[STAGE21] verdict aggregation error: {_vagg_err}", flush=True)
                _verdict_summary = None

        # D-1: mode-specific result fields
        if _bg_mode == "semantic_scene_cleanup":
            _bg_result_mode = {
                "actualPsdRenderMode": "ai-semantic-scene-cleanup",
                "resizeStrategy": "full-image-semantic-scene-cleanup",
                "backgroundMode": "semantic_scene_cleanup",
                "layoutScore": None,
                "candidateCount": 1,
                "selectedCandidateId": (
                    layout_plan.selectedCandidateId if layout_plan
                    else (f"ssc:{_scene_result.prompt_version}" if _scene_result else None)
                ),
                "bestEvaluatedBackgroundSource": "full_composite",
                "appliedBackgroundSource": "full_composite",
                "appliedBackgroundScore": None,
                "externalInpaintAttempted": bool(
                    _scene_result and _scene_result.actual_provider_request_count > 0
                ),
                "outpaintAttempted": bool(
                    _scene_result and _scene_result.canvas_transform
                    and _scene_result.canvas_transform.outpaint_required
                ),
            }
        else:
            _bg_result_mode = {
                "actualPsdRenderMode": "ai-source-faithful-repair",
                "resizeStrategy": "source-faithful-ai-repair",
                "backgroundMode": "source_faithful_repair",
                "layoutScore": sfr.overall_repair_score if sfr else None,
                "candidateCount": sfr.background_ai_candidate_count if sfr else 0,
                "selectedCandidateId": (
                    layout_plan.selectedCandidateId if layout_plan
                    else (f"sfr:{sfr.background_ai_provider}" if sfr else None)
                ),
                "bestEvaluatedBackgroundSource": sfr.applied_background_source if sfr else "",
                "appliedBackgroundSource": sfr.applied_background_source if sfr else "",
                "appliedBackgroundScore": sfr.source_faithfulness_score if sfr else 0,
                "externalInpaintAttempted": sfr.background_ai_executed if sfr else False,
                "outpaintAttempted": sfr.outpaint_mask_ratio > 0 if sfr else False,
            }

        # D-1: mode-specific renderProvenance fields
        if _bg_mode == "semantic_scene_cleanup":
            from scene_cleanup.serializer import extract_d1_provenance_fields as _ext_d1_prov
            _prov_mode = {
                "effectiveRenderer": "semantic-scene-cleanup",
                "selectedMode": "ai-semantic-scene",
                "backgroundGenerationMode": "semantic_scene_cleanup",
                "backgroundAiExecuted": bool(
                    _scene_result and _scene_result.actual_provider_request_count > 0
                ),
                "backgroundAiProvider": _scene_result.provider_name if _scene_result else "",
                "backgroundAiModel": _scene_result.provider_model if _scene_result else "",
                "backgroundAiSucceeded": _scene_result.success if _scene_result else False,
                "backgroundAiAttemptCount": _scene_result.attempt_count if _scene_result else 0,
                "sourceFaithfulnessScore": None,
                "sourceFaithfulRepairUsed": False,
                "semanticSceneCleanupUsed": True,
                "visibleHandMutationCount": 0,
                **(_ext_d1_prov(_scene_result) if _scene_result else {}),
            }
        else:
            _prov_mode = {
                "effectiveRenderer": "source-faithful-ai-repair",
                "selectedMode": "ai-source-faithful",
                "backgroundGenerationMode": "source_faithful_repair",
                "backgroundAiExecuted": sfr.background_ai_executed if sfr else False,
                "backgroundAiProvider": sfr.background_ai_provider if sfr else "",
                "backgroundAiModel": sfr.background_ai_model if sfr else "",
                "backgroundAiSucceeded": sfr.background_ai_succeeded if sfr else False,
                "backgroundAiAttemptCount": sfr.background_ai_attempt_count if sfr else 0,
                "sourceFaithfulnessScore": sfr.source_faithfulness_score if sfr else 0,
                "sourceFaithfulRepairUsed": True,
                "semanticSceneCleanupUsed": False,
                "visibleHandMutationCount": sfr.visible_hand_mutation_count if sfr else 0,
            }

        # D-2: virtual foreground provenance fields
        try:
            from virtual_foreground.serializer import extract_d2_provenance_fields as _ext_d2_prov
            _d2_prov_fields = _ext_d2_prov(_d2_result)
        except Exception:
            _d2_prov_fields = {}

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
            "validationMessage": "정상" if valid else f"expected={w}x{h} actual={actual_w}x{actual_h}",
            "selectedArtboardId": None,
            "selectedArtboardName": None,
            "renderSource": source_meta.get("renderSource", "unknown"),
            "fallbackUsed": source_meta.get("fallbackUsed", False),
            "fallbackReason": source_meta.get("fallbackReason"),
            "fallbackErrors": source_meta.get("fallbackErrors", []),
            "sourceWidth": source_meta.get("sourceWidth"),
            "sourceHeight": source_meta.get("sourceHeight"),
            "layerReflowAttempted": fg_result is not None,
            "layerReflowSucceeded": fg_result is not None and fg_result.success,
            "layerReflowError": None,
            "layerReflowExtractedLayerCount": fg_result.layer_count if fg_result else 0,
            "layerReflowDetectedRoles": fg_result.placed_roles if fg_result else [],
            "layerReflowTemplate": (
                f"bundle-b-{layout_plan.selectedCandidateId}" if layout_plan and layout_plan.success
                else ("deterministic-original-positions" if fg_result and fg_result.success else None)
            ),
            "usedLayerRoles": fg_result.placed_roles if fg_result else [],
            "candidateType": layout_plan.selectedCandidateId if layout_plan else None,
            "candidateScore": None,
            "blurAreaRatio": None,
            "cropRatio": None,
            "subjectScale": None,
            "qualityGate": None,
            "qualityLabel": None,
            "safeZonePass": (
                layout_plan.safeZoneViolationCount == 0 if layout_plan else None
            ),
            "safeZoneViolations": (
                layout_plan.hardFailReasons if layout_plan else []
            ),
            "requiredLayerMissing": (
                not layout_plan.allRequiredObjectsPlaced if layout_plan else None
            ),
            "renderMode": "ai-only",
            "objectReflowUsed": layout_plan is not None,
            "objectReflowFallbackUsed": False,
            "backgroundPipelineExecuted": True,
            "backgroundPipelineEnabled": True,
            "backgroundCompareOnly": False,
            "backgroundFallbackUsed": False,
            "backgroundFallbackReason": "",
            "shadowApplied": False,
            **_bg_result_mode,
            "renderProvenance": {
                "renderPolicy": "ai-only",
                "requestedResizeMode": resize_mode,
                "effectiveResizeMode": "ai-auto",
                "blurFillUsed": False,
                "forcedSmartFit": False,
                "sourceType": source_type,
                "psdMode": psd_mode if source_type == "psd" else None,
                "backgroundPipelineUsed": True,
                "failClosed": True,
                # D-1: mode-specific provenance (effectiveRenderer, backgroundGenerationMode, etc.)
                **_prov_mode,
                # Stage 21: Foreground compositor provenance
                "foregroundCompositor": "deterministic-layer-compositor" if fg_result and fg_result.success else None,
                "productPlaced": fg_result.product_placed if fg_result else False,
                "logoPlaced": fg_result.logo_placed if fg_result else False,
                "headlinePlaced": fg_result.headline_placed if fg_result else False,
                "bodyTextPlaced": fg_result.body_text_placed if fg_result else False,
                "ctaPresent": bool(psd_layers_classified and any(l.get("role") == "cta" for l in psd_layers_classified)),
                "ctaPlaced": fg_result.cta_placed if fg_result else False,
                "humanSubjectPreserved": fg_result.human_subject_preserved if fg_result else False,
                "backgroundCacheHit": False,
                # Bundle B: deterministic reflow provenance
                "layoutPlanUsed": layout_plan is not None,
                "layoutPlanSuccess": layout_plan.success if layout_plan else False,
                "layoutSelectedCandidate": layout_plan.selectedCandidateId if layout_plan else None,
                "layoutSafeZoneAvailable": layout_plan.safeZoneAvailable if layout_plan else False,
                "layoutSafeZoneEnforced": layout_plan.safeZoneEnforced if layout_plan else False,
                "layoutSafeZoneViolationCount": layout_plan.safeZoneViolationCount if layout_plan else 0,
                "layoutAllRequiredObjectsPlaced": layout_plan.allRequiredObjectsPlaced if layout_plan else False,
                "layoutAllObjectsCompositedOnce": (
                    layout_plan.allObjectsCompositedOnce if layout_plan
                    else (fg_result.all_objects_composited_once if fg_result else False)
                ),
                "layoutNoDuplicateComposition": (
                    layout_plan.noDuplicateComposition if layout_plan
                    else (fg_result.duplicate_count == 0 if fg_result else True)
                ),
                "layoutHardFailReasons": layout_plan.hardFailReasons if layout_plan else [],
                # D-3: routing and policy provenance
                "backgroundModeSource": "explicit" if _bg_mode_raw else "default",
                "semanticDefaultApplied": _bg_mode_default_applied,
                "legacyRollbackExplicit": _bg_mode_explicit_rollback,
                "legacyFallbackUsed": False,
                "decorativeDetectedCount": _decorative_policy_report.get("detectedCount", 0),
                "decorativeGroupedCount": _decorative_policy_report.get("groupedCount", 0),
                "decorativeExcludedCount": _decorative_policy_report.get("excludedCount", 0),
                "decorativeCompositionCount": _decorative_policy_report.get("compositionCount", 0),
                "excludedDecorativeObjectIds": _decorative_policy_report.get("excludedObjectIds", []),
                "groupedDecorativeObjectIds": _decorative_policy_report.get("groupedObjectIds", []),
                # D-2: virtual foreground provenance (computed before this block)
                **_d2_prov_fields,
                # P0: SHA-256 provenance fields for cross-job audit
                "sourceFileSha256": render_ctx.source_file_sha256[:16] if render_ctx.source_file_sha256 else "",
                "compositeSha256": render_ctx.composite_sha256[:16] if render_ctx.composite_sha256 else "",
                "providerInputSha256": render_ctx.provider_input_sha256[:16] if render_ctx.provider_input_sha256 else "",
                "aiBackgroundSha256": render_ctx.ai_background_sha256[:16] if render_ctx.ai_background_sha256 else "",
                "finalArtifactSha256": render_ctx.final_artifact_sha256[:16] if render_ctx.final_artifact_sha256 else "",
                "workDir": render_ctx.work_dir,
                # Bundle C-1: structured verdict fields (overrides legacy verdict string)
                **({} if _verdict_summary is None else extract_provenance_fields(
                    _verdict_summary, _verdict_manifest
                )),
                # verdict field: derived from overallVerdict (C-1) or mode-specific
                "verdict": (
                    _verdict_summary.overallStatus if _verdict_summary is not None
                    else (sfr.verdict if sfr else (
                        "success" if (_scene_result and _scene_result.success) else "fail"
                    ))
                ),
            },
        })

    total_elapsed_ms = int((_time.time() - t_all) * 1000)
    print(
        f"[AI_ONLY_END] jobId={jid} elapsedMs={total_elapsed_ms}"
        f" successCount={len(results)} specCount={len(specs)}"
        f" actualProviderRequestCount={actual_provider_request_count}",
        flush=True,
    )
    return results, []


def generate_candidates(input_path: str, output_dir: str, spec: dict,
                        resize_mode: str = "smart-fit", focal_position: str = "center",
                        strengths: list = None, detected_elements: list = None,
                        required_groups: list = None, priority_groups: list = None,
                        content_bands: list = None) -> tuple[str, list]:
    if strengths is None:
        strengths = ["safe", "balanced", "fill"]

    os.makedirs(output_dir, exist_ok=True)
    img, _ = load_source_image(input_path)

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
             psd_mode: str = "artboard-first",
             selected_artboard_ids: list[str] | None = None,
             object_reflow_enabled: bool = False,
             object_analysis: dict | None = None,
             job_id: str | None = None) -> tuple[list[dict], list[str]]:
    """returns (results, missingRatioTypes)"""

    os.makedirs(output_dir, exist_ok=True)

    # Stage 20.3 AI-only routing gate: resize_mode=='ai-auto' OR AI_ONLY_RENDERING=true
    if resize_mode == "ai-auto" or os.environ.get("AI_ONLY_RENDERING", "false").lower() == "true":
        return _generate_ai_only(
            psd_path, specs, resize_mode, output_format, output_dir,
            smart_fit_strength, focal_position, source_type, psd_mode,
            selected_artboard_ids, object_reflow_enabled, object_analysis, job_id
        )
    results = []

    # 4차-9: 객체 기반 재배치 모드 (OR 조건: objectReflowEnabled OR psdMode=="object-reflow")
    if source_type == "psd" and (object_reflow_enabled or psd_mode == "object-reflow"):
        import psd_tools
        import tempfile
        from psd_layer_parser import parse_psd_layers
        from creative_object_extractor import build_creative_object_set
        from background_builder import build_background
        from layout_compiler import compile_layout
        from layout_compositor import composite_layout
        from safe_zone import normalize_safe_zone

        ai_objects = (object_analysis or {}).get("objects", [])
        artboard_box = (object_analysis or {}).get("artboardBox")
        canvas_w = int((object_analysis or {}).get("canvasWidth") or 0)
        canvas_h = int((object_analysis or {}).get("canvasHeight") or 0)

        if not ai_objects:
            print(f"[{job_id or 'job'}][ObjectReflow] AI 분석 없음 - artboard smart-fit fallback 예정")

        creative_object_set = None
        try:
            psd = psd_tools.PSDImage.open(psd_path)
            if canvas_w <= 0:
                canvas_w = psd.width
            if canvas_h <= 0:
                canvas_h = psd.height

            # 아트보드 합성 이미지 추출
            composite_full = psd.composite()
            if artboard_box:
                ax = int(artboard_box.get("x", 0))
                ay = int(artboard_box.get("y", 0))
                aw = int(artboard_box.get("width", canvas_w))
                ah = int(artboard_box.get("height", canvas_h))
                artboard_img = composite_full.crop((ax, ay, ax + aw, ay + ah)).convert("RGBA")
            else:
                artboard_img = composite_full.convert("RGBA")

            tmp_dir = tempfile.mkdtemp()
            psd_layers = parse_psd_layers(psd, tmp_dir)

            # 6단계: CreativeObjectSet 빌드 (per-PSD 한 번)
            asset_dir = os.path.join(output_dir, "assets")
            creative_object_set = build_creative_object_set(
                psd_path, psd_layers, object_analysis,
                asset_dir, artboard_img, artboard_box, job_id
            )
            n_objs   = len(creative_object_set.get("objects", []))
            n_assets = sum(1 for o in creative_object_set.get("objects", []) if o.get("imagePath"))
            print(f"[{job_id or 'job'}][ObjectReflow] CreativeObjectSet: {n_objs} objs, {n_assets} assets")
        except Exception as e:
            print(f"[ObjectReflow] PSD 로딩/분석 실패, fallback: {e}")
            artboard_img = None
            creative_object_set = None

        # ── Stage 16 PoC: segmentation (PSD당 1회, spec loop 외부) ──────────────
        # Flag: CREATIVE_SEGMENTATION_POC=true 또는 request.experimentalSegmentation=true
        # 기존 경로에 영향 없음 — 실패해도 masks=[] 로 처리
        _poc_extra_flags = {
            "experimentalSegmentation": bool((object_analysis or {}).get("experimentalSegmentation")),
            "experimentalInpaint":      bool((object_analysis or {}).get("experimentalInpaint")),
            "experimentalOutpaint":     bool((object_analysis or {}).get("experimentalOutpaint")),
        }
        _poc_masks: list = []
        _poc_seg_meta: dict = {}
        _poc_seg_on = (
            os.environ.get("CREATIVE_SEGMENTATION_POC", "false").lower() == "true"
            or _poc_extra_flags["experimentalSegmentation"]
        )
        if _poc_seg_on and creative_object_set:
            try:
                from segmentation_poc import run_segmentation_poc
                _poc_masks, _poc_seg_meta = run_segmentation_poc(
                    creative_object_set, artboard_img, canvas_w, canvas_h,
                    output_dir, job_id, extra_flags=_poc_extra_flags,
                )
            except Exception as _seg_e:
                print(f"[{job_id or 'job'}][SegPoc] import/run failed: {_seg_e}")
                _poc_seg_meta = {"segmentationPocEnabled": True, "maskFallbackUsed": True,
                                 "maskWarnings": [str(_seg_e)]}
        # ── end Stage 16 PoC ────────────────────────────────────────────────────

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

            obj_reflow_succeeded = False
            obj_reflow_error = None
            fallback_img = None
            obj_sz_passed = None
            obj_sz_violations = []
            bg_meta_out = {}
            layout_meta_out = {}
            comp_meta_out = {}
            _is_emergency = False
            layout_score_status = None
            effective_layout_score = None
            _poc_inpaint_meta: dict = {}   # Stage 17 PoC — 항상 초기화
            safe_zones = normalize_safe_zone(spec, w, h)

            print(f"[{job_id or 'job'}][ObjectReflow] spec={name} size={w}x{h}")

            # 6단계: background + layout candidates + composite 통합 경로
            if artboard_img and creative_object_set and creative_object_set.get("objects"):
                try:
                    # 1. blur-free background
                    bg_img, bg_meta_out = build_background(
                        creative_object_set, artboard_img, w, h, output_dir, job_id
                    )

                    # 2a. Stage 17 PoC: inpaint (flag 켜진 경우만, 실패 시 기존 bg 유지)
                    _poc_inpaint_meta: dict = {}
                    _poc_inpaint_on = (
                        os.environ.get("CREATIVE_INPAINT_POC", "false").lower() == "true"
                        or _poc_extra_flags.get("experimentalInpaint")
                    )
                    if _poc_inpaint_on and _poc_masks:
                        try:
                            from inpaint_outpaint_poc import run_inpaint_poc
                            bg_img, _poc_inpaint_meta = run_inpaint_poc(
                                bg_img, _poc_masks,
                                canvas_w, canvas_h, w, h,
                                output_dir, job_id, extra_flags=_poc_extra_flags,
                            )
                            if _poc_inpaint_meta.get("inpaintApplied"):
                                _nat = bg_meta_out.get("backgroundNaturalnessScore", 70.0)
                                bg_meta_out["backgroundNaturalnessScore"] = round(
                                    min(90.0, _nat + 5.0), 1
                                )
                                _orig_bg_mode = bg_meta_out.get("backgroundMode", "unknown")
                                bg_meta_out["baseBackgroundMode"] = _orig_bg_mode
                                bg_meta_out["pocBackgroundApplied"] = True
                                bg_meta_out["pocBackgroundMode"] = "poc_inpainted"
                                bg_meta_out["backgroundProcessingMode"] = (
                                    "poc_inpainted_" + _orig_bg_mode
                                )
                                # backgroundMode kept as-is (original value preserved)
                        except Exception as _ip_e:
                            _poc_inpaint_meta = {
                                "inpaintPocEnabled": True, "inpaintApplied": False,
                                "inpaintFallbackUsed": True,
                                "warnings": [f"inpaintImportFailed:{_ip_e}"],
                            }
                            bg_meta_out.setdefault("warnings", []).append(
                                f"stage17InpaintFailed:{_ip_e}"
                            )

                    # 2b. Stage 16+17 debug JSON 저장
                    if _poc_seg_on and output_dir:
                        try:
                            import json as _json
                            _s16_17 = {
                                "segmentationPocEnabled": _poc_seg_meta.get("segmentationPocEnabled", False),
                                "inpaintPocEnabled":  _poc_inpaint_meta.get("inpaintPocEnabled", False),
                                "outpaintPocEnabled": False,
                                "masksGenerated":     _poc_seg_meta.get("masksGenerated", 0),
                                "productMaskSelected": _poc_seg_meta.get("productMaskSelected", False),
                                "productMaskId":      _poc_seg_meta.get("productMaskId"),
                                "maskQualityScore":   _poc_seg_meta.get("maskQualityScore", 0.0),
                                "cleanBackgroundUsed": _poc_inpaint_meta.get("cleanBackgroundUsed", False),
                                "inpaintApplied":     _poc_inpaint_meta.get("inpaintApplied", False),
                                "outpaintApplied":    False,
                                "backgroundQualityScore": bg_meta_out.get("backgroundNaturalnessScore", 0.0),
                                "fallbackUsed":       _poc_inpaint_meta.get("inpaintFallbackUsed", True),
                                "warnings": (
                                    _poc_seg_meta.get("maskWarnings", [])
                                    + _poc_inpaint_meta.get("warnings", [])
                                ),
                            }
                            _json_path = os.path.join(output_dir, "result.stage16_17.json")
                            with open(_json_path, "w", encoding="utf-8") as _jf:
                                _json.dump(_s16_17, _jf, indent=2)
                        except Exception:
                            pass

                    # 2c. 기존 layout candidates 생성 (Stage 15: bg naturalness 전달)
                    layout_result = compile_layout(
                        creative_object_set, w, h, safe_zones,
                        bg_naturalness=bg_meta_out.get("backgroundNaturalnessScore"),
                    )
                    layout_meta_out = layout_result.get("metadata", {})
                    if layout_meta_out.get("fallbackUsed"):
                        print(f"[{job_id or 'job'}][ObjectReflow] emergency layout spec={name}")

                    # 3. z-order object asset compositing
                    final_img, comp_meta_out = composite_layout(
                        bg_img, bg_meta_out, layout_result,
                        creative_object_set, w, h, output_dir, job_id,
                        artboard_img=artboard_img,
                        artboard_box=artboard_box,
                    )

                    _missing = comp_meta_out.get("missingRequiredAssets", [])
                    _rendered = comp_meta_out.get("renderedRoles", [])
                    if _missing:
                        # product/로고가 누락됐더라도 텍스트가 렌더됐으면 부분 결과 허용
                        # 아무것도 렌더되지 않은 경우에만 smart-fit fallback
                        if not _rendered:
                            raise ValueError(
                                f"required assets missing and nothing rendered: {_missing}"
                            )
                        print(
                            f"[{job_id or 'job'}][ObjectReflow] WARN partial result "
                            f"spec={name}: missing={_missing} rendered={_rendered}"
                        )
                        comp_meta_out.setdefault("warnings", []).append(
                            f"partialResult: missing={_missing}"
                        )

                    debug_ref_img = final_img  # RGBA reference (before RGB conversion)
                    if ext in ("jpg", "jpeg"):
                        final_img = final_img.convert("RGB")
                    final_img.save(out_path)
                    obj_reflow_succeeded = True
                    obj_sz_passed = comp_meta_out.get("safeZonePassed")
                    obj_sz_violations = comp_meta_out.get("safeZoneViolations", [])
                    _is_emergency = layout_meta_out.get("selectedCandidateId") == "emergency_fallback"
                    layout_score_status = "fallback" if _is_emergency else "normal"
                    effective_layout_score = None if _is_emergency else layout_meta_out.get("layoutScore")
                    print(
                        f"[{job_id or 'job'}][ObjectReflow] success spec={name} size={w}x{h} "
                        f"candidate={layout_meta_out.get('selectedCandidateId')} "
                        f"score={effective_layout_score} status={layout_score_status}"
                    )
                    # 7단계: debug overlay (실패해도 원본 무영향)
                    try:
                        from debug_overlay import generate_debug_files as _gen_debug
                        _gen_debug(
                            debug_ref_img, out_path,
                            comp_meta_out, layout_result,
                            creative_object_set, safe_zones, w, h,
                            render_source="psd_object_reflow",
                            actual_render_mode="object-layout-reflow",
                            layout_score_status=layout_score_status,
                            job_id=job_id,
                            spec_info=spec,
                        )
                    except Exception as _oe:
                        comp_meta_out.setdefault("warnings", []).append(
                            f"debugOverlayFailed: {_oe}"
                        )
                except Exception as e:
                    obj_reflow_error = str(e)
                    obj_sz_passed = False
                    print(f"[{job_id or 'job'}][ObjectReflow] fallback spec={name} size={w}x{h} reason={e}")

            if not obj_reflow_succeeded:
                # fallback: artboard_img → smart-fit
                if artboard_img:
                    fallback_img = artboard_img
                    obj_reflow_error = obj_reflow_error or "creative_object_set 없음 또는 asset 추출 실패"
                else:
                    import psd_analyzer
                    analysis_fb = psd_analyzer.analyze_psd_file(psd_path)
                    best_ab = psd_analyzer.select_best_artboard(analysis_fb["artboards"], w, h)
                    if best_ab and best_ab.get("id") != "full_canvas":
                        fallback_img, _ = psd_analyzer.safe_render_artboard(psd_path, best_ab)
                    if fallback_img is None:
                        fallback_img, _, _ = psd_analyzer.fallback_flatten_psd(psd_path)
                    if fallback_img is None:
                        fallback_img = Image.new("RGBA", (w, h), (200, 200, 200, 255))

                resized, _ = _apply_resize(fallback_img, w, h, resize_mode or "smart-fit",
                                           smart_fit_strength or "balanced", focal_position or "center")
                if ext in ("jpg", "jpeg"):
                    resized = resized.convert("RGB")
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
                "actualPsdRenderMode": "object-layout-reflow" if obj_reflow_succeeded else "artboard",
                "renderSource": "psd_object_reflow" if obj_reflow_succeeded else "psd_tools_composite",
                "fallbackUsed": not obj_reflow_succeeded,
                "fallbackReason": (
                    "all_candidates_hard_failed" if (obj_reflow_succeeded and _is_emergency)
                    else (obj_reflow_error if not obj_reflow_succeeded else None)
                ),
                "fallbackErrors": [],
                "sourceWidth": artboard_img.width if artboard_img else 0,
                "sourceHeight": artboard_img.height if artboard_img else 0,
                "objectReflowAttempted": True,
                "objectReflowSucceeded": obj_reflow_succeeded,
                "objectReflowMode": layout_meta_out.get("selectedCandidateId") if obj_reflow_succeeded else None,
                "objectReflowFallbackReason": (
                    "all_candidates_hard_failed" if (obj_reflow_succeeded and _is_emergency)
                    else (obj_reflow_error if not obj_reflow_succeeded else None)
                ),
                "usedObjectRoles": comp_meta_out.get("renderedRoles", []) if obj_reflow_succeeded else [],
                "missingObjectRoles": comp_meta_out.get("missingRequiredAssets", []) if obj_reflow_succeeded else [o.get("role") for o in ai_objects],
                "productExpected": (
                    # isProductEvidence=True: caseb_product_isolated 또는 product 키워드 보유 레이어
                    # 사람·장식·행사·area_fallback은 product 증거 아님
                    any(
                        o.get("isProductEvidence", False)
                        for o in (creative_object_set or {}).get("objects", [])
                    )
                ) if obj_reflow_succeeded else bool(ai_objects),
                "productRendered": (
                    any(r in ("main_image", "person") for r in comp_meta_out.get("renderedRoles", []))
                    if obj_reflow_succeeded else False
                ),
                "noProductScenarioDetected": (
                    obj_reflow_succeeded and not any(
                        o.get("isProductEvidence", False)
                        for o in (creative_object_set or {}).get("objects", [])
                    )
                ),
                "productRenderQuality": (
                    "pass" if (
                        obj_reflow_succeeded and
                        any(r in ("main_image", "person") for r in comp_meta_out.get("renderedRoles", [])) and
                        any(
                            o.get("matchStatus") == "caseb_product_isolated" and o.get("imagePath")
                            for o in (creative_object_set or {}).get("objects", [])
                            if o.get("role") in ("main_image", "person")
                        )
                    ) else "partial" if (
                        obj_reflow_succeeded and
                        any(r in ("main_image", "person") for r in comp_meta_out.get("renderedRoles", []))
                    ) else "fail"
                ),
                "compositeFallbackUsed": (
                    obj_reflow_succeeded and any(
                        o.get("matchStatus") in ("caseb_area_fallback", "caseb_area_fallback_scene")
                        for o in (creative_object_set or {}).get("objects", [])
                    )
                ),
                "roleSeparationQuality": (
                    (creative_object_set or {}).get("roleSeparationQuality", "fail")
                    if obj_reflow_succeeded else "fail"
                ),
                "separatedRoles": (
                    (creative_object_set or {}).get("separatedRoles", [])
                    if obj_reflow_succeeded else []
                ),
                "compositeOnlyRoles": (
                    (creative_object_set or {}).get("compositeOnlyRoles", [])
                    if obj_reflow_succeeded else []
                ),
                "ctaMeta": (
                    (creative_object_set or {}).get("ctaMeta", {})
                    if obj_reflow_succeeded else {}
                ),
                "cropFallbackRoles": [],
                "lowConfidenceRoles": [],
                "objectSafeZonePass": obj_sz_passed,
                "resizeStrategy": "psd-object-layout-reflow" if obj_reflow_succeeded else "smart-fit",
                "candidateType": None,
                "candidateScore": effective_layout_score if obj_reflow_succeeded else None,
                "blurAreaRatio": None, "cropRatio": None, "subjectScale": None,
                "qualityGate": None, "qualityLabel": None,
                "safeZonePassed": obj_sz_passed,   # canonical
                "safeZonePass": obj_sz_passed,     # backward-compat alias
                "safeZoneViolations": obj_sz_violations,
                "requiredLayerMissing": None,
                "layerReflowAttempted": False,
                "layerReflowSucceeded": False,
                "layerReflowError": None,
                "layerReflowExtractedLayerCount": 0,
                "layerReflowDetectedRoles": [],
                "layerReflowTemplate": None,
                "usedLayerRoles": [],
                # 1단계 + 6단계: 고품질 경로 메타
                "renderMode": "object-layout-reflow" if obj_reflow_succeeded else "psd_artboard_first",
                "objectReflowUsed": obj_reflow_succeeded,
                "objectReflowFallbackUsed": not obj_reflow_succeeded or _is_emergency,
                "layoutScore": effective_layout_score if obj_reflow_succeeded else None,
                "layoutScoreStatus": layout_score_status if obj_reflow_succeeded else None,
                "backgroundMode": bg_meta_out.get("backgroundMode") if obj_reflow_succeeded else None,
                "candidateCount": layout_meta_out.get("candidateCount", 5) if obj_reflow_succeeded else 0,
                "selectedCandidateId": layout_meta_out.get("selectedCandidateId") if obj_reflow_succeeded else None,
                # safe zone + hard fail + 드롭 + 경고 (모두 optional)
                "hardFailReasons": comp_meta_out.get("hardFailReasons", []) if obj_reflow_succeeded else [],
                "droppedObjects": comp_meta_out.get("droppedObjects", []) if obj_reflow_succeeded else [],
                "warnings": comp_meta_out.get("warnings", []) if obj_reflow_succeeded else [],
                # 9단계: Layout Repair & Quality Meta
                "repairAttempted":         layout_meta_out.get("repairAttempted") if obj_reflow_succeeded else None,
                "repairApplied":           layout_meta_out.get("repairApplied") if obj_reflow_succeeded else None,
                "repairReasons":           layout_meta_out.get("repairReasons", []) if obj_reflow_succeeded else [],
                "repairedObjects":         layout_meta_out.get("repairedObjects", []) if obj_reflow_succeeded else [],
                "scoringBreakdown":        (layout_result.get("topCandidates") or [{}])[0].get("scoringBreakdown") if obj_reflow_succeeded else None,
                "duplicateObjectsRemoved": layout_meta_out.get("duplicateObjectsRemoved") if obj_reflow_succeeded else None,
                "ctaGroupCreated":         layout_meta_out.get("ctaGroupCreated") if obj_reflow_succeeded else None,
                # Stage 16+17 PoC metadata (flag OFF 시 모두 None)
                "segmentationPocEnabled":  _poc_seg_meta.get("segmentationPocEnabled"),
                "masksGenerated":          _poc_seg_meta.get("masksGenerated"),
                "productMaskSelected":     _poc_seg_meta.get("productMaskSelected"),
                "productMaskId":           _poc_seg_meta.get("productMaskId"),
                "maskQualityScore":        _poc_seg_meta.get("maskQualityScore"),
                "inpaintPocEnabled":       _poc_inpaint_meta.get("inpaintPocEnabled"),
                "inpaintApplied":          _poc_inpaint_meta.get("inpaintApplied"),
                "cleanBackgroundUsed":     _poc_inpaint_meta.get("cleanBackgroundUsed"),
                "pocFallbackUsed":         _poc_seg_meta.get("maskFallbackUsed"),
                # Stage 20.3: 렌더 프로브넌스
                "renderProvenance": {
                    "requestedResizeMode": resize_mode,
                    "effectiveResizeMode": "psd-object-layout-reflow" if obj_reflow_succeeded else "smart-fit",
                    "blurFillUsed": not obj_reflow_succeeded,
                    "forcedSmartFit": not obj_reflow_succeeded,
                    "sourceType": "psd",
                    "psdMode": "object-reflow",
                    "backgroundPipelineUsed": False,
                    "sourceFaithfulRepairUsed": False,
                },
            })

            print(
                f"[RENDER_PROVENANCE] spec={name} size={w}x{h}"
                f" sourceType=psd psdMode=object-reflow requestedMode={resize_mode}"
                f" effectiveMode={'psd-object-layout-reflow' if obj_reflow_succeeded else 'smart-fit'}"
                f" blurFill={not obj_reflow_succeeded} objReflowSucceeded={obj_reflow_succeeded}"
            )
        return results, []

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

            # Stage 20: Try Typography Pipeline first when TYPOGRAPHY_PIPELINE_ENABLED=true
            typo_meta: dict = {}
            typography_attempted = False
            lr = None
            if os.environ.get("TYPOGRAPHY_PIPELINE_ENABLED", "false").lower() == "true":
                try:
                    from typography.pipeline import run_typography_pipeline
                    typo_result = run_typography_pipeline(
                        psd_path, w, h, out_path,
                        debug_dir=debug_dir,
                        output_format=output_format,
                        job_id=f"{media}_{w}x{h}",
                    )
                    typography_attempted = True
                    typo_meta = typo_result
                    if typo_result.get("success"):
                        lr = {
                            "success": True,
                            "error": None,
                            "template": typo_result.get("template"),
                            "detectedRoles": typo_result.get("detectedRoles", []),
                            "usedLayerRoles": typo_result.get("usedLayerRoles", []),
                            "extractedLayerCount": typo_result.get("extractedLayerCount", 0),
                            "layerReflowScore": typo_result.get("qualityScore", 0.0),
                            "quality": {
                                "safeZonePass": typo_result.get("safeZonePass", True),
                                "requiredLayerMissing": bool(typo_result.get("missingRoles")),
                                "overlapRisk": False,
                            },
                        }
                except Exception as _typo_exc:
                    print(f"[Resizer] Typography pipeline error: {_typo_exc}")
                    typo_meta = {"error": str(_typo_exc)}

            # Fall back to legacy layer-reflow if Typography Pipeline not enabled/succeeded
            if lr is None:
                lr = psd_layer_reflow.generate_psd_layer_reflow(
                    psd_path, w, h, out_path, debug_dir
                )

            layer_reflow_attempted = True
            layer_reflow_succeeded = lr.get("success", False)
            layer_reflow_error = lr.get("error")
            layer_reflow_extracted_count = lr.get("extractedLayerCount", 0)
            layer_reflow_detected_roles = lr.get("detectedRoles", [])
            flat_meta = None
            ab_img = None

            enhance_meta = {
                "resizeStrategy": None, "candidateType": None, "candidateScore": None,
                "blurAreaRatio": None, "cropRatio": None, "subjectScale": None,
            }
            lr_quality = lr.get("quality", {})
            safe_zone_pass = lr_quality.get("safeZonePass")
            required_layer_missing = lr_quality.get("requiredLayerMissing")

            if layer_reflow_succeeded:
                actual_render_mode = "layer-reflow"
                layer_reflow_template = lr.get("template")
                used_layer_roles = lr.get("usedLayerRoles", [])
                enhance_meta["resizeStrategy"] = "psd-layer-reflow"
                if output_format in ("jpg", "jpeg"):
                    img = Image.open(out_path).convert("RGB")
                    img.save(out_path)
            else:
                # fallback → artboard-first 체인 재사용
                import psd_analyzer
                analysis = psd_analyzer.analyze_psd_file(psd_path)
                best_ab = psd_analyzer.select_best_artboard(analysis["artboards"], w, h)
                is_full_canvas = (best_ab is None or best_ab.get("id") == "full_canvas")

                if not is_full_canvas:
                    ab_img, actual_render_mode = psd_analyzer.safe_render_artboard(psd_path, best_ab)
                if ab_img is None:
                    ab_img, actual_render_mode, flat_meta = psd_analyzer.fallback_flatten_psd(psd_path)
                if ab_img is None:
                    ab_img = Image.new("RGBA", (w, h), (200, 200, 200, 255))
                    actual_render_mode = "failed"

                resized, enhance_meta = _apply_resize(
                    ab_img, w, h, "smart-fit",
                    smart_fit_strength or "balanced",
                    focal_position or "center",
                )
                enhance_meta["resizeStrategy"] = enhance_meta.get("resizeStrategy") or "smart-fit-enhanced"
                if output_format in ("jpg", "jpeg"):
                    resized = resized.convert("RGB")
                resized.save(out_path)
                layer_reflow_template = None
                used_layer_roles = []

            # render meta 계산
            fallback_errors = []
            if layer_reflow_succeeded:
                render_source = "psd_layer_reflow"   # Fix 3: 전용 enum 값
                fallback_used = False
                fallback_reason = None
                source_w, source_h = w, h             # Fix 3: 결과 크기로 저장
            elif actual_render_mode in ("artboard", "full-canvas"):
                render_source = "psd_tools_composite"
                fallback_used = False
                fallback_reason = None
                source_w = ab_img.width if ab_img else 0
                source_h = ab_img.height if ab_img else 0
            elif flat_meta:
                render_source = flat_meta.get("renderSource", "unknown")
                fallback_used = flat_meta.get("fallbackUsed", True)
                fallback_reason = flat_meta.get("fallbackReason")
                fallback_errors = flat_meta.get("fallbackErrors", [])
                source_w = flat_meta.get("sourceWidth", 0)
                source_h = flat_meta.get("sourceHeight", 0)
            else:
                render_source = actual_render_mode or "unknown"
                fallback_used = True
                fallback_reason = layer_reflow_error
                source_w, source_h = 0, 0

            file_size = os.path.getsize(out_path)
            with Image.open(out_path) as check_img:
                actual_w, actual_h = check_img.size
            valid = (actual_w == w and actual_h == h)
            validation_message = "정상" if valid else f"expected={w}x{h}, actual={actual_w}x{actual_h}"

            # Fix 1: 모든 fallback 실패 시 valid=false 처리 (회색 placeholder는 파일 존재용으로만)
            if actual_render_mode == "failed":
                valid = False
                validation_message = "PSD final image extraction failed"

            _lr_eff_mode = enhance_meta.get("resizeStrategy") or ("psd-layer-reflow" if layer_reflow_succeeded else "smart-fit")
            _lr_blur = not layer_reflow_succeeded  # fallback → smart-fit → blur
            print(
                f"[RENDER_PROVENANCE] spec={name} size={w}x{h}"
                f" sourceType=psd psdMode=layer-reflow requestedMode={resize_mode}"
                f" effectiveMode={_lr_eff_mode} blurFill={_lr_blur}"
                f" layerReflowSucceeded={layer_reflow_succeeded}"
            )

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
                "renderSource": render_source,
                "fallbackUsed": fallback_used,
                "fallbackReason": fallback_reason,
                "fallbackErrors": fallback_errors,
                "sourceWidth": source_w,
                "sourceHeight": source_h,
                "layerReflowAttempted": layer_reflow_attempted,
                "layerReflowSucceeded": layer_reflow_succeeded,
                "layerReflowError": layer_reflow_error,
                "layerReflowExtractedLayerCount": layer_reflow_extracted_count,
                "layerReflowDetectedRoles": layer_reflow_detected_roles,
                "layerReflowTemplate": layer_reflow_template,
                "usedLayerRoles": used_layer_roles,
                "resizeStrategy":       enhance_meta.get("resizeStrategy"),
                "candidateType":        enhance_meta.get("candidateType"),
                "candidateScore":       enhance_meta.get("candidateScore"),
                "blurAreaRatio":        enhance_meta.get("blurAreaRatio"),
                "cropRatio":            enhance_meta.get("cropRatio"),
                "subjectScale":         enhance_meta.get("subjectScale"),
                "qualityGate":          enhance_meta.get("qualityGate"),
                "qualityLabel":         enhance_meta.get("qualityLabel"),
                "safeZonePass":         safe_zone_pass,
                "safeZoneViolations":   [],
                "requiredLayerMissing": required_layer_missing,
                # 1단계: 고품질 경로 메타
                "renderMode": "psd_layer_reflow",
                "objectReflowUsed": False,
                "objectReflowFallbackUsed": False,
                "layoutScore": None,
                "backgroundMode": None,
                "candidateCount": None,
                "selectedCandidateId": None,
                # Stage 20 Typography Pipeline meta
                "typographyPipelineAttempted": typography_attempted,
                "typographyPipelineSucceeded": typo_meta.get("success", False) if typography_attempted else False,
                "typographyTemplate": typo_meta.get("template"),
                "typographyKoreanLayers": typo_meta.get("koreanLayers", 0),
                "typographyDedupRemoved": typo_meta.get("dedupRemovedCount", 0),
                "typographyCtaGroupDetected": typo_meta.get("ctaGroupDetected", False),
                "typographyQualityScore": typo_meta.get("qualityScore", 0.0),
                "typographyWarnings": typo_meta.get("warnings", []),
                # Stage 20.3: 렌더 프로브넌스
                "renderProvenance": {
                    "requestedResizeMode": resize_mode,
                    "effectiveResizeMode": _lr_eff_mode,
                    "blurFillUsed": _lr_blur,
                    "forcedSmartFit": not layer_reflow_succeeded,  # fallback 시 smart-fit 강제
                    "sourceType": "psd",
                    "psdMode": "layer-reflow",
                    "backgroundPipelineUsed": False,
                    "sourceFaithfulRepairUsed": False,
                },
            })
        return results, []

    # PSD 아트보드 모드: 각 spec마다 최적 아트보드를 선택해 렌더링 (안전 렌더링 + fallback 체인)
    if source_type == "psd" and psd_mode == "artboard-first":
        import psd_analyzer

        print(f"[PSD_LOAD] start file={os.path.basename(psd_path)}")
        analysis = psd_analyzer.analyze_psd_file(psd_path)
        all_artboards = analysis["artboards"]

        # 사용자가 선택한 아트보드만 필터링 (빈 목록이면 전체 사용)
        if selected_artboard_ids:
            filtered = [ab for ab in all_artboards if ab.get("id") in selected_artboard_ids]
            artboards = filtered if filtered else all_artboards
        else:
            artboards = all_artboards

        # 감지된 비율 타입 vs 전체 기대 타입 → missing 계산
        _ALL_RATIO_TYPES = {"square", "vertical", "horizontal"}
        _detected_types = {ab.get("artboardType") for ab in artboards
                           if ab.get("artboardType") in _ALL_RATIO_TYPES}
        missing_ratio_types = sorted(_ALL_RATIO_TYPES - _detected_types)
        print(f"[PSD_LOAD] artboards={len(artboards)} detected={_detected_types} missing={missing_ratio_types}")

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
            selected_ab_type = None
            selected_ab_box = None
            artboard_match_score = None
            selected_source_artboard_size = None
            source_match_type = None
            if best_ab:
                _ab_w = best_ab.get("width", 0)
                _ab_h = best_ab.get("height", 1)
                _ab_ratio = _ab_w / max(_ab_h, 1)
                _tgt_ratio = w / max(h, 1)
                _ratio_diff = abs(_tgt_ratio - _ab_ratio) / max(_tgt_ratio, 1)
                artboard_match_score = round(max(0.0, 1.0 - _ratio_diff), 3)
                selected_ab_type = best_ab.get("artboardType")
                selected_ab_box = {
                    "x": best_ab.get("x", 0),
                    "y": best_ab.get("y", 0),
                    "width": _ab_w,
                    "height": _ab_h,
                }
                selected_source_artboard_size = f"{_ab_w}x{_ab_h}"
                if best_ab.get("id") == "full_canvas":
                    source_match_type = "fallback"
                elif _ratio_diff < 0.15:
                    source_match_type = "exact"
                else:
                    source_match_type = "inferred"
            actual_render_mode = None
            ab_img = None
            flat_meta = None

            if not is_full_canvas:
                ab_img, actual_render_mode = psd_analyzer.safe_render_artboard(psd_path, best_ab)
                if actual_render_mode == "artboard":
                    selected_ab_id = best_ab["id"]
                    selected_ab_name = best_ab["name"]
                    selected_ab_type = best_ab.get("artboardType")

            if ab_img is None:
                # 아트보드 렌더 실패 또는 full_canvas → 4단계 fallback 체인
                ab_img, actual_render_mode, flat_meta = psd_analyzer.fallback_flatten_psd(psd_path)

            if ab_img is None:
                # 최종 실패: 회색 빈 이미지로 대체
                ab_img = Image.new("RGBA", (w, h), (200, 200, 200, 255))
                actual_render_mode = "failed"

            # render meta 계산
            fallback_errors = []
            if actual_render_mode in ("artboard", "full-canvas"):
                render_source = "psd_tools_composite"
                fallback_used = False
                fallback_reason = None
            elif flat_meta:
                render_source = flat_meta.get("renderSource", "unknown")
                fallback_used = flat_meta.get("fallbackUsed", True)
                fallback_reason = flat_meta.get("fallbackReason")
                fallback_errors = flat_meta.get("fallbackErrors", [])
            else:
                render_source = actual_render_mode or "unknown"
                fallback_used = actual_render_mode not in ("artboard", "full-canvas", None)
                fallback_reason = None
            source_w = ab_img.width
            source_h = ab_img.height

            print(f"[PSD_LOAD] source loaded width={source_w} height={source_h} source={render_source}")

            # artboard 모드일 때 smart-fit 강제(기존 동작 유지); 사용자가 다른 모드 선택해도 artboard는 smart-fit
            eff_mode = resize_mode if actual_render_mode != "artboard" else "smart-fit"
            _forced_sf = (eff_mode != resize_mode)
            resized, enhance_meta = _apply_resize(
                ab_img, w, h, eff_mode,
                smart_fit_strength or "balanced",
                focal_position or "center",
            )

            # Stage 19: BackgroundPipeline (disabled by default; BACKGROUND_PIPELINE_ENABLED=false)
            _bg_dir = os.path.join(output_dir, "stage19", f"{w}x{h}")
            _pipeline_meta = _try_background_pipeline(ab_img, w, h, job_id=job_id, output_dir=_bg_dir)
            if _pipeline_meta["executed"] and not _pipeline_meta["compareOnly"]:
                _pi = _pipeline_meta.get("resultImage")
                if _pi is not None:
                    resized = _pi
                    enhance_meta["resizeStrategy"] = "stage19-background-pipeline"
                    enhance_meta["blurFillUsed"] = False

            _psd_ab_eff_mode = enhance_meta.get("resizeStrategy") or eff_mode
            _psd_ab_blur = enhance_meta.get("blurFillUsed", eff_mode == "smart-fit")
            print(
                f"[RENDER_PROVENANCE] spec={name} size={w}x{h}"
                f" sourceType=psd psdMode=artboard-first requestedMode={resize_mode}"
                f" effectiveMode={_psd_ab_eff_mode} blurFill={_psd_ab_blur}"
                f" forcedSmartFit={_forced_sf}"
            )

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

            # Fix 1: 모든 fallback 실패 시 valid=false 처리
            if actual_render_mode == "failed":
                valid = False
                validation_message = "PSD final image extraction failed"

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
                "selectedArtboardType": selected_ab_type,
                "selectedArtboardBox": selected_ab_box,
                "artboardMatchScore": artboard_match_score,
                "selectedSourceArtboardSize": selected_source_artboard_size,
                "sourceMatchType": source_match_type,
                "actualPsdRenderMode": actual_render_mode,
                "renderSource": render_source,
                "fallbackUsed": fallback_used,
                "fallbackReason": fallback_reason,
                "fallbackErrors": fallback_errors,
                "sourceWidth": source_w,
                "sourceHeight": source_h,
                "layerReflowAttempted": False,
                "layerReflowSucceeded": False,
                "layerReflowError": None,
                "layerReflowExtractedLayerCount": 0,
                "layerReflowDetectedRoles": [],
                "layerReflowTemplate": None,
                "usedLayerRoles": [],
                "resizeStrategy":       enhance_meta.get("resizeStrategy"),
                "candidateType":        enhance_meta.get("candidateType"),
                "candidateScore":       enhance_meta.get("candidateScore"),
                "blurAreaRatio":        enhance_meta.get("blurAreaRatio"),
                "cropRatio":            enhance_meta.get("cropRatio"),
                "subjectScale":         enhance_meta.get("subjectScale"),
                "qualityGate":          enhance_meta.get("qualityGate"),
                "qualityLabel":         enhance_meta.get("qualityLabel"),
                "safeZonePass":         None,
                "safeZoneViolations":   [],
                "requiredLayerMissing": None,
                # 1단계: 고품질 경로 메타
                "renderMode": "psd_artboard_first",
                "objectReflowUsed": False,
                "objectReflowFallbackUsed": False,
                "layoutScore": None,
                "backgroundMode": None,
                "candidateCount": None,
                "selectedCandidateId": None,
                # Stage 19 BackgroundPipeline metadata
                "backgroundPipelineExecuted": _pipeline_meta["executed"],
                "backgroundPipelineEnabled": _pipeline_meta["enabled"],
                "backgroundCompareOnly": _pipeline_meta["compareOnly"],
                "bestEvaluatedBackgroundSource": _pipeline_meta["bestEvaluatedBackgroundSource"],
                "appliedBackgroundSource": _pipeline_meta["appliedBackgroundSource"],
                "appliedBackgroundScore": _pipeline_meta["appliedBackgroundScore"],
                "backgroundFallbackUsed": _pipeline_meta["backgroundFallbackUsed"],
                "backgroundFallbackReason": _pipeline_meta["backgroundFallbackReason"],
                "externalInpaintAttempted": _pipeline_meta["externalInpaintAttempted"],
                "outpaintAttempted": _pipeline_meta["outpaintAttempted"],
                "shadowApplied": _pipeline_meta["shadowApplied"],
                # Stage 20.3: 렌더 프로브넌스
                "renderProvenance": {
                    "requestedResizeMode": resize_mode,
                    "effectiveResizeMode": _psd_ab_eff_mode,
                    "blurFillUsed": _psd_ab_blur,
                    "forcedSmartFit": _forced_sf,
                    "sourceType": "psd",
                    "psdMode": "artboard-first",
                    "backgroundPipelineUsed": _pipeline_meta["executed"] and not _pipeline_meta["compareOnly"],
                    "sourceFaithfulRepairUsed": False,
                },
            })
        return results, missing_ratio_types

    # 기존 이미지/PSD flatten 처리
    print(f"[PSD_LOAD] start file={os.path.basename(psd_path)}")
    img, source_meta = load_source_image(psd_path)
    print(f"[PSD_LOAD] source loaded width={img.width} height={img.height} source={source_meta['renderSource']}")
    resize_fn = RESIZE_FUNCS.get(resize_mode, resize_cover)

    for spec in specs:
        media = spec["media"]
        w = spec["width"]
        h = spec["height"]
        slug = spec.get("slug", "")
        name = spec.get("name", "")

        resized, enhance_meta = _apply_resize(img, w, h, resize_mode, smart_fit_strength, focal_position)

        # Stage 19: BackgroundPipeline (disabled by default; BACKGROUND_PIPELINE_ENABLED=false)
        _bg_dir = os.path.join(output_dir, "stage19", f"{w}x{h}")
        _pipeline_meta = _try_background_pipeline(img, w, h, job_id=job_id, output_dir=_bg_dir)
        if _pipeline_meta["executed"] and not _pipeline_meta["compareOnly"]:
            _pi = _pipeline_meta.get("resultImage")
            if _pi is not None:
                resized = _pi
                enhance_meta["resizeStrategy"] = "stage19-background-pipeline"
                enhance_meta["blurFillUsed"] = False

        _eff_mode = enhance_meta.get("resizeStrategy") or resize_mode
        _blur_used = enhance_meta.get("blurFillUsed", resize_mode == "smart-fit")
        print(
            f"[RENDER_PROVENANCE] spec={name} size={w}x{h}"
            f" sourceType={source_type} requestedMode={resize_mode}"
            f" effectiveMode={_eff_mode} blurFill={_blur_used}"
        )

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
            "actualPsdRenderMode": source_meta["renderSource"] if source_type == "psd" else None,
            "renderSource": source_meta["renderSource"],
            "fallbackUsed": source_meta["fallbackUsed"],
            "fallbackReason": source_meta["fallbackReason"],
            "fallbackErrors": source_meta.get("fallbackErrors", []),
            "sourceWidth": source_meta["sourceWidth"],
            "sourceHeight": source_meta["sourceHeight"],
            "layerReflowAttempted": False,
            "layerReflowSucceeded": False,
            "layerReflowError": None,
            "layerReflowExtractedLayerCount": 0,
            "layerReflowDetectedRoles": [],
            "layerReflowTemplate": None,
            "usedLayerRoles": [],
            "resizeStrategy":       enhance_meta.get("resizeStrategy"),
            "candidateType":        enhance_meta.get("candidateType"),
            "candidateScore":       enhance_meta.get("candidateScore"),
            "blurAreaRatio":        enhance_meta.get("blurAreaRatio"),
            "cropRatio":            enhance_meta.get("cropRatio"),
            "subjectScale":         enhance_meta.get("subjectScale"),
            "qualityGate":          enhance_meta.get("qualityGate"),
            "qualityLabel":         enhance_meta.get("qualityLabel"),
            "safeZonePass":         None,
            "safeZoneViolations":   [],
            "requiredLayerMissing": None,
            # 1단계: 고품질 경로 메타
            "renderMode": "flatten",
            "objectReflowUsed": False,
            "objectReflowFallbackUsed": False,
            "layoutScore": None,
            "backgroundMode": None,
            "candidateCount": None,
            "selectedCandidateId": None,
            # Stage 19 BackgroundPipeline metadata
            "backgroundPipelineExecuted": _pipeline_meta["executed"],
            "backgroundPipelineEnabled": _pipeline_meta["enabled"],
            "backgroundCompareOnly": _pipeline_meta["compareOnly"],
            "bestEvaluatedBackgroundSource": _pipeline_meta["bestEvaluatedBackgroundSource"],
            "appliedBackgroundSource": _pipeline_meta["appliedBackgroundSource"],
            "appliedBackgroundScore": _pipeline_meta["appliedBackgroundScore"],
            "backgroundFallbackUsed": _pipeline_meta["backgroundFallbackUsed"],
            "backgroundFallbackReason": _pipeline_meta["backgroundFallbackReason"],
            "externalInpaintAttempted": _pipeline_meta["externalInpaintAttempted"],
            "outpaintAttempted": _pipeline_meta["outpaintAttempted"],
            "shadowApplied": _pipeline_meta["shadowApplied"],
            # Stage 20.3: 렌더 프로브넌스 (요청 모드 vs 실제 모드 추적)
            "renderProvenance": {
                "requestedResizeMode": resize_mode,
                "effectiveResizeMode": _eff_mode,
                "blurFillUsed": _blur_used,
                "forcedSmartFit": False,
                "sourceType": source_type,
                "psdMode": None,
                "backgroundPipelineUsed": _pipeline_meta["executed"] and not _pipeline_meta["compareOnly"],
                "sourceFaithfulRepairUsed": False,
            },
        })

    return results, []
