"""Stage 17: Inpaint / Outpaint PoC.

Local heuristic 구현 (GPU/외부 API 없이 동작).
외부 AI provider는 stub만 준비 — API key 없으면 자동 skip.

Flags:
  env  CREATIVE_INPAINT_POC=true
  req  object_analysis.experimentalInpaint=true
  req  object_analysis.experimentalOutpaint=true
"""

import os
from PIL import Image, ImageFilter, ImageChops

from mask_utils import build_mask_union, scale_mask_to_target


# ─── PoC flags ────────────────────────────────────────────────────────────────

def _inpaint_on(extra: dict | None) -> bool:
    return (
        os.environ.get("CREATIVE_INPAINT_POC", "false").lower() == "true"
        or bool((extra or {}).get("experimentalInpaint"))
    )


def _outpaint_on(extra: dict | None) -> bool:
    return (
        os.environ.get("CREATIVE_INPAINT_POC", "false").lower() == "true"
        or bool((extra or {}).get("experimentalOutpaint"))
    )


# ─── External AI provider stubs ───────────────────────────────────────────────

def generate_mask_with_external_ai(
    image: "Image.Image",
    prompt: str = "",
    api_key: str | None = None,
) -> "Image.Image | None":
    """외부 AI segmentation stub (SAM, Grounded-SAM 등 향후 연결 예정).

    API key 없으면 None 반환 (providerUnavailable warning — job 죽이지 않음).
    """
    if not api_key:
        return None  # providerUnavailable — caller adds warning
    return None      # stub: 실제 외부 호출 구현 전까지 None


def inpaint_with_external_ai(
    image: "Image.Image",
    mask: "Image.Image",
    prompt: str = "",
    api_key: str | None = None,
) -> "Image.Image | None":
    """외부 AI inpainting stub (DALL-E Edit, SD Inpaint 등 향후 연결 예정)."""
    if not api_key:
        return None
    return None


def outpaint_with_external_ai(
    image: "Image.Image",
    target_w: int,
    target_h: int,
    prompt: str = "",
    api_key: str | None = None,
) -> "Image.Image | None":
    """외부 AI outpainting stub (DALL-E Edit / SD 향후 연결 예정)."""
    if not api_key:
        return None
    return None


# ─── Local heuristic inpaint ─────────────────────────────────────────────────

def _heuristic_inpaint(
    img: "Image.Image",
    mask: "Image.Image",
    blur_radius: int = 28,
) -> "Image.Image":
    """mask 영역을 주변 색상의 heavy-blur로 채운다.

    완벽한 AI 인페인팅이 아니라, 기존 extend/stretch보다
    덜 어색한 clean background 후보를 만드는 것이 목표.

    1. 이미지를 heavy Gaussian blur
    2. feathered mask로 합성 (blur → masked, original → unmasked)
    """
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    if mask.size != img.size:
        mask = mask.resize(img.size, Image.LANCZOS)

    blurred = img.filter(ImageFilter.GaussianBlur(radius=blur_radius))
    feathered = mask.filter(ImageFilter.GaussianBlur(radius=10))
    return Image.composite(blurred, img, feathered)


# ─── Local heuristic outpaint ─────────────────────────────────────────────────

