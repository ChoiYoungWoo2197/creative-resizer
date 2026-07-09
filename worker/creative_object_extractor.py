"""2단계: CreativeObjectSet 생성기.

PSD layer parser 결과 + AI object analysis 결과를 결합해 CreativeObjectSet을 만든다.

- AI + PSD 레이어 모두 있을 때: 6-component 점수로 greedy 매칭 → layer asset 우선 추출
- AI만 있을 때: ai_bbox_crop으로 fallback (qualityRisk="high")
- PSD만 있을 때: 레이어명 키워드로 역할 추론
- 어느 쪽도 없을 때: 빈 세트 + warning

함수 분류:
  pure (unit-testable): normalize_role, classify_layer_role, compute_iou,
                         compute_center_distance_score, match_ai_objects_to_layers
  orchestration:        extract_object_assets, build_creative_object_set
"""

import math
import os
import re
import shutil

# ─── 역할 정의 ────────────────────────────────────────────────────────────────

# AI 모델 raw role → 정규화 역할
AI_ROLE_MAP: dict[str, str] = {
    "background": "background",
    "main_image": "main_image",
    "image":      "main_image",
    "product":    "main_image",
    "person":     "person",
    "model":      "person",
    "headline":   "headline",
    "title":      "headline",
    "header":     "headline",
    "body_text":  "body_text",
    "body":       "body_text",
    "text":       "body_text",
    "copy":       "body_text",
    "description":"body_text",
    "price":      "price",
    "pricing":    "price",
    "discount":   "discount",
    "sale":       "discount",
    "coupon":     "discount",
    "cta":        "cta",
    "button":     "cta",
    "logo":       "logo",
    "brand":      "logo",
    "badge":      "badge",
    "tag":        "badge",
    "decoration": "decoration",
    "deco":       "decoration",
    "ornament":   "decoration",
    "unknown":    "unknown",
}

# 레이어 이름 키워드 → 역할 (길고 구체적인 키워드를 앞에 배치)
ROLE_KEYWORDS: dict[str, list[str]] = {
    "background": ["backdrop", "background", "배경이미지", "배경", "base", "back", "bkg", "bg"],
    "cta":        ["바로가기", "더알아보기", "자세히보기", "자세히", "구매하기", "신청하기",
                   "button", "cta", "더보기", "구매", "신청", "클릭", "btn", "more"],
    "headline":   ["메인카피", "main_copy", "maincopy", "headline", "타이틀", "제목",
                   "main copy", "title", "copy"],
    "body_text":  ["서브카피", "sub_copy", "subcopy", "description", "설명", "본문", "body", "desc"],
    "logo":       ["logo", "로고", "brand", "ci", "bi"],
    "price":      ["price", "가격", "금액"],
    "discount":   ["discount", "coupon", "benefit", "쿠폰", "혜택", "할인", "event", "sale"],
    "main_image": ["key_visual", "keyvisual", "key visual", "제품이미지", "main_img",
                   "product", "photo", "image", "인물", "model", "hero", "메인", "제품",
                   "main", "kv", "img"],
    "person":     ["person", "model", "인물", "사람"],
    "badge":      ["badge", "sticker", "스티커", "배지", "태그", "new", "hot", "tag"],
    "decoration": ["decoration", "ornament", "sparkle", "pattern", "라인", "장식",
                   "deco", "shape", "dot", "line", "icon"],
}

