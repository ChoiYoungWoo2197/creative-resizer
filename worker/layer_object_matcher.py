"""4차-8: AI 객체 bbox ↔ PSD 레이어 매칭."""
import math

ROLE_KEYWORDS = {
    "background": ["배경", "background", "bg", "back", "배경이미지", "bkg", "base"],
    "title": ["타이틀", "제목", "title", "headline", "헤드라인", "main_text", "메인텍스트", "주제목", "메인카피", "copy"],
    "body_text": ["본문", "body", "text", "설명", "description", "내용", "서브카피", "sub_copy", "subcopy"],
    "main_image": ["이미지", "image", "img", "제품", "product", "사진", "photo", "main_img", "key_visual", "kv", "메인"],
    "cta": ["cta", "버튼", "button", "클릭", "신청", "지금", "구매", "바로가기", "btn"],
    "logo": ["로고", "logo", "brand", "브랜드", "bi"],
    "badge": ["배지", "badge", "태그", "tag", "신규", "할인", "new", "hot", "sale", "point"],
    "decoration": ["장식", "decoration", "deco", "패턴", "pattern", "line", "라인", "dot"],
}


def _iou(b1: dict, b2: dict) -> float:
    x1 = max(b1["x"], b2["x"])
    y1 = max(b1["y"], b2["y"])
    x2 = min(b1["x"] + b1["width"], b2["x"] + b2["width"])
    y2 = min(b1["y"] + b1["height"], b2["y"] + b2["height"])
    iw = max(0, x2 - x1)
    ih = max(0, y2 - y1)
    inter = iw * ih
    if inter == 0:
        return 0.0
    a1 = b1["width"] * b1["height"]
    a2 = b2["width"] * b2["height"]
    union = a1 + a2 - inter
    return inter / union if union > 0 else 0.0


def _center_dist_score(b1: dict, b2: dict, cw: int, ch: int) -> float:
    c1x = b1["x"] + b1["width"] / 2
    c1y = b1["y"] + b1["height"] / 2
    c2x = b2["x"] + b2["width"] / 2
    c2y = b2["y"] + b2["height"] / 2
    dx = (c1x - c2x) / max(cw, 1)
    dy = (c1y - c2y) / max(ch, 1)
    dist = math.sqrt(dx ** 2 + dy ** 2)
    return max(0.0, 1.0 - dist * 3)


def _name_score(layer_name: str, role: str, label: str) -> float:
    name_l = (layer_name or "").lower()
    for kw in ROLE_KEYWORDS.get(role, []):
        if kw in name_l:
            return 1.0
    label_words = (label or "").lower().split()
    for w in label_words:
        if len(w) >= 2 and w in name_l:
            return 0.5
    return 0.0


def _area_score(b_ai: dict, b_layer: dict) -> float:
    a1 = b_ai["width"] * b_ai["height"]
    a2 = b_layer["width"] * b_layer["height"]
    if a1 <= 0 or a2 <= 0:
        return 0.0
    return min(a1, a2) / max(a1, a2)


def match_objects_to_layers(
    ai_objects: list,
    psd_layers: list,
    canvas_w: int,
    canvas_h: int,
    artboard_box: dict = None,
) -> list:
    """
    ai_objects: bbox는 artboard-relative 좌표
    psd_layers: bbox는 canvas-absolute 좌표
    artboard_box: {x, y, width, height}
    반환: ai_objects에 매칭 결과 필드가 추가된 리스트
    """
    ox = artboard_box["x"] if artboard_box else 0
    oy = artboard_box["y"] if artboard_box else 0
    ab_w = artboard_box["width"] if artboard_box else canvas_w
    ab_h = artboard_box["height"] if artboard_box else canvas_h

    def in_ab(lb):
        cx = lb["x"] + lb["width"] / 2
        cy = lb["y"] + lb["height"] / 2
        return (ox <= cx <= ox + ab_w) and (oy <= cy <= oy + ab_h)

    ab_layers = [l for l in psd_layers if l.get("bbox") and in_ab(l["bbox"])]
    print(f"[Matcher] artboard_layers={len(ab_layers)}, total_layers={len(psd_layers)}")

    results = []
    for obj in ai_objects:
        ai_bbox = obj.get("bbox")
        if not ai_bbox or not isinstance(ai_bbox, dict):
            results.append({**obj, "matchedLayerId": None, "matchedLayerName": None,
                            "matchScore": 0.0, "matchStatus": "missing_layer"})
            continue

        # artboard-relative → canvas-absolute
        abs_bbox = {
            "x": ai_bbox["x"] + ox,
            "y": ai_bbox["y"] + oy,
            "width": ai_bbox["width"],
            "height": ai_bbox["height"],
        }

        best_score = -1.0
        best_layer = None
        for layer in ab_layers:
            lb = layer["bbox"]
            score = (
                _name_score(layer.get("name", ""), obj.get("role", ""), obj.get("label", "")) * 0.35
                + _iou(abs_bbox, lb) * 0.35
                + _center_dist_score(abs_bbox, lb, canvas_w, canvas_h) * 0.20
                + _area_score(abs_bbox, lb) * 0.10
            )
            if score > best_score:
                best_score = score
                best_layer = layer

        if best_layer and best_score >= 0.50:
            status = "ready" if best_score >= 0.75 else "matched_low_confidence"
        else:
            status = "missing_layer"
            best_layer = None
            best_score = 0.0

        results.append({
            **obj,
            "matchedLayerId": best_layer["id"] if best_layer else None,
            "matchedLayerName": best_layer["name"] if best_layer else None,
            "matchScore": round(best_score, 3),
            "matchStatus": status,
        })

    return results
