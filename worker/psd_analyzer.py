from psd_tools import PSDImage
from PIL import Image
import subprocess
from io import BytesIO
from psd_compat import open_psd_safe_with_patch


ARTBOARD_KEYWORDS = [
    "artboard", "아트보드", "artbd",
    "square", "horizontal", "vertical",
    "1200x1200", "1200x300", "300x1200",
    "1080x1080", "1080x1920", "728x90", "1250x560",
]

# 광고 결과물에 포함하면 안 되는 제작 가이드/TIP 영역 키워드
TIP_EXCLUDE_KEYWORDS = [
    "tip", "제작", "guide", "참고", "안내", "샘플", "sample", "draft", "tmp", "가이드",
]


def _classify_artboard_type(w: int, h: int) -> str:
    """너비/높이 비율로 아트보드 유형 분류."""
    if w <= 0 or h <= 0:
        return "unknown"
    ratio = w / h
    if abs(ratio - 1.0) < 0.15:
        return "square"       # 정방형 (예: 1200×1200)
    elif ratio < 0.65:
        return "vertical"     # 세로형 (예: 300×1200)
    elif ratio > 1.8:
        return "horizontal"   # 가로형 (예: 1200×300)
    else:
        return "custom"


def analyze_psd_file(file_path: str) -> dict:
    try:
        return _analyze_psd_file_inner(file_path)
    except Exception as e:
        print(f"[PSD] analyze_psd_file unexpected error: {e}")
        return {
            "width": 0,
            "height": 0,
            "hasArtboards": False,
            "artboards": [],
            "layers": [],
            "layerReadable": False,
            "layerCount": 0,
            "layerReadError": str(e),
            "layerReadErrorCode": "unexpected_error",
            "layerReflowAvailable": False,
            "psdParserEngine": "unknown",
            "psdCompatPatched": False,
        }


def _analyze_psd_file_inner(file_path: str) -> dict:
    psd, open_meta = open_psd_safe_with_patch(file_path)

    layer_readable = open_meta["success"]
    psd_parser_engine = open_meta.get("engine", "psd-tools")
    psd_compat_patched = open_meta.get("patchedRetry", False)
    layer_read_error = open_meta.get("error") if not layer_readable else None
    layer_read_error_code = open_meta.get("errorCode") if not layer_readable else None

    if not layer_readable:
        print(f"[PSD] psd-tools open failed ({layer_read_error_code}): {layer_read_error}")
        return {
            "width": 0,
            "height": 0,
            "hasArtboards": False,
            "artboards": [],
            "layers": [],
            "layerReadable": False,
            "layerCount": 0,
            "layerReadError": layer_read_error,
            "layerReadErrorCode": layer_read_error_code,
            "layerReflowAvailable": False,
            "psdParserEngine": psd_parser_engine,
            "psdCompatPatched": psd_compat_patched,
        }

    artboards = _extract_artboards(psd)
    if not artboards:
        artboards = _infer_artboards_from_groups(psd)
    has_artboards = bool(artboards)
    if not artboards:
        artboards = _infer_artboards_from_large_layers(psd)
    if not artboards:
        artboards = _fallback_single_artboard(psd)
    layers = _extract_layers(psd)
    layer_count = len(layers)
    layer_reflow_available = _check_layer_reflow_available(layers)
    reflow_diag = get_reflow_diagnostic(layers)

    return {
        "width": psd.width,
        "height": psd.height,
        "hasArtboards": has_artboards,
        "artboards": artboards,
        "layers": layers,
        "layerReadable": True,
        "layerCount": layer_count,
        "layerReadError": None,
        "layerReadErrorCode": None,
        "layerReflowAvailable": layer_reflow_available,
        "reflowDetectedRoles": reflow_diag["detectedRoles"],
        "reflowMissingRoles":  reflow_diag["missingRoles"],
        "psdParserEngine": psd_parser_engine,
        "psdCompatPatched": psd_compat_patched,
    }


def _extract_artboards(psd) -> list:
    """psd-tools tagged_blocks 기반 아트보드 추출."""
    artboards = []
    try:
        from psd_tools.constants import Tag
        artboard_tags = set()
        for attr in ("ARTBOARD_DATA1", "ARTBOARD_DATA2", "ARTBOARD_DATA3"):
            if hasattr(Tag, attr):
                artboard_tags.add(getattr(Tag, attr))

        for idx, layer in enumerate(psd):
            if not (hasattr(layer, "is_group") and layer.is_group()):
                continue
            name_lower = (layer.name or "").lower()
            if any(k in name_lower for k in TIP_EXCLUDE_KEYWORDS):
                continue
            try:
                tagged = getattr(layer, "tagged_blocks", None) or {}
                if any(t in tagged for t in artboard_tags):
                    w = int(layer.width)
                    h = int(layer.height)
                    if w > 0 and h > 0:
                        artboards.append({
                            "id": f"artboard_{idx}",
                            "name": layer.name,
                            "x": int(layer.left),
                            "y": int(layer.top),
                            "width": w,
                            "height": h,
                            "ratio": w / h,
                            "artboardType": _classify_artboard_type(w, h),
                            "source": "artboard_tag",
                        })
            except Exception:
                continue
    except Exception:
        pass
    return artboards