def _heuristic_outpaint(
    img: "Image.Image",
    target_w: int,
    target_h: int,
    sample_px: int = 20,
) -> "Image.Image":
    """비율 차이가 큰 target으로 배경을 확장한다.

    기존 background_builder.extend_edges 대비 개선:
    - 단순 1px 에지 복제 대신 sample_px 폭 평균 + Gaussian blur
    - 경계 부분에 blend 적용

    반환: target_w × target_h RGBA image
    """
    if img.mode != "RGBA":
        img = img.convert("RGBA")

    src_w, src_h = img.size

    # contain-fit 배치
    scale = min(target_w / src_w, target_h / src_h)
    sw = max(1, round(src_w * scale))
    sh = max(1, round(src_h * scale))
    scaled = img.resize((sw, sh), Image.LANCZOS)

    px = (target_w - sw) // 2
    py = (target_h - sh) // 2

    canvas = Image.new("RGBA", (target_w, target_h), (220, 220, 220, 255))

    samp = max(1, min(sample_px, sw // 4, sh // 4))

    # 좌우 채우기 — sample_px 폭 blur 평균
    if px > 0:
        left_strip = scaled.crop((0, 0, samp, sh))
        left_blurred = left_strip.filter(ImageFilter.GaussianBlur(radius=samp))
        left_avg = left_blurred.resize((1, sh), Image.LANCZOS)
        left_fill = left_avg.resize((px, sh), Image.NEAREST)
        canvas.paste(left_fill, (0, py))

        right_start = sw - samp
        right_strip = scaled.crop((right_start, 0, sw, sh))
        right_blurred = right_strip.filter(ImageFilter.GaussianBlur(radius=samp))
        right_avg = right_blurred.resize((1, sh), Image.LANCZOS)
        rx = px + sw
        rw = target_w - rx
        if rw > 0:
            right_fill = right_avg.resize((rw, sh), Image.NEAREST)
            canvas.paste(right_fill, (rx, py))

    # 상하 채우기
    if py > 0:
        top_strip = scaled.crop((0, 0, sw, samp))
        top_blurred = top_strip.filter(ImageFilter.GaussianBlur(radius=samp))
        top_avg = top_blurred.resize((sw, 1), Image.LANCZOS)
        top_fill = top_avg.resize((sw, py), Image.NEAREST)
        canvas.paste(top_fill, (px, 0))

        bot_start = sh - samp
        bot_strip = scaled.crop((0, bot_start, sw, sh))
        bot_blurred = bot_strip.filter(ImageFilter.GaussianBlur(radius=samp))
        bot_avg = bot_blurred.resize((sw, 1), Image.LANCZOS)
        by_ = py + sh
        bh = target_h - by_
        if bh > 0:
            bot_fill = bot_avg.resize((sw, bh), Image.NEAREST)
            canvas.paste(bot_fill, (px, by_))

    # corner 채우기 (모서리 픽셀 단색)
    if px > 0 and py > 0:
        corners = [
            (0, 0, px, py),           scaled.getpixel((0, 0)),
            (px + sw, 0, target_w, py), scaled.getpixel((sw - 1, 0)),
            (0, py + sh, px, target_h), scaled.getpixel((0, sh - 1)),
            (px + sw, py + sh, target_w, target_h), scaled.getpixel((sw - 1, sh - 1)),
        ]
        it = iter(corners)
        for box, color in zip(it, it):
            bx1, by1, bx2, by2 = box
            if bx2 > bx1 and by2 > by1:
                canvas.paste(Image.new("RGBA", (bx2 - bx1, by2 - by1), color), (bx1, by1))

    # 원본 중앙 배치
    canvas.paste(scaled, (px, py))

    # 경계 부드럽게 (가벼운 blur 후 원본 재붙임)
    if px > 0 or py > 0:
        softened = canvas.filter(ImageFilter.GaussianBlur(radius=2))
        softened.paste(scaled, (px, py))
        return softened

    return canvas


# ─── Inpaint entry ────────────────────────────────────────────────────────────

def run_inpaint_poc(
    bg_img: "Image.Image",
    masks: list[dict],
    src_canvas_w: int,
    src_canvas_h: int,
    target_w: int,
    target_h: int,
    output_dir: str | None = None,
    job_id: str | None = None,
    extra_flags: dict | None = None,
) -> tuple["Image.Image", dict]:
    """product/text/cta mask 영역을 인페인팅해 clean background를 만든다.

    bg_img는 target_w × target_h 크기.
    masks는 src_canvas 좌표 — 내부에서 target 크기로 변환.

    실패 시 (bg_img_unchanged, fallback_metadata) — job 죽이지 않음.
    """
    prefix = f"[{job_id or 'job'}][InpaintPoc]"

    if not _inpaint_on(extra_flags):
        return bg_img, {"inpaintPocEnabled": False, "inpaintApplied": False}

    warnings: list[str] = []
    inpaint_applied = False
    provider = "none"
    quality = 0.0

    try:
        # 외부 AI 시도 (key 없으면 None 반환)
        ext_key = os.environ.get("INPAINT_API_KEY")
        clean_bg = None

        if ext_key:
            try:
                union = build_mask_union(masks, {"product", "text", "cta"}, target_w, target_h)
                ext_bg = inpaint_with_external_ai(bg_img, union, api_key=ext_key)
                if ext_bg is not None:
                    clean_bg = ext_bg.convert("RGBA")
                    provider = "external_ai"
                    quality = 90.0
            except Exception as e:
                warnings.append(f"externalAiInpaintFailed:{e}")

        if clean_bg is None:
            # local heuristic
            # mask를 src_canvas → target 크기로 변환 후 합산
            scaled_masks: list[dict] = []
            for m in masks:
                mask_img = m.get("_maskImg")
                if mask_img is None:
                    path = m.get("maskPath")
                    if path and os.path.exists(path):
                        try:
                            mask_img = Image.open(path).convert("L")
                        except Exception:
                            continue
                if mask_img is not None:
                    scaled_img = scale_mask_to_target(
                        mask_img, src_canvas_w, src_canvas_h, target_w, target_h
                    )
                    scaled_m = dict(m)
                    scaled_m["_maskImg"] = scaled_img
                    scaled_masks.append(scaled_m)

            union = build_mask_union(scaled_masks, {"product", "text", "cta"}, target_w, target_h)
            n_masked = sum(1 for v in union.getdata() if v > 0)

            if n_masked > 0:
                clean_bg = _heuristic_inpaint(bg_img, union)
                provider = "local_heuristic"
                mask_ratio = n_masked / max(target_w * target_h, 1)
                quality = round(max(40.0, 80.0 - mask_ratio * 100), 1)
                inpaint_applied = True
                print(f"{prefix} heuristic mask_ratio={mask_ratio:.3f} quality={quality}")
            else:
                warnings.append("noMaskedPixels:inpaintSkipped")
                clean_bg = bg_img

        elif provider == "external_ai":
            inpaint_applied = True

        # debug 저장
        if output_dir and inpaint_applied:
            try:
                os.makedirs(output_dir, exist_ok=True)
                clean_bg.save(os.path.join(output_dir, "result.clean_background.png"))
                clean_bg.save(os.path.join(output_dir, "result.inpaint_preview.png"))
            except Exception as e:
                warnings.append(f"inpaintSaveFailed:{e}")

        result_img = clean_bg if (inpaint_applied and clean_bg is not None) else bg_img
        bg_mask_ids = [m["maskId"] for m in masks if m.get("role") in {"product", "text", "cta"}]

        return result_img, {
            "inpaintPocEnabled":    True,
            "inpaintApplied":       inpaint_applied,
            "inpaintProvider":      provider,
            "inpaintQualityScore":  quality,
            "inpaintFallbackUsed":  not inpaint_applied,
            "cleanBackgroundUsed":  inpaint_applied,
            "backgroundMaskIds":    bg_mask_ids,
            "warnings":             warnings,
        }

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"{prefix} FAILED: {e}")
        return bg_img, {
            "inpaintPocEnabled":   True,
            "inpaintApplied":      False,
            "inpaintProvider":     "none",
            "inpaintQualityScore": 0.0,
            "inpaintFallbackUsed": True,
            "cleanBackgroundUsed": False,
            "backgroundMaskIds":   [],
            "warnings":            [f"inpaintFailed:{e}"],
        }


# ─── Outpaint entry ───────────────────────────────────────────────────────────

def run_outpaint_poc(
    bg_img: "Image.Image",
    src_canvas_w: int,
    src_canvas_h: int,
    target_w: int,
    target_h: int,
    output_dir: str | None = None,
    job_id: str | None = None,
    extra_flags: dict | None = None,
) -> tuple["Image.Image", dict]:
    """배경 이미지를 target 크기로 자연스럽게 확장한다.

    비율 차이가 1.3 미만이면 skip (기존 cover/extend로 충분).
    실패 시 (bg_img_unchanged, fallback_metadata) — job 죽이지 않음.
    """
    prefix = f"[{job_id or 'job'}][OutpaintPoc]"

    if not _outpaint_on(extra_flags):
        return bg_img, {"outpaintPocEnabled": False, "outpaintApplied": False}

    warnings: list[str] = []
    provider = "none"
    quality = 0.0
    outpaint_applied = False

    try:
        src_ratio = src_canvas_w / max(src_canvas_h, 1)
        tgt_ratio = target_w / max(target_h, 1)
        ratio_diff = max(src_ratio, tgt_ratio) / max(min(src_ratio, tgt_ratio), 1e-9)

        if ratio_diff < 1.3:
            return bg_img, {
                "outpaintPocEnabled": True,
                "outpaintApplied":    False,
                "outpaintProvider":   "none",
                "outpaintQualityScore": 0.0,
                "outpaintFallbackUsed": False,
                "warnings": ["ratioDiffTooSmall:outpaintSkipped"],
            }

        # 외부 AI 시도
        ext_key = os.environ.get("INPAINT_API_KEY")
        ext_result = None
        if ext_key:
            try:
                ext_result = outpaint_with_external_ai(bg_img, target_w, target_h, api_key=ext_key)
                if ext_result:
                    provider = "external_ai"
                    quality = 88.0
            except Exception as e:
                warnings.append(f"externalAiOutpaintFailed:{e}")

        if ext_result is not None:
            extended = ext_result.convert("RGBA").resize((target_w, target_h), Image.LANCZOS)
            outpaint_applied = True
        else:
            extended = _heuristic_outpaint(bg_img, target_w, target_h)
            provider = "local_heuristic"
            quality = round(max(35.0, 75.0 - (ratio_diff - 1.0) * 30), 1)
            outpaint_applied = True
            print(f"{prefix} heuristic ratio_diff={ratio_diff:.2f} quality={quality}")

        if output_dir and outpaint_applied:
            try:
                os.makedirs(output_dir, exist_ok=True)
                extended.save(os.path.join(output_dir, "result.outpaint_preview.png"))
            except Exception as e:
                warnings.append(f"outpaintSaveFailed:{e}")

        return extended if outpaint_applied else bg_img, {
            "outpaintPocEnabled":    True,
            "outpaintApplied":       outpaint_applied,
            "outpaintProvider":      provider,
            "outpaintQualityScore":  quality if outpaint_applied else 0.0,
            "outpaintFallbackUsed":  not outpaint_applied,
            "warnings":              warnings,
        }

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"{prefix} FAILED: {e}")
        return bg_img, {
            "outpaintPocEnabled":    True,
            "outpaintApplied":       False,
            "outpaintProvider":      "none",
            "outpaintQualityScore":  0.0,
            "outpaintFallbackUsed":  True,
            "warnings":              [f"outpaintFailed:{e}"],
        }
