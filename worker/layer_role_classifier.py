"""4차-5: 레이어 역할 분류기.
이름 기반 룰 + 위치/크기 기반 보완 룰 + 타입 기반 모순 검증.
역할 enum: background / human_subject / product / main_image / title / body_text / cta / logo / badge / decorative / unknown

Stage 21: main_image를 human_subject(인물/모델)와 product(제품/상품)로 분리.
Stage 21.1: _validate_roles() — 타입 모순 검증 (텍스트 레이어 → human_subject 불가 등).
"""

# 이름 기반 룰 (순서 중요: 먼저 매칭된 것 선택)
NAME_RULES = [
    ("background",    ["배경",   "bg",        "background", "bkg",    "back",    "backdrop"]),
    ("title",         ["타이틀", "title",      "headline",   "제목",   "카피",    "copy",
                       "maintext", "main_text", "main_title"]),
    ("body_text",     ["텍스트", "text",       "설명",       "desc",   "description",
                       "subcopy",  "서브",      "body",       "subtext", "sub_text", "subhead"]),
    ("cta",           ["액션",   "cta",        "button",     "버튼",   "신청",    "구매",
                       "btn",     "apply",      "buy",        "order"]),
    # Stage 21: 인물/모델 → human_subject (SFR immutable 대상)
    # 주의: pixel/smartobject 레이어에만 유효. 텍스트 레이어는 _validate_roles()에서 재분류.
    ("human_subject", ["모델",   "person",     "model",      "인물",   "손",      "hand",
                       "피부",   "사람",       "human"]),
    # Stage 21: 제품/상품 → product (SFR removal 대상 → 배경 생성 후 재합성)
    ("product",       ["제품",   "product",    "패키지",     "pack",   "goods",   "item",
                       "상품"]),
    # main_image: visual / image / main 키워드 → 역할 불명 시각 요소
    ("main_image",    ["주요이미지", "main",   "image",      "visual"]),
    ("logo",          ["로고",   "logo",       "brand",      "브랜드", "ci",      "bi",     "emblem"]),
    ("badge",         ["배지",   "badge",      "sticker",    "sale",   "discount","혜택",
                       "event",   "ribbon",     "seal",       "이벤트"]),
    ("decorative",    ["장식",   "decoration", "deco",       "ornament","shape",  "pattern",
                       "effect",  "icon",       "star",       "heart"]),
]

PRIORITY_MAP = {
    "background":    "required",
    "human_subject": "required",
    "product":       "required",
    "main_image":    "required",
    "title":         "required",
    "cta":           "important",
    "logo":          "important",
    "body_text":     "important",
    "badge":         "important",
    "decorative":    "optional",
    "unknown":       "optional",
}

# 이름 기반 분류 시 시각 주체 역할을 텍스트 레이어에 절대 부여하지 않기 위한 집합
_VISUAL_SUBJECT_ROLES = frozenset({
    "human_subject", "product", "main_image", "logo", "decorative",
})

# 사각형/직사각형 계열 레이어명 키워드 → decorative
_RECTANGLE_NAME_KEYWORDS = ("사각형", "rectangle", "rect_", "_rect", "square_", "_square")

# shape 레이어에서 logo로 인정되려면 이름에 반드시 있어야 하는 키워드
_EXPLICIT_LOGO_KEYWORDS = ("로고", "logo", "brand", "브랜드", "ci", "bi", "emblem")


def classify_role_by_name(name: str) -> str:
    n = (name or "").lower()
    for role, keywords in NAME_RULES:
        if any(k in n for k in keywords):
            return role
    return "unknown"