def _infer_artboards_from_groups(psd) -> list:
    """레이어 그룹 이름으로 아트보드 추정."""
    artboards = []
    for idx, layer in enumerate(psd):
        name = (layer.name or "").lower()
        if any(k in name for k in TIP_EXCLUDE_KEYWORDS):
            continue
        if not any(k in name for k in ARTBOARD_KEYWORDS):
            continue
        try:
            w = int(layer.width)
            h = int(layer.height)
            if w <= 0 or h <= 0:
                continue
            artboards.append({
                "id": f"artboard_{idx}",
                "name": layer.name,
                "x": int(layer.left),
                "y": int(layer.top),
                "width": w,
                "height": h,
                "ratio": w / h,
                "artboardType": _classify_artboard_type(w, h),
                "source": "group_name",
            })
        except Exception:
            continue
    return artboards


def _infer_artboards_from_large_layers(psd) -> list:
    """Top-level 그룹 레이어 bbox 기반 광고 영역 추정.
    아트보드 메타/그룹명 키워드 모두 없을 때 최후 cascade 시도.
    제작 TIP 영역(이름 기반)과 전체 배경 레이어는 제외한다.
    """
    canvas_w = psd.width
    canvas_h = psd.height
    canvas_area = canvas_w * canvas_h
    candidates = []
    seen: set = set()

    for idx, layer in enumerate(psd):
        if not (hasattr(layer, "is_group") and layer.is_group()):
            continue
        name_lower = (layer.name or "").lower()
        if any(k in name_lower for k in TIP_EXCLUDE_KEYWORDS):
            continue
        try:
            x = int(layer.left)
            y = int(layer.top)
            w = max(0, int(layer.right) - x)
            h = max(0, int(layer.bottom) - y)
        except Exception:
            continue
        if w <= 0 or h <= 0:
            continue

        area = w * h
        # 캔버스 면적의 3% 이상
        if area < canvas_area * 0.03:
            continue
        # 전체 캔버스 크기에 너무 가깝다면 배경 레이어로 간주 → skip
        if w >= canvas_w * 0.92 and h >= canvas_h * 0.92:
            continue

        # 캔버스 경계 보정
        cx = max(0, x)
        cy = max(0, y)
        cw = min(w, canvas_w - cx)
        ch = min(h, canvas_h - cy)
        if cw <= 0 or ch <= 0:
            continue

        key = (cx, cy, cw, ch)
        if key in seen:
            continue
        seen.add(key)

        candidates.append({
            "id": f"inferred_layer_{idx}",
            "name": layer.name or f"영역_{idx}",
            "x": cx,
            "y": cy,
            "width": cw,
            "height": ch,
            "ratio": round(cw / ch, 4),
            "artboardType": _classify_artboard_type(cw, ch),
            "source": "layer_bbox",
        })

    return candidates


def _fallback_single_artboard(psd) -> list:
    return [{
        "id": "full_canvas",
        "name": "전체 캔버스",
        "x": 0,
        "y": 0,
        "width": psd.width,
        "height": psd.height,
        "ratio": psd.width / psd.height,
        "artboardType": "full-canvas",
        "source": "fallback",
    }]


def _extract_layers(psd) -> list:
    layers = []
    try:
        for idx, layer in enumerate(psd.descendants()):
            if not layer.is_visible():
                continue
            try:
                bbox = [int(layer.left), int(layer.top), int(layer.right), int(layer.bottom)]
            except Exception:
                bbox = [0, 0, 0, 0]
            layers.append({
                "id": f"layer_{idx}",
                "name": layer.name,
                "type": _detect_layer_type(layer),
                "visible": True,
                "bbox": bbox,
                "role": _infer_layer_role(layer.name),
            })
    except Exception:
        pass
    return layers


def _detect_layer_type(layer) -> str:
    try:
        kind = str(layer.kind)
        for t in ("pixel", "type", "shape", "smartobject", "group"):
            if t in kind.lower():
                return t
    except Exception:
        pass
    return "pixel"


def _infer_layer_role(name: str) -> str:
    n = (name or "").lower()
    if any(k in n for k in ["bg", "background", "배경"]):
        return "background"
    if any(k in n for k in ["logo", "로고"]):
        return "logo"
    if any(k in n for k in ["title", "headline", "main", "제목", "타이틀"]):
        return "headline"
    if any(k in n for k in ["cta", "button", "date", "기간", "신청"]):
        return "cta"
    if any(k in n for k in ["product", "person", "model", "visual", "제품", "모델"]):
        return "visual"
    return "unknown"


