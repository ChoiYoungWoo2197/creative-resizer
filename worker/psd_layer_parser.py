"""4차-5: PSD 레이어 파서.
visible layer 순회 → bbox / opacity / depth / preview PNG 추출.
"""
from PIL import Image
import os


def _detect_layer_type(layer) -> str:
    try:
        kind = str(layer.kind).lower()
        for t in ("type", "group", "smartobject", "shape", "pixel"):
            if t in kind:
                return t
    except Exception:
        pass
    return "pixel"


def parse_psd_layers(psd, job_dir: str) -> list:
    """PSD 오브젝트에서 visible 레이어 목록 추출.
    반환: list of layer_item dict (직렬화 가능 + _layer_obj 내부 키 포함)
    """
    layer_dir = os.path.join(job_dir, "layers")
    os.makedirs(layer_dir, exist_ok=True)

    canvas_w = psd.width
    canvas_h = psd.height
    items = []

    for idx, layer in enumerate(psd.descendants()):
        try:
            if not layer.is_visible():
                continue

            lx = int(layer.left)
            ly = int(layer.top)
            lw = max(0, int(layer.right) - lx)
            lh = max(0, int(layer.bottom) - ly)
            if lw <= 0 or lh <= 0:
                continue

            layer_id = f"layer_{idx:03d}"
            name = layer.name or layer_id

            # depth: parent chain 깊이
            depth = 0
            try:
                p = layer.parent
                while p and hasattr(p, "parent") and p.parent is not None:
                    depth += 1
                    p = p.parent
            except Exception:
                pass

            try:
                opacity = int(layer.opacity)
            except Exception:
                opacity = 100

            layer_type = _detect_layer_type(layer)

            # preview PNG 저장
            preview_path = None
            try:
                limg = layer.composite()
                if limg and limg.width > 0 and limg.height > 0:
                    preview_name = f"{layer_id}.png"
                    preview_path = os.path.join(layer_dir, preview_name)
                    limg.convert("RGBA").save(preview_path)
            except Exception as e:
                print(f"[LayerParser] preview failed {name}: {e}")

            items.append({
                "id":           layer_id,
                "name":         name,
                "type":         layer_type,
                "visible":      True,
                "opacity":      opacity,
                "depth":        depth,
                "bbox":         {"x": lx, "y": ly, "width": lw, "height": lh},
                "previewPath":  preview_path,
                "canvasWidth":  canvas_w,
                "canvasHeight": canvas_h,
                "_layer_obj":   layer,   # compositor 전용 (직렬화 제외)
            })
        except Exception as e:
            print(f"[LayerParser] skip idx={idx}: {e}")

    return items
