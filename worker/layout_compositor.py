"""6단계: Layout compositor — background + layout_compiler 결과 → 최종 RGBA 이미지.

흐름:
    composite_layout(bg_img, bg_meta, layout_result, cos, dst_w, dst_h, ...)
        → best candidate의 placements를 z-order 정렬
        → 각 placement: asset 로드 → scale/crop → alpha composite
        → debug metadata JSON 저장
        → (final_rgba_image, metadata_dict) 반환

z-order (낮을수록 먼저 렌더):
    background(0) → main_image/person(1) → decoration(2) →
    badge/price/discount(3) → logo(4) → headline/body_text(5) → cta(6)

원칙:
    - blur background 사용 금지 (bg_img는 background_builder 제공)
    - NO_CROP_ROLES는 layout_compiler가 이미 contain 배치 → 여기서 crop 없음
    - required asset 누락 시 metadata에 기록 → 호출자가 fallback 판단
"""

import json
import os
from PIL import Image, ImageEnhance


# ─── z-order ─────────────────────────────────────────────────────────────────

_Z_ORDER: dict[str, int] = {
    "background":  0,
    "main_image":  1,
    "person":      1,
    "decoration":  2,
    "badge":       3,
    "price":       3,
    "discount":    3,
    "logo":        4,
    "body_text":   5,
    "headline":    5,
    "cta":         6,
}

_OPTIONAL_ROLES = frozenset({"decoration", "background", "badge"})
_REQUIRED_ROLES = frozenset({"cta", "headline", "logo", "main_image"})


def _z_key(placement: dict) -> int:
    return _Z_ORDER.get(placement.get("role", ""), 3)


# ─── 이미지 리샘플 ────────────────────────────────────────────────────────────

def _resample(img: Image.Image, target_w: int, target_h: int, scale: float) -> Image.Image:
    """LANCZOS 리사이즈 + 큰 축소 시 sharpness 보정."""
    tw = max(1, target_w)
    th = max(1, target_h)
    result = img.resize((tw, th), Image.LANCZOS)
    # 크게 축소할수록 약한 sharpening (최대 +25%)
    if scale < 0.4:
        factor = 1.0 + min(0.25, (0.4 - scale) * 0.6)
        result = ImageEnhance.Sharpness(result).enhance(factor)
    return result


# ─── placement 렌더 ───────────────────────────────────────────────────────────

def _render_bbox_fallback(p: dict, obj: dict, canvas: "Image.Image") -> "Image.Image | None":
    """imagePath 없는 required 객체: bbox 좌표로 canvas 영역 crop.

    객체의 원본 bbox가 canvas에 해당 위치에 있다면 그 픽셀을 잘라 사용한다.
    bbox가 없거나 canvas 범위 밖이면 None 반환.
    """
    bbox = obj.get("bbox")
    if not bbox:
        return None
    try:
        bx = int(bbox.get("x", 0))
        by = int(bbox.get("y", 0))
        bw = int(bbox.get("width", 0))
        bh = int(bbox.get("height", 0))
    except (TypeError, ValueError):
        return None
    if bw <= 0 or bh <= 0:
        return None
    cw, ch = canvas.size
    # bbox가 canvas와 전혀 겹치지 않으면 스킵
    if bx >= cw or by >= ch or bx + bw <= 0 or by + bh <= 0:
        return None
    # 교차 영역만 crop
    cx1 = max(0, bx)
    cy1 = max(0, by)
    cx2 = min(cw, bx + bw)
    cy2 = min(ch, by + bh)
    try:
        region = canvas.crop((cx1, cy1, cx2, cy2)).convert("RGBA")
    except Exception:
        return None
    target_w = max(1, p["width"])
    target_h = max(1, p["height"])
    scale = float(p.get("scale", 1.0))
    return _resample(region, target_w, target_h, scale)