# 역할별 레이아웃 제약 규칙
ROLE_PROPERTIES: dict[str, dict] = {
    "background": dict(importance="priority", canCrop=True,  canDrop=False,
                       mustBeReadable=False, mustBeInsideSafeZone=False,
                       minScale=0.50, maxScale=2.00, keepAspectRatio=False),
    "main_image": dict(importance="required", canCrop=True,  canDrop=False,
                       mustBeReadable=False, mustBeInsideSafeZone=False,
                       minScale=0.60, maxScale=1.50, keepAspectRatio=True),
    "person":     dict(importance="required", canCrop=True,  canDrop=False,
                       mustBeReadable=False, mustBeInsideSafeZone=False,
                       minScale=0.60, maxScale=1.50, keepAspectRatio=True),
    "headline":   dict(importance="required", canCrop=False, canDrop=False,
                       mustBeReadable=True,  mustBeInsideSafeZone=True,
                       minScale=0.75, maxScale=1.25, keepAspectRatio=True),
    "body_text":  dict(importance="priority", canCrop=False, canDrop=True,
                       mustBeReadable=True,  mustBeInsideSafeZone=True,
                       minScale=0.75, maxScale=1.10, keepAspectRatio=True),
    "price":      dict(importance="priority", canCrop=False, canDrop=False,
                       mustBeReadable=True,  mustBeInsideSafeZone=True,
                       minScale=0.75, maxScale=1.25, keepAspectRatio=True),
    "discount":   dict(importance="priority", canCrop=False, canDrop=True,
                       mustBeReadable=True,  mustBeInsideSafeZone=True,
                       minScale=0.75, maxScale=1.25, keepAspectRatio=True),
    "cta":        dict(importance="required", canCrop=False, canDrop=False,
                       mustBeReadable=True,  mustBeInsideSafeZone=True,
                       minScale=0.75, maxScale=1.25, keepAspectRatio=True),
    "logo":       dict(importance="required", canCrop=False, canDrop=False,
                       mustBeReadable=True,  mustBeInsideSafeZone=True,
                       minScale=0.75, maxScale=1.25, keepAspectRatio=True),
    "badge":      dict(importance="optional", canCrop=False, canDrop=True,
                       mustBeReadable=True,  mustBeInsideSafeZone=False,
                       minScale=0.75, maxScale=1.25, keepAspectRatio=True),
    "decoration": dict(importance="optional", canCrop=True,  canDrop=True,
                       mustBeReadable=False, mustBeInsideSafeZone=False,
                       minScale=0.50, maxScale=1.50, keepAspectRatio=False),
    "unknown":    dict(importance="optional", canCrop=True,  canDrop=True,
                       mustBeReadable=False, mustBeInsideSafeZone=False,
                       minScale=0.50, maxScale=2.00, keepAspectRatio=False),
}

# 실제로 없는 경우 warning만 남기고 실패하지 않는 필수 역할
REQUIRED_ROLES: frozenset[str] = frozenset({"cta", "headline", "logo", "main_image"})


# ─── pure functions (unit-testable) ──────────────────────────────────────────

def normalize_role(raw_role: str, layer_name: str = "") -> str:
    """AI raw role 또는 레이어명으로부터 정규화된 역할 반환.

    우선순위: AI_ROLE_MAP → layer_name 키워드 → "unknown"

    >>> normalize_role("title")
    'headline'
    >>> normalize_role("button", "")
    'cta'
    >>> normalize_role("unknown_xyz", "cta_button")
    'cta'
    >>> normalize_role("", "배경이미지")
    'background'
    """
    if raw_role:
        mapped = AI_ROLE_MAP.get(raw_role.strip().lower())
        if mapped:
            return mapped

    if layer_name:
        layer_role, score = classify_layer_role(layer_name)
        if layer_role != "unknown" and score >= 0.3:
            return layer_role

    return "unknown"


