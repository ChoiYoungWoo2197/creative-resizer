"""4차-5: PSD 레이어 파서.
visible layer 순회 → bbox / opacity / depth / preview PNG 추출.
"""
import os
import re


def _detect_layer_type(layer) -> str:
    try:
        kind = str(layer.kind).lower()
        for t in ("type", "group", "smartobject", "shape", "pixel"):
            if t in kind:
                return t
    except Exception:
        pass
    return "pixel"


def _collect_descendant_ids(layer) -> set:
    """그룹 레이어의 모든 하위 자손 id() 집합 반환."""
    ids = set()
    try:
        for child in layer:
            ids.add(id(child))
            ids.update(_collect_descendant_ids(child))
    except Exception:
        pass
    return ids


def parse_psd_layers(psd, job_dir: str) -> list:
    """PSD 오브젝트에서 visible 레이어 목록 추출.
    그룹 레이어가 preview 렌더에 성공하면 그 자손은 건너뜀 (중복 합성 방지).
    반환: list of layer_item dict (직렬화 가능 + _layer_obj 내부 키 포함)
    """
    layer_dir = os.path.join(job_dir, "layers")
    os.makedirs(layer_dir, exist_ok=True)

    canvas_w = psd.width
    canvas_h = psd.height
    items = []
    covered_ids: set = set()  # 이미 그룹으로 커버된 자손 layer id()들

    for idx, layer in enumerate(psd.descendants()):
        try:
            if not layer.is_visible():
                continue

            # 부모 그룹이 이미 렌더되어 커버됨 → 자손 건너뜀
            if id(layer) in covered_ids:
                continue

            lx = int(layer.left)
            ly = int(layer.top)
            lw = max(0, int(layer.right) - lx)
            lh = max(0, int(layer.bottom) - ly)
            if lw <= 0 or lh <= 0:
                continue

            name = layer.name or f"layer_{idx:03d}"
            safe_name = re.sub(r'[^\w\-]', '_', name)[:32].strip('_') or f"layer_{idx:03d}"
            layer_id = f"{safe_name}_{lx}_{ly}"

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

            # 그룹이 preview 렌더에 성공했으면 모든 자손을 covered로 등록
            if layer_type == "group" and preview_path is not None:
                covered_ids.update(_collect_descendant_ids(layer))

        except Exception as e:
            print(f"[LayerParser] skip idx={idx}: {e}")

    return items