def classify_role_by_position(bbox: dict, canvas_w: int, canvas_h: int) -> str | None:
    """위치/크기/비율 기반 보완 룰."""
    x = bbox.get("x", 0)
    y = bbox.get("y", 0)
    w = bbox.get("width", 0)
    h = bbox.get("height", 0)
    if w <= 0 or h <= 0:
        return None

    area_ratio = (w * h) / max(canvas_w * canvas_h, 1)
    cx = (x + w / 2) / max(canvas_w, 1)
    cy = (y + h / 2) / max(canvas_h, 1)
    aspect = w / max(h, 1)
    width_ratio  = w / max(canvas_w, 1)
    height_ratio = h / max(canvas_h, 1)

    # 전체 배경 (면적 70% 이상)
    if area_ratio >= 0.70:
        return "background"

    # 상단 15% 이내 소형 로고
    if cy < 0.15 and area_ratio < 0.10 and aspect >= 0.5:
        return "logo"

    # 상단 35% 이내 가로형 텍스트 → title
    if cy < 0.35 and aspect >= 1.5 and width_ratio >= 0.30 and height_ratio <= 0.25:
        return "title"

    # 하단 25% 이내 가로형 소형 → cta
    if cy >= 0.75 and aspect >= 1.2 and area_ratio <= 0.15:
        return "cta"

    # 하단 35% 이내 얇은 텍스트 → body_text
    if cy >= 0.65 and aspect >= 2.0 and height_ratio <= 0.15:
        return "body_text"

    # 중앙 영역 대형 이미지형
    if area_ratio >= 0.15 and aspect < 3.0 and 0.20 <= cy <= 0.80:
        return "main_image"

    if area_ratio >= 0.35:
        return "main_image"

    return None


def _text_layer_fallback_role(text: str, cy: float) -> str:
    """텍스트 레이어가 시각 주체 역할(human_subject 등)로 잘못 분류됐을 때의 대체 역할.

    짧고 행동 유발적인 텍스트가 하단에 있으면 cta.
    긴 설명 텍스트는 body_text.
    그 외 short headline은 title.
    """
    stripped = text.strip()
    length = len(stripped)

    # 매우 짧은 텍스트 + 하단 → cta 가능성
    if length <= 15 and cy >= 0.75:
        return "cta"

    # 긴 설명 → body_text
    if length > 30:
        return "body_text"

    # 짧고 중단 이상 → title
    if cy <= 0.60:
        return "title"

    # 짧고 하단 → body_text
    return "body_text"


def _validate_roles(result: list) -> list:
    """타입-역할 모순 검증 패스. classify_layers() 맨 마지막에 호출된다.

    V1  텍스트 레이어(type==type)는 시각 주체 역할 불가.
        (human_subject / product / main_image / logo / decorative → 재분류)
    V2  텍스트 레이어의 cta 역할은 짧은 행동 텍스트일 때만 허용.
        긴 텍스트(>30자)는 body_text로 재분류.
    V3  shape 레이어는 human_subject 불가 → decorative.
    V4  사각형/rectangle 계열 이름 → decorative (background/cta 제외).
    V5  shape 레이어 + logo 역할인데 이름에 명시적 로고 키워드 없음 → decorative.
    """
    for layer in result:
        layer_type = layer.get("type", "pixel")
        role = layer.get("role", "unknown")
        name_lc = (layer.get("name") or "").lower()
        # textContent 우선 (실제 텍스트 내용), 없으면 레이어명 사용
        text = layer.get("textContent") or layer.get("name") or ""
        canvas_h = layer.get("canvasHeight", 1) or 1
        bbox = layer.get("bbox", {})
        cy = (bbox.get("y", 0) + bbox.get("height", 0) / 2) / canvas_h

        # V1: 텍스트 레이어 + 시각 주체 역할 → 재분류
        if layer_type == "type" and role in _VISUAL_SUBJECT_ROLES:
            new_role = _text_layer_fallback_role(text, cy)
            layer["role"] = new_role
            layer["priority"] = PRIORITY_MAP.get(new_role, "optional")
            continue

        # V2: 텍스트 레이어 + cta + 긴 텍스트 → body_text
        # 한국어 CTA는 ≤15자("지금 신청하기" 등), 영어도 짧음 → 20자 초과면 설명 텍스트로 재분류
        if layer_type == "type" and role == "cta" and len(text.strip()) > 20:
            layer["role"] = "body_text"
            layer["priority"] = PRIORITY_MAP.get("body_text", "important")
            continue

        # V6: 텍스트 레이어 + badge + 긴 텍스트 → body_text
        # badge는 짧은 라벨(예: "SALE", "20%")용. 긴 문장은 body_text로 재분류.
        if layer_type == "type" and role == "badge" and len(text.strip()) > 20:
            layer["role"] = "body_text"
            layer["priority"] = PRIORITY_MAP.get("body_text", "important")
            continue

        # V3: shape 레이어 + human_subject → decorative
        if layer_type == "shape" and role == "human_subject":
            layer["role"] = "decorative"
            layer["priority"] = PRIORITY_MAP.get("decorative", "optional")
            continue

        # V4: 사각형/rectangle 이름 → decorative (background, cta 제외)
        if any(k in name_lc for k in _RECTANGLE_NAME_KEYWORDS):
            if role not in ("background", "cta"):
                layer["role"] = "decorative"
                layer["priority"] = PRIORITY_MAP.get("decorative", "optional")
            continue

        # V5: shape + logo + 명시적 로고 키워드 없음 → decorative
        if layer_type == "shape" and role == "logo":
            if not any(k in name_lc for k in _EXPLICIT_LOGO_KEYWORDS):
                layer["role"] = "decorative"
                layer["priority"] = PRIORITY_MAP.get("decorative", "optional")

    return result