def classify_layer_role(layer_name: str) -> tuple[str, float]:
    """레이어 이름 키워드로부터 역할과 신뢰도 추론.

    긴 키워드(더 구체적)가 먼저 매칭되도록 ROLE_KEYWORDS 각 리스트 앞에 배치됨.
    반환: (role, confidence_score 0~1.0)

    >>> classify_layer_role("btn_cta_apply")
    ('cta', 1.0)
    >>> classify_layer_role("bg_blue")
    ('background', 1.0)
    >>> classify_layer_role("random_layer_xyz")[0]
    'unknown'
    """
    if not layer_name:
        return "unknown", 0.0

    name_l = layer_name.lower()
    best_role = "unknown"
    best_score = 0.0

    for role, keywords in ROLE_KEYWORDS.items():
        for kw in keywords:
            kw_l = kw.lower()
            if kw_l not in name_l:
                continue
            # 단어 경계 매칭이면 1.0, 부분 포함이면 0.75
            escaped = re.escape(kw_l)
            boundary = r'(?<![a-z0-9가-힣])' + escaped + r'(?![a-z0-9가-힣])'
            score = 1.0 if re.search(boundary, name_l) else 0.75
            if score > best_score:
                best_score = score
                best_role = role
            break  # 역할당 첫 히트로 충분 (키워드는 구체→일반 순 배치됨)

    return best_role, best_score


def compute_iou(box_a: dict, box_b: dict) -> float:
    """두 bbox 간 IoU (Intersection over Union).

    bbox 형식: {x, y, width, height}

    >>> compute_iou({"x":0,"y":0,"width":10,"height":10}, {"x":5,"y":5,"width":10,"height":10})
    0.14285714285714285
    >>> compute_iou({"x":0,"y":0,"width":10,"height":10}, {"x":20,"y":20,"width":5,"height":5})
    0.0
    """
    x1 = max(box_a["x"], box_b["x"])
    y1 = max(box_a["y"], box_b["y"])
    x2 = min(box_a["x"] + box_a["width"],  box_b["x"] + box_b["width"])
    y2 = min(box_a["y"] + box_a["height"], box_b["y"] + box_b["height"])
    iw = max(0, x2 - x1)
    ih = max(0, y2 - y1)
    inter = iw * ih
    if inter == 0:
        return 0.0
    union = (box_a["width"] * box_a["height"]
             + box_b["width"] * box_b["height"]
             - inter)
    return inter / union if union > 0 else 0.0


def compute_center_distance_score(
    box_a: dict, box_b: dict, canvas_w: int, canvas_h: int
) -> float:
    """두 bbox 중심점의 정규화된 거리 점수 (가까울수록 1.0).

    canvas 대각선 대비 거리를 0~1로 환산한 뒤 반전.

    >>> score = compute_center_distance_score(
    ...     {"x":0,"y":0,"width":10,"height":10},
    ...     {"x":0,"y":0,"width":10,"height":10}, 100, 100)
    >>> score == 1.0
    True
    """
    cx_a = box_a["x"] + box_a["width"]  / 2
    cy_a = box_a["y"] + box_a["height"] / 2
    cx_b = box_b["x"] + box_b["width"]  / 2
    cy_b = box_b["y"] + box_b["height"] / 2
    dx = (cx_a - cx_b) / max(canvas_w, 1)
    dy = (cy_a - cy_b) / max(canvas_h, 1)
    dist = math.sqrt(dx ** 2 + dy ** 2)
    return max(0.0, 1.0 - dist * 3)


# ─── 내부 scoring helpers ─────────────────────────────────────────────────────

def _role_keyword_score(layer_name: str, role: str) -> float:
    """레이어 이름이 해당 역할의 키워드를 포함하는지 (0 / 1)."""
    if not layer_name or not role:
        return 0.0
    name_l = layer_name.lower()
    for kw in ROLE_KEYWORDS.get(role, []):
        if kw.lower() in name_l:
            return 1.0
    return 0.0


def _layer_name_score(layer_name: str, ai_label: str) -> float:
    """AI label 단어가 레이어 이름에 포함되는 비율 (0~1)."""
    if not layer_name or not ai_label:
        return 0.0
    name_l = layer_name.lower()
    words = re.findall(r'[a-z가-힣]+', ai_label.lower())
    if not words:
        return 0.0
    hits = sum(1 for w in words if len(w) >= 2 and w in name_l)
    return hits / len(words)


