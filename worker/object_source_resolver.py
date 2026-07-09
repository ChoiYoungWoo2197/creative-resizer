"""4차-9: 객체별 소스 이미지 해석.
source_type: layer_asset | bbox_crop | flat_background | dominant_color | dropped
"""
from PIL import Image, ImageStat


def _dominant_color_bg(artboard_img: "Image.Image", size: tuple) -> "Image.Image":
    """아트보드 가장자리 영역 평균색으로 단색 배경 생성 (중복 합성 방지)."""
    try:
        w, h = artboard_img.size
        edge_px = max(10, min(w, h) // 10)
        top    = artboard_img.crop((0, 0, w, edge_px))
        bottom = artboard_img.crop((0, h - edge_px, w, h))
        left   = artboard_img.crop((0, 0, edge_px, h))
        right  = artboard_img.crop((w - edge_px, 0, w, h))
        merged = Image.new("RGBA", (w * 2, edge_px * 2 + h))
        merged.paste(top.resize((w, edge_px)), (0, 0))
        merged.paste(bottom.resize((w, edge_px)), (0, edge_px))
        stat = ImageStat.Stat(merged.convert("RGB"))
        r, g, b = int(stat.mean[0]), int(stat.mean[1]), int(stat.mean[2])
        bg = Image.new("RGBA", size, (r, g, b, 255))
    except Exception:
        bg = Image.new("RGBA", size, (240, 240, 240, 255))
    return bg


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
    canvas_size = (artboard_img.width, artboard_img.height) if artboard_img else (1, 1)
    results = {}

    for obj in objects:
        obj_id = obj.get("id", "")
        role = obj.get("role", "unknown")
        match_status = obj.get("matchStatus", "missing_layer")
        matched_layer_id = obj.get("matchedLayerId")
        bbox = obj.get("bbox")

        # background: matched layer_asset 우선 → 없으면 dominant_color (전체 composite 금지)
        if role == "background":
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
                        print(f"[ObjectSource] background layer_asset failed: {e}")
            # bbox_crop도 시도 (배경 bbox가 명시된 경우)
            if bbox and artboard_img:
                bx1 = max(0, int(bbox.get("x", 0)))
                by1 = max(0, int(bbox.get("y", 0)))
                bx2 = min(artboard_img.width, bx1 + int(bbox.get("width", artboard_img.width)))
                by2 = min(artboard_img.height, by1 + int(bbox.get("height", artboard_img.height)))
                if (bx2 - bx1) > artboard_img.width * 0.7 and (by2 - by1) > artboard_img.height * 0.7:
                    # 배경이 아트보드 70% 이상 덮으면 bbox_crop 허용
                    results[obj_id] = {
                        "sourceType": "bbox_crop",
                        "image": artboard_img.crop((bx1, by1, bx2, by2)).convert("RGBA"),
                        "layerId": None,
                    }
                    continue
            # fallback: dominant color
            results[obj_id] = {
                "sourceType": "dominant_color",
                "image": _dominant_color_bg(artboard_img, canvas_size) if artboard_img else Image.new("RGBA", canvas_size, (240, 240, 240, 255)),
                "layerId": None,
            }
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

        # bbox_crop fallback (레이어 매칭 실패해도 AI bbox 있으면 crop 시도)
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
