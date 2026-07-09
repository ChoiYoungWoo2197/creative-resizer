"""4차-9: PIL 기반 객체 재배치 컴포지터.
resolved_sources + laid_out_objects → 최종 RGBA Image
"""
from PIL import Image

# 렌더 순서: 배경 먼저, 텍스트/CTA 마지막
_RENDER_ORDER = ["background", "main_image", "decoration", "badge", "logo", "body_text", "title", "cta", "unknown"]


def _render_order_key(obj: dict) -> int:
    role = obj.get("role", "unknown")
    try:
        return _RENDER_ORDER.index(role)
    except ValueError:
        return len(_RENDER_ORDER)


def _fit_in_zone(img: Image.Image, zw: int, zh: int, can_crop: bool = False) -> Image.Image:
    """contain(기본) 또는 cover(can_crop=True)로 zone에 맞춤."""
    if can_crop:
        src_ratio = img.width / max(img.height, 1)
        dst_ratio = zw / max(zh, 1)
        if src_ratio > dst_ratio:
            new_h = zh
            new_w = max(1, int(img.width * zh / img.height))
        else:
            new_w = zw
            new_h = max(1, int(img.height * zw / img.width))
        resized = img.resize((new_w, new_h), Image.LANCZOS)
        left = max(0, (new_w - zw) // 2)
        top = max(0, (new_h - zh) // 2)
        return resized.crop((left, top, left + zw, top + zh))
    else:
        copy = img.copy()
        copy.thumbnail((zw, zh), Image.LANCZOS)
        return copy


def composite_objects(resolved_sources: dict, laid_out_objects: list,
                      dst_w: int, dst_h: int) -> tuple:
    """
    resolved_sources: {obj_id: {"sourceType": str, "image": Image|None, "layerId": str|None}}
    laid_out_objects: object_reflow_engine.compute_layout() 결과
    반환: (Image, meta_dict)
    """
    canvas = Image.new("RGBA", (dst_w, dst_h), (255, 255, 255, 255))
    used_roles = []
    missing_roles = []
    crop_fallback_roles = []
    low_confidence_roles = []

    sorted_objects = sorted(laid_out_objects, key=_render_order_key)

    for obj in sorted_objects:
        obj_id = obj.get("id", "")
        role = obj.get("role", "unknown")
        layout_type = obj.get("layoutType", "dropped")
        zone = obj.get("layoutZone")
        match_status = obj.get("matchStatus", "missing_layer")

        if layout_type == "dropped" or zone is None:
            if role not in ("decoration", "unknown"):
                missing_roles.append(role)
            continue

        entry = resolved_sources.get(obj_id, {"sourceType": "dropped", "image": None})
        source_type = entry["sourceType"]
        img = entry["image"]

        if source_type == "dropped" or img is None:
            if role not in ("decoration", "unknown"):
                missing_roles.append(role)
            continue

        if match_status == "matched_low_confidence":
            low_confidence_roles.append(role)

        used_roles.append(role)

        if layout_type == "fill":
            bg = img.resize((dst_w, dst_h), Image.LANCZOS)
            if bg.mode != "RGBA":
                bg = bg.convert("RGBA")
            canvas.paste(bg, (0, 0))
        else:
            zx = zone["x"]
            zy = zone["y"]
            zw = zone["w"]
            zh = zone["h"]
            can_crop = bool(obj.get("canCrop", False))

            # bbox_crop은 canCrop 기준, layer_asset은 keep_aspect 기준
            if source_type == "bbox_crop":
                crop_fallback_roles.append(role)
                fitted = _fit_in_zone(img, zw, zh, can_crop=False)
            else:
                fitted = _fit_in_zone(img, zw, zh, can_crop=can_crop)

            if fitted.mode != "RGBA":
                fitted = fitted.convert("RGBA")

            px = zx + max(0, (zw - fitted.width) // 2)
            py = zy + max(0, (zh - fitted.height) // 2)
            canvas.alpha_composite(fitted, (px, py))

    # 안전 영역 체크: safeZoneRequired=True 역할이 캔버스 경계 5% 이내에 배치됐는지
    safe_zone_pass = _check_safe_zone(laid_out_objects, used_roles, dst_w, dst_h)

    return canvas, {
        "usedObjectRoles": used_roles,
        "missingObjectRoles": missing_roles,
        "cropFallbackRoles": crop_fallback_roles,
        "lowConfidenceRoles": low_confidence_roles,
        "objectSafeZonePass": safe_zone_pass,
    }


def _check_safe_zone(objects: list, used_roles: list, dst_w: int, dst_h: int,
                     margin_ratio: float = 0.05) -> bool:
    """safeZoneRequired=True 객체의 layoutZone이 안전 영역(margin 5%) 안에 있는지."""
    mx = int(dst_w * margin_ratio)
    my = int(dst_h * margin_ratio)
    for obj in objects:
        if not obj.get("safeZoneRequired"):
            continue
        if obj.get("role") not in used_roles:
            continue
        zone = obj.get("layoutZone")
        if not zone:
            continue
        if (zone["x"] < mx or zone["y"] < my
                or zone["x"] + zone["w"] > dst_w - mx
                or zone["y"] + zone["h"] > dst_h - my):
            return False
    return True