def _size_similarity_score(box_a: dict, box_b: dict) -> float:
    """면적 유사도 (0~1, 두 bbox 넓이 비율의 min/max)."""
    a1 = max(1, box_a["width"] * box_a["height"])
    a2 = max(1, box_b["width"] * box_b["height"])
    return min(a1, a2) / max(a1, a2)


def _zindex_compatibility_score(layer_depth: int, role: str) -> float:
    """레이어 깊이와 역할의 일반적 z-레벨 호환성 (0~1).

    background는 depth=0 선호, decoration은 무관, 나머지는 1~3 선호.
    """
    if role == "background":
        return 1.0 if layer_depth == 0 else max(0.0, 1.0 - layer_depth * 0.25)
    if role == "decoration":
        return 0.8
    return 1.0 if 0 < layer_depth <= 3 else 0.6


def _compute_match_score(
    ai_bbox_abs: dict,
    ai_role: str,
    ai_label: str,
    layer: dict,
    canvas_w: int,
    canvas_h: int,
) -> float:
    """6-component match score.

    matchScore =
      bboxIoU                * 0.40
    + centerDistanceScore    * 0.15
    + roleKeywordScore       * 0.20
    + layerNameScore         * 0.15
    + sizeSimilarityScore    * 0.05
    + zIndexCompatScore      * 0.05
    """
    lb = layer["bbox"]
    return (
        compute_iou(ai_bbox_abs, lb)                                          * 0.40
        + compute_center_distance_score(ai_bbox_abs, lb, canvas_w, canvas_h)  * 0.15
        + _role_keyword_score(layer.get("name", ""), ai_role)                 * 0.20
        + _layer_name_score(layer.get("name", ""), ai_label)                  * 0.15
        + _size_similarity_score(ai_bbox_abs, lb)                             * 0.05
        + _zindex_compatibility_score(layer.get("depth", 0), ai_role)        * 0.05
    )


# ─── 매칭 ─────────────────────────────────────────────────────────────────────

def match_ai_objects_to_layers(
    ai_objects: list[dict],
    layers: list[dict],
    canvas_w: int,
    canvas_h: int,
    artboard_box: dict | None = None,
) -> list[dict]:
    """AI object와 PSD 레이어를 greedy 최대 매칭.

    ai_objects의 bbox는 artboard-relative 좌표.
    layers의 bbox는 canvas-absolute 좌표.
    artboard_box: {x, y, width, height} — AI bbox를 canvas-absolute로 변환하는 오프셋.

    반환: [{ai_obj, layer|None, score, match_status, abs_bbox}]
      match_status: "ready" (≥0.75) | "matched_low_confidence" (0.30~0.75) | "missing_layer" (<0.30)
    """
    ox = artboard_box["x"]      if artboard_box else 0
    oy = artboard_box["y"]      if artboard_box else 0
    ab_w = artboard_box["width"]  if artboard_box else canvas_w
    ab_h = artboard_box["height"] if artboard_box else canvas_h

    def _in_artboard(layer: dict) -> bool:
        lb = layer.get("bbox", {})
        cx = lb.get("x", 0) + lb.get("width",  0) / 2
        cy = lb.get("y", 0) + lb.get("height", 0) / 2
        return (ox <= cx <= ox + ab_w) and (oy <= cy <= oy + ab_h)

    candidate_layers = [l for l in layers if l.get("bbox") and _in_artboard(l)]

    # 전체 (ai_obj, layer) 쌍 점수 계산
    scored_pairs: list[tuple[float, int, int, dict, dict, dict]] = []
    for obj in ai_objects:
        ai_bbox = obj.get("bbox")
        if not ai_bbox:
            continue
        abs_bbox = {
            "x": ai_bbox["x"] + ox,
            "y": ai_bbox["y"] + oy,
            "width":  ai_bbox["width"],
            "height": ai_bbox["height"],
        }
        norm_role = normalize_role(obj.get("role", ""), "")
        label = obj.get("label", "")
        for layer in candidate_layers:
            score = _compute_match_score(abs_bbox, norm_role, label, layer, canvas_w, canvas_h)
            scored_pairs.append((score, id(obj), id(layer), obj, layer, abs_bbox))

    # greedy 할당: 점수 내림차순, 중복 배정 방지
    scored_pairs.sort(key=lambda p: p[0], reverse=True)
    assigned_objs:   set[int] = set()
    assigned_layers: set[int] = set()
    matches: dict[int, dict] = {}  # id(obj) → match info

    for score, oid, lid, obj, layer, abs_bbox in scored_pairs:
        if oid in assigned_objs or lid in assigned_layers:
            continue
        if score >= 0.30:
            matches[oid] = {"layer": layer, "score": score, "abs_bbox": abs_bbox}
            assigned_objs.add(oid)
            assigned_layers.add(lid)

    # 결과 조합 (ai_objects 순서 유지)
    results: list[dict] = []
    for obj in ai_objects:
        m = matches.get(id(obj))
        ai_bbox = obj.get("bbox")
        fallback_abs = None
        if ai_bbox:
            fallback_abs = {
                "x": ai_bbox["x"] + ox,
                "y": ai_bbox["y"] + oy,
                "width":  ai_bbox["width"],
                "height": ai_bbox["height"],
            }

        if m:
            score = m["score"]
            layer = m["layer"]
            status = "ready" if score >= 0.75 else "matched_low_confidence"
            abs_bbox = m["abs_bbox"]
        else:
            score  = 0.0
            layer  = None
            status = "missing_layer"
            abs_bbox = fallback_abs

        results.append({
            "ai_obj":       obj,
            "layer":        layer,
            "score":        score,
            "match_status": status,
            "abs_bbox":     abs_bbox,
        })

    return results