def _check_layer_reflow_available(layers: list) -> bool:
    """레이어 재배치 가능 여부. 구 role명(headline/visual) + 신 role명(title/main_image) 모두 인정."""
    roles = set(l.get("role") for l in layers)
    has_headline = any(r in roles for r in ["headline", "title"])
    has_visual = any(r in roles for r in ["visual", "product", "person", "main_image", "main"])
    has_cta = "cta" in roles
    return has_headline and (has_visual or has_cta)


def get_reflow_diagnostic(layers: list) -> dict:
    """레이어 재배치 비활성화 원인 진단. 감지된 role 목록과 누락 role 반환."""
    roles = set(l.get("role") for l in layers)
    roles.discard("unknown")
    detected = sorted(roles)

    required_old = {"headline", "visual"}  # 구 시스템
    required_new = {"title", "main_image"}  # 신 시스템
    has_headline = any(r in roles for r in ["headline", "title"])
    has_visual   = any(r in roles for r in ["visual", "product", "person", "main_image", "main"])
    has_cta      = "cta" in roles

    missing = []
    if not has_headline:
        missing.append("title(메인카피)")
    if not has_visual:
        missing.append("main_image(제품/인물)")
    if not has_cta:
        missing.append("cta(행동버튼)")

    return {
        "detectedRoles": detected,
        "missingRoles":  missing,
        "available":     has_headline and (has_visual or has_cta),
    }


def select_best_artboard(artboards: list, target_w: int, target_h: int) -> dict | None:
    if not artboards:
        return None
    target_ratio = target_w / target_h
    target_area = target_w * target_h
    best = None
    best_score = float("inf")
    for ab in artboards:
        ab_h = ab.get("height", 0)
        if ab_h == 0:
            continue
        ab_w = ab.get("width", 0)
        ab_ratio = ab_w / ab_h
        ab_area = ab_w * ab_h
        ratio_diff = abs(target_ratio - ab_ratio)
        size_diff = abs(target_area - ab_area) / max(target_area, ab_area)
        score = ratio_diff * 0.7 + size_diff * 0.3
        if score < best_score:
            best_score = score
            best = ab
    return best


def render_artboard_from_composed(composed: Image.Image, artboard: dict) -> Image.Image:
    """합성된 전체 PSD 이미지에서 아트보드 영역을 crop."""
    x = artboard.get("x", 0)
    y = artboard.get("y", 0)
    w = artboard.get("width", composed.width)
    h = artboard.get("height", composed.height)
    x = max(0, x)
    y = max(0, y)
    x2 = min(composed.width, x + w)
    y2 = min(composed.height, y + h)
    return composed.crop((x, y, x2, y2))


def is_valid_rendered_image(img) -> bool:
    """렌더링 결과가 유효한지 검증 (None, 너무 작은 이미지, 빈 이미지 제외)."""
    if img is None:
        return False
    try:
        w, h = img.size
    except Exception:
        return False
    if w < 10 or h < 10:
        return False
    if w * h < 1000:
        return False
    return True


def clamp_crop_box(x: int, y: int, w: int, h: int, canvas_w: int, canvas_h: int):
    """crop 좌표를 캔버스 범위 안으로 보정. 유효하지 않으면 None 반환."""
    x1 = max(0, x)
    y1 = max(0, y)
    x2 = min(canvas_w, x + w)
    y2 = min(canvas_h, y + h)
    if x2 <= x1 or y2 <= y1:
        return None
    return (x1, y1, x2, y2)


def safe_render_artboard(file_path: str, artboard: dict) -> tuple:
    """아트보드 렌더링 시도. (img, mode) 반환. 실패 시 (None, 'failed')."""
    try:
        psd, open_meta = open_psd_safe_with_patch(file_path)
        if not open_meta["success"]:
            return None, "failed"
        composed = psd.composite()
        if not is_valid_rendered_image(composed):
            return None, "failed"
        box = clamp_crop_box(
            artboard.get("x", 0), artboard.get("y", 0),
            artboard.get("width", composed.width), artboard.get("height", composed.height),
            composed.width, composed.height,
        )
        if box is None:
            return None, "failed"
        rendered = composed.crop(box)
        if not is_valid_rendered_image(rendered):
            return None, "failed"
        return rendered.convert("RGBA"), "artboard"
    except Exception as e:
        print(f"[PSD] artboard render failed: {e}")
        return None, "failed"


def fallback_flatten_psd(file_path: str) -> tuple:
    """psd-tools composite → ImageMagick 4단계 fallback pipeline.
    반환: (img, mode, meta)  mode는 backward compat 문자열, meta는 render 상세 dict."""
    from resizer import load_psd_as_flat_image
    img, meta = load_psd_as_flat_image(file_path)
    if img is not None:
        render_source = meta["renderSource"]
        mode = "full-canvas" if render_source == "psd_tools_composite" else "imagemagick-flatten"
        return img, mode, meta
    return None, "failed", meta
