from psd_tools import PSDImage
from PIL import Image
import subprocess
from io import BytesIO


ARTBOARD_KEYWORDS = [
    "artboard", "아트보드", "artbd",
    "square", "horizontal", "vertical",
    "1200x1200", "1200x300", "300x1200",
    "1080x1080", "1080x1920", "728x90", "1250x560",
]


def analyze_psd_file(file_path: str) -> dict:
    try:
        psd = PSDImage.open(file_path)
    except Exception as e:
        print(f"[PSD] psd-tools open failed ({e}), using full_canvas fallback")
        return {
            "width": 0,
            "height": 0,
            "hasArtboards": False,
            "artboards": [],
            "layers": [],
        }
    artboards = _extract_artboards(psd)
    if not artboards:
        artboards = _infer_artboards_from_groups(psd)
    has_artboards = bool(artboards)
    if not artboards:
        artboards = _fallback_single_artboard(psd)
    layers = _extract_layers(psd)
    return {
        "width": psd.width,
        "height": psd.height,
        "hasArtboards": has_artboards,
        "artboards": artboards,
        "layers": layers,
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
            })
        except Exception:
            continue
    return artboards


def _fallback_single_artboard(psd) -> list:
    return [{
        "id": "full_canvas",
        "name": "전체 캔버스",
        "x": 0,
        "y": 0,
        "width": psd.width,
        "height": psd.height,
        "ratio": psd.width / psd.height,
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
        psd = PSDImage.open(file_path)
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
    """psd-tools composite → ImageMagick 순서로 fallback. (img, mode) 반환."""
    try:
        psd = PSDImage.open(file_path)
        img = psd.composite()
        if is_valid_rendered_image(img):
            return img.convert("RGBA"), "full-canvas"
    except Exception as e:
        print(f"[PSD] psd-tools flatten failed: {e}")
    try:
        result = subprocess.run(
            ["convert", f"{file_path}[0]", "-flatten", "PNG:-"],
            capture_output=True, timeout=120, check=True,
        )
        img = Image.open(BytesIO(result.stdout))
        img.load()
        return img.convert("RGBA"), "imagemagick-flatten"
    except Exception as e:
        print(f"[PSD] ImageMagick fallback failed: {e}")
    return None, "failed"