# ─── asset 추출 ───────────────────────────────────────────────────────────────

def extract_object_assets(
    matched: list[dict],
    artboard_img,            # PIL Image (artboard-local 좌표) | None
    assets_dir: str,
    artboard_box: dict | None = None,
) -> dict[str, str | None]:
    """각 객체를 assets_dir에 PNG로 저장.

    우선순위:
      1. layer.previewPath 존재 → 복사
      2. layer._layer_obj composite → 렌더링
      3. artboard_img bbox crop (AI bbox)
      → 모두 실패 시 None

    반환: {obj_id: path | None}
    """
    os.makedirs(assets_dir, exist_ok=True)
    ax = artboard_box["x"] if artboard_box else 0
    ay = artboard_box["y"] if artboard_box else 0

    paths: dict[str, str | None] = {}

    for m in matched:
        obj_id   = m.get("obj_id", "unknown")
        layer    = m.get("layer")
        abs_bbox = m.get("abs_bbox")
        out_path = os.path.join(assets_dir, f"{obj_id}.png")

        # 1순위: layer previewPath
        if layer and layer.get("previewPath") and os.path.exists(layer["previewPath"]):
            try:
                shutil.copy2(layer["previewPath"], out_path)
                paths[obj_id] = out_path
                continue
            except Exception as e:
                print(f"[Extractor] previewPath copy failed {obj_id}: {e}")

        # 2순위: _layer_obj composite
        if layer and layer.get("_layer_obj"):
            try:
                limg = layer["_layer_obj"].composite()
                if limg and limg.width > 0:
                    limg.convert("RGBA").save(out_path)
                    paths[obj_id] = out_path
                    continue
            except Exception as e:
                print(f"[Extractor] layer composite failed {obj_id}: {e}")

        # 3순위: artboard_img bbox crop (abs_bbox → artboard-local 변환)
        if artboard_img and abs_bbox:
            try:
                x1 = max(0, int(abs_bbox["x"])     - ax)
                y1 = max(0, int(abs_bbox["y"])     - ay)
                x2 = min(artboard_img.width,  x1 + int(abs_bbox["width"]))
                y2 = min(artboard_img.height, y1 + int(abs_bbox["height"]))
                if x2 > x1 and y2 > y1:
                    artboard_img.crop((x1, y1, x2, y2)).convert("RGBA").save(out_path)
                    paths[obj_id] = out_path
                    continue
            except Exception as e:
                print(f"[Extractor] bbox crop failed {obj_id}: {e}")

        paths[obj_id] = None

    return paths