def _render_placement(p: dict, obj: dict) -> "Image.Image | None":
    """placement + object → 최종 크기로 렌더된 RGBA 이미지.

    - crop 없음: target_w × target_h로 직접 리사이즈 (layout_compiler가 contain 계산)
    - crop 있음: 전체 scaled size로 리사이즈 후 crop dict 적용
    """
    img_path = obj.get("imagePath")
    if not img_path or not os.path.exists(str(img_path)):
        return None

    try:
        img = Image.open(img_path)
        img.load()
    except Exception:
        return None

    if img.mode != "RGBA":
        img = img.convert("RGBA")

    target_w = max(1, p["width"])
    target_h = max(1, p["height"])
    scale    = float(p.get("scale", 1.0))
    crop     = p.get("crop")

    if crop:
        # full scaled size = visible size + crop margins
        full_w = target_w + int(crop.get("left", 0)) + int(crop.get("right",  0))
        full_h = target_h + int(crop.get("top",  0)) + int(crop.get("bottom", 0))
        scaled = _resample(img, max(1, full_w), max(1, full_h), scale)
        cl = int(crop.get("left", 0))
        ct = int(crop.get("top",  0))
        return scaled.crop((cl, ct, cl + target_w, ct + target_h))
    else:
        return _resample(img, target_w, target_h, scale)


# ─── 메인 함수 ────────────────────────────────────────────────────────────────