def classify_layers(layers: list) -> list:
    """레이어 목록에 role / priority 추가한 새 목록 반환."""
    if not layers:
        return []

    canvas_w = layers[0].get("canvasWidth", 1) or 1
    canvas_h = layers[0].get("canvasHeight", 1) or 1

    result = []
    for layer in layers:
        role = classify_role_by_name(layer["name"])
        if role == "unknown":
            layer_type = layer.get("type", "pixel")
            pos_role = classify_role_by_position(layer["bbox"], canvas_w, canvas_h)
            # 텍스트 레이어에는 위치 기반 시각 주체 역할 부여 금지
            if pos_role and layer_type == "type" and pos_role in _VISUAL_SUBJECT_ROLES:
                pos_role = None
            if pos_role:
                role = pos_role
        priority = PRIORITY_MAP.get(role, "optional")
        result.append({**layer, "role": role, "priority": priority})

    # 휴리스틱 보완: unknown 중 가장 큰 픽셀/스마트오브젝트 → main_image
    unknown_image = [
        l for l in result
        if l["role"] == "unknown" and l["type"] in ("pixel", "smartobject")
    ]
    if unknown_image:
        biggest = max(unknown_image, key=lambda l: l["bbox"]["width"] * l["bbox"]["height"])
        biggest["role"] = "main_image"
        biggest["priority"] = "required"

    # 휴리스틱 보완: unknown 중 텍스트 레이어 → title (title이 없는 경우)
    has_title = any(l["role"] == "title" for l in result)
    if not has_title:
        unknown_text = [l for l in result if l["role"] == "unknown" and l["type"] in ("text", "type")]
        if unknown_text:
            biggest_text = max(unknown_text, key=lambda l: l["bbox"]["width"] * l["bbox"]["height"])
            biggest_text["role"] = "title"
            biggest_text["priority"] = "required"

    # 모순 검증 패스: 타입-역할 불일치 교정
    result = _validate_roles(result)

    return result


def get_role_stats(classified: list) -> dict:
    """분류 통계 반환 (성공률, 역할 목록)."""
    total = len(classified)
    known = sum(1 for l in classified if l.get("role") != "unknown")
    roles = list({l.get("role") for l in classified})
    return {
        "total":        total,
        "known":        known,
        "classifyRate": round(known / total, 3) if total > 0 else 0.0,
        "roles":        sorted(roles),
    }
