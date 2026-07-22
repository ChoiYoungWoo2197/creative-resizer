"""4차-5: 레이어 역할 분류기.
이름 기반 룰 + 위치/크기 기반 보완 룰.
역할 enum: background / human_subject / product / main_image / title / body_text / cta / logo / badge / decorative / unknown

Stage 21: main_image를 human_subject(인물/모델)와 product(제품/상품)로 분리.
human_subject는 SFR _IMMUTABLE_ROLES에 포함되어 AI가 덮어쓰지 않는다.
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
    cx = (x + w / 2) / max(canvas_w, 1)  # 수평 중심 비율
    cy = (y + h / 2) / max(canvas_h, 1)  # 수직 중심 비율
    aspect = w / max(h, 1)               # 가로/세로 비율
    width_ratio  = w / max(canvas_w, 1)  # 캔버스 대비 너비
    height_ratio = h / max(canvas_h, 1)  # 캔버스 대비 높이

    # 전체 배경 (면적 70% 이상 or 가로세로 거의 캔버스 전체)
    if area_ratio >= 0.70:
        return "background"

    # 상단 15% 이내 소형 로고
    if cy < 0.15 and area_ratio < 0.10 and aspect >= 0.5:
        return "logo"

    # 상단 35% 이내 가로형 텍스트 → title
    if cy < 0.35 and aspect >= 1.5 and width_ratio >= 0.30 and height_ratio <= 0.25:
        return "title"

    # 하단 25% 이내 가로형 소형 → cta (버튼/행동 유도)
    if cy >= 0.75 and aspect >= 1.2 and area_ratio <= 0.15:
        return "cta"

    # 하단 35% 이내 얇은 텍스트 → body_text
    if cy >= 0.65 and aspect >= 2.0 and height_ratio <= 0.15:
        return "body_text"

    # 중앙 영역 대형 이미지형 (비율 1:3 미만, 면적 15% 이상)
    if area_ratio >= 0.15 and aspect < 3.0 and 0.20 <= cy <= 0.80:
        return "main_image"

    # 면적 35% 이상인 경우 main_image 추정
    if area_ratio >= 0.35:
        return "main_image"

    return None


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
            pos_role = classify_role_by_position(layer["bbox"], canvas_w, canvas_h)
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

    # 휴리스틱 보완: unknown 중 텍스트 레이어 → title (없는 경우)
    has_title = any(l["role"] == "title" for l in result)
    if not has_title:
        unknown_text = [l for l in result if l["role"] == "unknown" and l["type"] in ("text", "type")]
        if unknown_text:
            biggest_text = max(unknown_text, key=lambda l: l["bbox"]["width"] * l["bbox"]["height"])
            biggest_text["role"] = "title"
            biggest_text["priority"] = "required"

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