def composite_layout(
    background_img: "Image.Image",
    bg_meta: dict,
    layout_result: dict,
    creative_object_set: dict,
    dst_w: int,
    dst_h: int,
    output_dir: "str | None" = None,
    job_id: "str | None" = None,
) -> tuple:
    """background + layout_compiler 결과 → (final_rgba_image, metadata_dict).

    background_img는 background_builder.build_background()가 생성한 dst_w × dst_h 이미지.
    layout_result는 layout_compiler.compile_layout() 결과.
    creative_object_set은 creative_object_extractor.build_creative_object_set() 결과.

    반환 metadata:
        renderMode, objectReflowUsed, backgroundMode, layoutScore, candidateCount,
        selectedCandidateId, safeZonePassed, safeZoneViolations,
        droppedObjects, missingRequiredAssets, renderedRoles, warnings, fallbackUsed
    """
    label = job_id or "job"

    # object id → object dict
    objs_by_id: dict = {
        obj["id"]: obj
        for obj in (creative_object_set or {}).get("objects", [])
        if obj.get("id")
    }

    best         = layout_result.get("best") or {}
    placements   = best.get("placements", [])
    layout_meta  = layout_result.get("metadata", {})

    # ── Canvas 초기화 (background_img 복사) ──────────────────────────────────
    canvas = background_img.copy()
    if canvas.mode != "RGBA":
        canvas = canvas.convert("RGBA")
    if canvas.size != (dst_w, dst_h):
        canvas = canvas.resize((dst_w, dst_h), Image.LANCZOS)

    # ── z-order 정렬 (dropped 제외) ──────────────────────────────────────────
    active_placements = [p for p in placements if not p.get("dropped", False)]
    sorted_placements = sorted(active_placements, key=_z_key)

    dropped_objects        : list = []
    missing_required_assets: list = []
    rendered_roles         : list = []
    warnings               : list = list(best.get("warnings", []))

    for p in sorted_placements:
        obj_id = p.get("objectId", "")
        role   = p.get("role",     "unknown")

        if not obj_id:
            continue

        obj = objs_by_id.get(obj_id)
        if obj is None:
            warnings.append(f"objectId={obj_id} not found in creative_object_set")
            dropped_objects.append(obj_id)
            continue

        rendered = _render_placement(p, obj)

        if rendered is None:
            is_optional = (
                role in _OPTIONAL_ROLES
                or bool(obj.get("canDrop", False))
                or p.get("dropped", False)
            )
            if not is_optional:
                # required asset 누락 → bbox crop fallback 시도
                rendered = _render_bbox_fallback(p, obj, canvas)
                if rendered is not None:
                    warnings.append(f"bbox_fallback used for {role}({obj_id})")
                    print(f"[{label}][Compositor] bbox_fallback used for {role}({obj_id})")

            if rendered is None:
                if is_optional:
                    dropped_objects.append(obj_id)
                    print(f"[{label}][Compositor] dropped optional {role}({obj_id}) - asset missing")
                else:
                    missing_required_assets.append(obj_id)
                    warnings.append(f"required asset missing: {role}({obj_id})")
                    print(f"[{label}][Compositor] WARN required asset missing: {role}({obj_id})")
                continue

        # 캔버스 경계 확인
        px = max(0, p["x"])
        py = max(0, p["y"])
        if px >= dst_w or py >= dst_h:
            warnings.append(f"{role}({obj_id}) placement outside canvas, skipped")
            dropped_objects.append(obj_id)
            continue

        # 경계를 벗어나는 부분 clip
        if px + rendered.width > dst_w or py + rendered.height > dst_h:
            clip_w = max(1, min(rendered.width,  dst_w - px))
            clip_h = max(1, min(rendered.height, dst_h - py))
            rendered = rendered.crop((0, 0, clip_w, clip_h))

        if rendered.mode != "RGBA":
            rendered = rendered.convert("RGBA")

        canvas.alpha_composite(rendered, (px, py))
        rendered_roles.append(role)
        print(
            f"[{label}][Compositor] placed {role}({obj_id}) "
            f"at ({px},{py}) {rendered.width}x{rendered.height} scale={p.get('scale',1):.3f}"
        )

    # ── debug metadata ────────────────────────────────────────────────────────
    # hardFailReasons: 모든 후보의 실패 이유 전체 집합 (debug용)
    all_hard_fail_reasons: list = layout_meta.get("hardFailures", [])

    # emergency fallback 여부 판정
    is_emergency: bool = best.get("candidateId") == "emergency_fallback"

    # safeZoneViolations: 선택된 candidate 기준으로만 추출
    #   - 일반 valid candidate: best.hardFailReasons에서 safe zone 관련만
    #     (valid candidate는 hardFail=False이므로 보통 []임)
    #   - emergency fallback: 전체 후보 실패 이유에서 safe zone 관련 추출
    if is_emergency:
        sz_violations: list = [r for r in all_hard_fail_reasons if "safe zone" in r.lower()]
    else:
        sz_violations = [r for r in best.get("hardFailReasons", []) if "safe zone" in r.lower()]

    # safeZonePassed: 선택 candidate가 safe zone 통과 + 필수 asset 누락 없음
    # emergency fallback이면 항상 False (품질 저하 신호)
    safeZonePassed: bool = (
        not is_emergency
        and len(missing_required_assets) == 0
        and len(sz_violations) == 0
    )

    meta = {
        "renderMode":              "object-layout-reflow",
        "objectReflowUsed":        True,
        "objectReflowFallbackUsed": is_emergency,
        "backgroundMode":          bg_meta.get("backgroundMode"),
        "backgroundBlurUsed":      bg_meta.get("blurUsed", False),
        "layoutScore":             layout_meta.get("layoutScore"),
        "candidateCount":          layout_meta.get("candidateCount"),
        "selectedCandidateId":     layout_meta.get("selectedCandidateId"),
        "ratioType":               layout_meta.get("ratioType"),
        "safeZonePassed":          safeZonePassed,
        "safeZoneViolations":      sz_violations,          # 선택 candidate 기준
        "hardFailReasons":         all_hard_fail_reasons,  # 전 유형 전 후보 (debug용)
        "droppedObjects":          dropped_objects,
        "missingRequiredAssets":   missing_required_assets,
        "renderedRoles":           rendered_roles,
        "warnings":                warnings + layout_meta.get("warnings", []),
        "fallbackUsed":            bool(best.get("fallbackUsed", False)),
    }

    if output_dir:
        try:
            os.makedirs(output_dir, exist_ok=True)
            debug_path = os.path.join(output_dir, "layout_debug.json")
            with open(debug_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[{label}][Compositor] debug JSON 저장 실패: {e}")

    return canvas, meta
