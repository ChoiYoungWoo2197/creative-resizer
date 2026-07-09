"""4차-9: 객체별 소스 이미지 해석.
source_type: layer_asset | bbox_crop | flat_background | dropped
"""
from PIL import Image


def resolve_sources(objects: list, psd_layers: list, artboard_img: "Image.Image",
                    artboard_box: dict | None = None) -> dict:
    """
    objects: PsdObjectAnalysis.objects 리스트 (dict 형태)
    psd_layers: parse_psd_layers() 반환 (_layer_obj 포함)
    artboard_img: 아트보드 합성 이미지 (artboard-local 좌표)
    artboard_box: {x, y, width, height} — canvas-global 오프셋 (미사용, 예비)
    반환: {obj_id: {"sourceType": str, "image": Image|None, "layerId": str|None}}
    """
    layer_map = {l["id"]: l for l in psd_layers}
    results = {}

    for obj in objects:
        obj_id = obj.get("id", "")
        role = obj.get("role", "unknown")
        match_status = obj.get("matchStatus", "missing_layer")
        matched_layer_id = obj.get("matchedLayerId")
        bbox = obj.get("bbox")

        # background → 아트보드 전체 이미지
        if role == "background":
            results[obj_id] = {"sourceType": "flat_background", "image": artboard_img.copy(), "layerId": None}
            continue

        # 매칭된 레이어가 있으면 layer_asset 시도
        if match_status in ("ready", "matched_low_confidence") and matched_layer_id:
            layer = layer_map.get(matched_layer_id)
            if layer and layer.get("_layer_obj"):
                try:
                    lobj = layer["_layer_obj"]
                    limg = lobj.composite()
                    if limg and limg.width > 0 and limg.height > 0:
                        results[obj_id] = {
                            "sourceType": "layer_asset",
                            "image": limg.convert("RGBA"),
                            "layerId": matched_layer_id,
                        }
                        continue
                except Exception as e:
                    print(f"[ObjectSource] layer_asset failed id={matched_layer_id}: {e}")

        # bbox_crop fallback
        if bbox and artboard_img:
            x1 = max(0, int(bbox.get("x", 0)))
            y1 = max(0, int(bbox.get("y", 0)))
            x2 = min(artboard_img.width, x1 + int(bbox.get("width", 0)))
            y2 = min(artboard_img.height, y1 + int(bbox.get("height", 0)))
            if x2 > x1 and y2 > y1:
                cropped = artboard_img.crop((x1, y1, x2, y2))
                results[obj_id] = {"sourceType": "bbox_crop", "image": cropped.convert("RGBA"), "layerId": None}
                continue

        # 드롭
        results[obj_id] = {"sourceType": "dropped", "image": None, "layerId": None}

    return results