# ─── z-index 계산 ─────────────────────────────────────────────────────────────

def _compute_zindex(layer: dict | None, role: str, fallback: int = 50) -> int:
    """레이어 depth 기반 z-index 계산.

    depth=0(최상위) → background=0, depth 클수록(하위 그룹) → 전경.
    """
    if layer is None:
        return fallback
    depth = layer.get("depth", 0)
    if role == "background":
        return 0
    # depth 0은 최상위(배경 바로 위), depth 클수록 내부 그룹 → 더 전경
    return min(99, max(10, 10 + depth * 15))


# ─── 메인 orchestration ───────────────────────────────────────────────────────

def build_creative_object_set(
    psd_path: str,
    layers: list[dict],
    ai_analysis: dict | None,
    output_dir: str,
    artboard_img=None,
    artboard_box: dict | None = None,
    job_id: str | None = None,
) -> dict:
    """CreativeObjectSet dict 생성.

    psd_path     : PSD 파일 경로 (로깅·참조용)
    layers       : parse_psd_layers() 반환값 (_layer_obj 포함 가능)
    ai_analysis  : AI object analysis dict | None
                   keys: objects, canvasWidth, canvasHeight, artboardBox
    output_dir   : assets PNG를 저장할 base 디렉터리
    artboard_img : PIL Image (artboard-local 좌표, bbox_crop fallback용)
    artboard_box : {x, y, width, height} canvas-global 아트보드 영역
    job_id       : 로그 prefix용

    반환:
    {
      "canvas":   {"width": int, "height": int},
      "objects":  [CreativeObject dict, ...],
      "warnings": [str, ...]
    }
    """
    prefix = f"[{job_id or 'job'}][ObjectExtractor]"

    ai_objects: list[dict] = (ai_analysis or {}).get("objects", []) if ai_analysis else []

    # artboard_box 우선순위: 인수 > AI 분석 결과
    if artboard_box is None and ai_analysis:
        artboard_box = ai_analysis.get("artboardBox")

    # canvas 크기 결정
    canvas_w = int((ai_analysis or {}).get("canvasWidth") or 0) if ai_analysis else 0
    canvas_h = int((ai_analysis or {}).get("canvasHeight") or 0) if ai_analysis else 0
    if canvas_w <= 0 and layers:
        canvas_w = int(layers[0].get("canvasWidth", 0))
    if canvas_h <= 0 and layers:
        canvas_h = int(layers[0].get("canvasHeight", 0))
    if canvas_w <= 0:
        canvas_w = 1080
    if canvas_h <= 0:
        canvas_h = 1080

    print(f"{prefix} canvas={canvas_w}x{canvas_h} ai={len(ai_objects)} layers={len(layers)}")

    assets_dir = os.path.join(output_dir, "assets")
    os.makedirs(assets_dir, exist_ok=True)

    warnings: list[str] = []
    creative_objects: list[dict] = []
    obj_counter: dict[str, int] = {}

    # ── 케이스 A: AI + PSD 레이어 매칭 ─────────────────────────────────────
    if ai_objects and layers:
        matched = match_ai_objects_to_layers(
            ai_objects, layers, canvas_w, canvas_h, artboard_box
        )

        # obj_id 부여 (역할별 순번)
        for m in matched:
            role = normalize_role(
                m["ai_obj"].get("role", ""),
                m["layer"].get("name", "") if m["layer"] else "",
            )
            obj_counter[role] = obj_counter.get(role, 0) + 1
            m["obj_id"] = f"obj_{role}_{obj_counter[role]}"
            m["role"]   = role

        asset_paths = extract_object_assets(matched, artboard_img, assets_dir, artboard_box)

        for m in matched:
            ai_obj   = m["ai_obj"]
            layer    = m["layer"]
            role     = m["role"]
            obj_id   = m["obj_id"]
            score    = m["score"]
            status   = m["match_status"]
            abs_bbox = m.get("abs_bbox") or ai_obj.get("bbox", {})
            img_path = asset_paths.get(obj_id)

            if layer and img_path:
                source_type  = "psd_layer_group" if layer.get("type") == "group" else "psd_layer"
                quality_risk = None
            else:
                source_type  = "ai_bbox_crop"
                quality_risk = "high"

            if status == "missing_layer":
                warnings.append(
                    f"{obj_id}: PSD 레이어 매칭 실패 "
                    f"(ai_role={ai_obj.get('role')}, score={score:.2f})"
                )
            elif status == "matched_low_confidence":
                warnings.append(
                    f"{obj_id}: 저신뢰 매칭 (score={score:.2f}, "
                    f"layer={layer['name'] if layer else 'N/A'})"
                )
            if img_path is None:
                warnings.append(f"{obj_id}: asset 추출 실패")

            ai_confidence = float(ai_obj.get("confidence") or 0.5)
            combined_conf = round(score * 0.6 + ai_confidence * 0.4, 3) if score > 0 else round(ai_confidence * 0.5, 3)

            props = ROLE_PROPERTIES.get(role, ROLE_PROPERTIES["unknown"])
            creative_objects.append(_make_object(
                obj_id      = obj_id,
                role        = role,
                source_type = source_type,
                layer_ids   = [layer["id"]] if layer else [],
                img_path    = img_path,
                bbox        = abs_bbox,
                z_index     = _compute_zindex(layer, role),
                importance  = ai_obj.get("importance") or props["importance"],
                confidence  = combined_conf,
                props       = props,
                quality_risk= quality_risk,
                match_score = round(score, 3),
                match_status= status,
            ))

    # ── 케이스 B: PSD 레이어만 (AI 없음) ────────────────────────────────────
    elif layers and not ai_objects:
        print(f"{prefix} AI 분석 없음 - 레이어명 키워드로 역할 추론")
        for layer in layers:
            role, role_score = classify_layer_role(layer.get("name", ""))
            if role == "unknown" and role_score < 0.3:
                continue
            obj_counter[role] = obj_counter.get(role, 0) + 1
            obj_id = f"obj_{role}_{obj_counter[role]}"

            img_path = None
            if layer.get("previewPath") and os.path.exists(layer["previewPath"]):
                try:
                    dest = os.path.join(assets_dir, f"{obj_id}.png")
                    shutil.copy2(layer["previewPath"], dest)
                    img_path = dest
                except Exception:
                    pass

            props = ROLE_PROPERTIES.get(role, ROLE_PROPERTIES["unknown"])
            creative_objects.append(_make_object(
                obj_id       = obj_id,
                role         = role,
                source_type  = "psd_layer_group" if layer.get("type") == "group" else "psd_layer",
                layer_ids    = [layer["id"]],
                img_path     = img_path,
                bbox         = layer.get("bbox", {}),
                z_index      = _compute_zindex(layer, role),
                importance   = props["importance"],
                confidence   = round(role_score * 0.8, 3),
                props        = props,
                quality_risk = None,
                match_score  = role_score,
                match_status = "layer_name_only",
            ))

    # ── 케이스 C: AI만 있음 (PSD 레이어 없음) ────────────────────────────────
    elif ai_objects and not layers:
        print(f"{prefix} PSD 레이어 없음 - AI bbox crop only")
        warnings.append("PSD 레이어 없음: 모든 객체가 AI bbox crop (qualityRisk=high)으로 처리됩니다.")

        ab_ox = artboard_box["x"] if artboard_box else 0
        ab_oy = artboard_box["y"] if artboard_box else 0

        for ai_obj in ai_objects:
            role = normalize_role(ai_obj.get("role", ""), "")
            obj_counter[role] = obj_counter.get(role, 0) + 1
            obj_id = f"obj_{role}_{obj_counter[role]}"

            ai_bbox = ai_obj.get("bbox", {})
            abs_bbox = {
                "x":      ai_bbox.get("x", 0) + ab_ox,
                "y":      ai_bbox.get("y", 0) + ab_oy,
                "width":  ai_bbox.get("width", 0),
                "height": ai_bbox.get("height", 0),
            }

            img_path = None
            if artboard_img and abs_bbox.get("width") and abs_bbox.get("height"):
                try:
                    x1 = max(0, int(abs_bbox["x"]) - ab_ox)
                    y1 = max(0, int(abs_bbox["y"]) - ab_oy)
                    x2 = min(artboard_img.width,  x1 + int(abs_bbox["width"]))
                    y2 = min(artboard_img.height, y1 + int(abs_bbox["height"]))
                    if x2 > x1 and y2 > y1:
                        out_path = os.path.join(assets_dir, f"{obj_id}.png")
                        artboard_img.crop((x1, y1, x2, y2)).convert("RGBA").save(out_path)
                        img_path = out_path
                except Exception as e:
                    print(f"{prefix} bbox crop failed {obj_id}: {e}")

            props = ROLE_PROPERTIES.get(role, ROLE_PROPERTIES["unknown"])
            creative_objects.append(_make_object(
                obj_id       = obj_id,
                role         = role,
                source_type  = "ai_bbox_crop",
                layer_ids    = [],
                img_path     = img_path,
                bbox         = abs_bbox,
                z_index      = 50,
                importance   = ai_obj.get("importance") or props["importance"],
                confidence   = round(float(ai_obj.get("confidence") or 0.5), 3),
                props        = props,
                quality_risk = "high",
                match_score  = 0.0,
                match_status = "ai_only",
            ))

    else:
        warnings.append("AI 분석도 PSD 레이어도 없습니다. 빈 CreativeObjectSet을 반환합니다.")

    # ── 필수 역할 누락 경고 (실패하지 않음) ──────────────────────────────────
    found_roles = {obj["role"] for obj in creative_objects}
    for req in sorted(REQUIRED_ROLES):
        if req not in found_roles:
            warnings.append(f"필수 역할 '{req}'를 감지하지 못했습니다.")

    # z-index 오름차순 정렬 (배경 → 전경)
    creative_objects.sort(key=lambda o: (o["zIndex"], o["role"]))

    print(f"{prefix} 완료: objects={len(creative_objects)} warnings={len(warnings)}")
    for w in warnings:
        print(f"{prefix} WARN: {w}")

    return {
        "canvas":   {"width": canvas_w, "height": canvas_h},
        "objects":  creative_objects,
        "warnings": warnings,
    }


def _make_object(
    obj_id: str, role: str, source_type: str, layer_ids: list[str],
    img_path: str | None, bbox: dict, z_index: int, importance: str,
    confidence: float, props: dict, quality_risk: str | None,
    match_score: float, match_status: str,
) -> dict:
    """CreativeObject dict 생성 헬퍼."""
    return {
        "id":                  obj_id,
        "role":                role,
        "sourceType":          source_type,
        "layerIds":            layer_ids,
        "imagePath":           img_path,
        "bbox":                bbox,
        "zIndex":              z_index,
        "importance":          importance,
        "confidence":          confidence,
        "canCrop":             props["canCrop"],
        "canDrop":             props["canDrop"],
        "mustBeReadable":      props["mustBeReadable"],
        "mustBeInsideSafeZone":props["mustBeInsideSafeZone"],
        "minScale":            props["minScale"],
        "maxScale":            props["maxScale"],
        "keepAspectRatio":     props["keepAspectRatio"],
        "qualityRisk":         quality_risk,
        "matchScore":          match_score,
        "matchStatus":         match_status,
    }
