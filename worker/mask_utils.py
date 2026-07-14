"""Shared mask data structures and utilities for Stage 16+17 PoC.

mask는 PIL L-mode 이미지 (255=masked, 0=background).
이 모듈은 segmentation_poc.py 와 inpaint_outpaint_poc.py 에서 공통으로 사용한다.
"""

from PIL import Image, ImageFilter

# 지원하는 mask role
MASK_ROLES = frozenset({
    "product", "person_or_hand", "text", "cta",
    "background", "visual_context", "unknown",
})

# mask source 우선순위 (0~1)
SOURCE_PRIORITY: dict[str, float] = {
    "psd_alpha":          0.95,
    "psd_layer_mask":     0.90,
    "object_bbox_coarse": 0.55,
    "local_heuristic":    0.40,
    "visual_context":     0.25,
    "unknown":            0.10,
}


def create_mask_dict(
    mask_id: str,
    object_id: str,
    role: str,
    source: str,
    bbox: dict,
    canvas_w: int,
    canvas_h: int,
    confidence: float = 0.8,
    mask_img: "Image.Image | None" = None,
    mask_path: str | None = None,
    quality: dict | None = None,
) -> dict:
    """Standard mask dict 생성.

    '_maskImg' 키는 직렬화하지 않는 in-memory 이미지다.
    """
    area = bbox.get("width", 0) * bbox.get("height", 0)
    area_ratio = round(area / max(canvas_w * canvas_h, 1), 4)
    return {
        "maskId":    mask_id,
        "objectId":  object_id,
        "role":      role if role in MASK_ROLES else "unknown",
        "source":    source,
        "bbox":      bbox,
        "confidence": round(float(confidence), 3),
        "maskPath":  mask_path,
        "areaRatio": area_ratio,
        "quality":   quality or {},
        "_maskImg":  mask_img,  # in-memory only; not serialized
    }


def bbox_to_mask_image(
    bbox: dict,
    canvas_w: int,
    canvas_h: int,
    feather: int = 0,
) -> "Image.Image":
    """bbox 좌표를 L-mode PIL 이미지(255=masked)로 변환.

    >>> from PIL import Image
    >>> m = bbox_to_mask_image({"x":10,"y":10,"width":50,"height":50}, 100, 100)
    >>> m.getpixel((35, 35))
    255
    >>> m.getpixel((0, 0))
    0
    """
    mask = Image.new("L", (canvas_w, canvas_h), 0)
    x = max(0, int(bbox.get("x", 0)))
    y = max(0, int(bbox.get("y", 0)))
    w = int(bbox.get("width", 0))
    h = int(bbox.get("height", 0))
    w = min(w, canvas_w - x)
    h = min(h, canvas_h - y)
    if w > 0 and h > 0:
        mask.paste(Image.new("L", (w, h), 255), (x, y))
    if feather > 0:
        mask = feather_mask(mask, feather)
    return mask


def psd_alpha_to_canvas_mask(
    layer_img: "Image.Image",
    bbox: dict,
    canvas_w: int,
    canvas_h: int,
    alpha_threshold: int = 30,
) -> "Image.Image | None":
    """RGBA 레이어 이미지의 alpha를 canvas 좌표계 L-mode mask로 변환.

    투명도가 거의 없는 이미지(불투명도 95% 이상)는 None 반환.

    >>> from PIL import Image
    >>> layer = Image.new("RGBA", (50, 100), (255,0,0,200))
    >>> m = psd_alpha_to_canvas_mask(layer, {"x":10,"y":5,"width":50,"height":100}, 200, 200)
    >>> m is not None
    True
    >>> m.getpixel((10, 5))
    255
    """
    try:
        if layer_img.mode != "RGBA":
            layer_img = layer_img.convert("RGBA")
        _, _, _, alpha = layer_img.split()

        alpha_data = list(alpha.getdata())
        total = len(alpha_data)
        if total == 0:
            return None
        non_opaque = sum(1 for v in alpha_data if v < 240)
        # 비투명 픽셀이 5% 미만이면 solid → alpha mask로 의미 없음
        if non_opaque / total < 0.05:
            return None

        # threshold: alpha > threshold → masked
        layer_mask = alpha.point(lambda v: 255 if v > alpha_threshold else 0)

        # bbox 크기로 리사이즈 후 canvas에 배치
        bx = max(0, int(bbox.get("x", 0)))
        by = max(0, int(bbox.get("y", 0)))
        bw = int(bbox.get("width", layer_img.width))
        bh = int(bbox.get("height", layer_img.height))
        bw = min(bw, canvas_w - bx)
        bh = min(bh, canvas_h - by)
        if bw <= 0 or bh <= 0:
            return None

        layer_mask_resized = layer_mask.resize((bw, bh), Image.LANCZOS)
        canvas_mask = Image.new("L", (canvas_w, canvas_h), 0)
        canvas_mask.paste(layer_mask_resized, (bx, by))
        return canvas_mask

    except Exception:
        return None


def feather_mask(mask: "Image.Image", radius: int) -> "Image.Image":
    """mask 가장자리를 Gaussian blur로 부드럽게 처리.

    >>> from PIL import Image
    >>> m = Image.new("L", (100,100), 0)
    >>> m.paste(Image.new("L",(50,50),255),(25,25))
    >>> f = feather_mask(m, 5)
    >>> f.getpixel((50,50)) > 200
    True
    """
    if radius <= 0:
        return mask
    return mask.filter(ImageFilter.GaussianBlur(radius=radius))


def scale_mask_to_target(
    mask: "Image.Image",
    src_canvas_w: int,
    src_canvas_h: int,
    target_w: int,
    target_h: int,
) -> "Image.Image":
    """mask를 원본 canvas 좌표에서 target 크기로 변환.

    근사 변환 — PoC 수준 정밀도.

    >>> from PIL import Image
    >>> m = Image.new("L", (100,100), 0)
    >>> m.paste(Image.new("L",(50,50),255),(25,25))
    >>> s = scale_mask_to_target(m, 100, 100, 200, 200)
    >>> s.size
    (200, 200)
    """
    if (src_canvas_w, src_canvas_h) == (target_w, target_h):
        return mask
    return mask.resize((target_w, target_h), Image.LANCZOS)


def build_mask_union(
    masks: list[dict],
    target_roles: set[str],
    canvas_w: int,
    canvas_h: int,
) -> "Image.Image":
    """지정 role의 mask를 합산(합집합)한 L-mode image 반환.

    >>> from PIL import Image
    >>> m1 = {"role":"product","_maskImg": Image.new("L",(100,100),255), "maskPath": None}
    >>> union = build_mask_union([m1], {"product"}, 100, 100)
    >>> union.getpixel((50,50))
    255
    """
    import os
    from PIL import ImageChops

    union = Image.new("L", (canvas_w, canvas_h), 0)
    for mask in masks:
        if mask.get("role") not in target_roles:
            continue
        img = mask.get("_maskImg")
        if img is None:
            path = mask.get("maskPath")
            if path and os.path.exists(path):
                try:
                    img = Image.open(path).convert("L")
                except Exception:
                    continue
            else:
                continue
        if img.size != (canvas_w, canvas_h):
            img = img.resize((canvas_w, canvas_h), Image.LANCZOS)
        union = ImageChops.lighter(union, img)
    return union


def compute_mask_quality(
    source: str,
    bbox: dict,
    canvas_w: int,
    canvas_h: int,
    product_score: float | None = None,
) -> dict:
    """mask quality 지표 계산.

    반환: edgeSharpness, alphaCoverage, leakRisk, sourcePriority, areaRatio, overallScore
    """
    priority = SOURCE_PRIORITY.get(source, 0.30)
    area = bbox.get("width", 0) * bbox.get("height", 0)
    area_ratio = round(area / max(canvas_w * canvas_h, 1), 4)

    if source == "psd_alpha":
        edge_sharpness = 0.80
        alpha_coverage = 0.85
        leak_risk = 0.10
    elif source == "psd_layer_mask":
        edge_sharpness = 0.85
        alpha_coverage = 0.90
        leak_risk = 0.08
    elif source == "object_bbox_coarse":
        edge_sharpness = 0.20
        alpha_coverage = 1.00
        leak_risk = 0.40
    else:
        edge_sharpness = 0.35
        alpha_coverage = 0.70
        leak_risk = 0.30

    overall = (
        priority         * 0.40
        + (1.0 - leak_risk)  * 0.30
        + edge_sharpness     * 0.20
        + alpha_coverage     * 0.10
    )
    feather_applied = source in ("object_bbox_coarse", "visual_context")
    if source == "psd_alpha":
        edge_quality = "sharp"
        post_process = False
    elif source == "psd_layer_mask":
        edge_quality = "sharp"
        post_process = False
    elif source in ("object_bbox_coarse", "visual_context"):
        edge_quality = "coarse"
        post_process = True   # GaussianBlur feather applied
    else:
        edge_quality = "medium"
        post_process = False

    return {
        "edgeSharpness":       round(edge_sharpness, 3),
        "alphaCoverage":       round(alpha_coverage, 3),
        "leakRisk":            round(leak_risk, 3),
        "maskLeakRisk":        round(leak_risk, 3),
        "sourcePriority":      round(priority, 3),
        "areaRatio":           area_ratio,
        "overallScore":        round(overall, 3),
        "productCandidateScore": round(product_score, 1) if product_score is not None else None,
        "maskFeatherApplied":  feather_applied,
        "maskEdgeQuality":     edge_quality,
        "maskPostProcessApplied": post_process,
    }
