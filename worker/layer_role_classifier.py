"""4차-5: 레이어 역할 분류기.
이름 기반 룰 + 위치/크기 기반 보완 룰.
역할 enum: background / main_image / title / body_text / cta / logo / badge / decorative / unknown
"""

# 이름 기반 룰 (순서 중요: 먼저 매칭된 것 선택)
NAME_RULES = [
    ("background", ["배경",   "bg",        "background", "bkg",    "back",    "backdrop"]),
    ("title",      ["타이틀", "title",      "headline",   "제목",   "카피",    "copy",
                    "maintext", "main_text", "main_title"]),
    ("body_text",  ["텍스트", "text",       "설명",       "desc",   "description",
                    "subcopy",  "서브",      "body",       "subtext", "sub_text", "subhead"]),
    ("cta",        ["액션",   "cta",        "button",     "버튼",   "신청",    "구매",
                    "btn",     "apply",      "buy",        "order"]),
    ("main_image", ["제품",   "product",    "주요이미지", "모델",   "main",    "image",
                    "visual",  "person",     "model",      "item",   "goods",   "pack"]),
    ("logo",       ["로고",   "logo",       "brand",      "브랜드", "ci",      "bi",     "emblem"]),
    ("badge",      ["배지",   "badge",      "sticker",    "sale",   "discount","혜택",
                    "event",   "ribbon",     "seal",       "이벤트"]),
    ("decorative", ["장식",   "decoration", "deco",       "ornament","shape",  "pattern",
                    "effect",  "icon",       "star",       "heart"]),
]

PRIORITY_MAP = {
    "background": "required",
    "main_image": "required",
    "title":      "required",
    "cta":        "required",
    "logo":       "required",
    "body_text":  "important",
    "badge":      "important",
    "decorative": "optional",
    "unknown":    "optional",
}


def classify_role_by_name(name: str) -> str:
    n = (name or "").lower()
    for role, keywords in NAME_RULES:
        if any(k in n for k in keywords):
            return role
    return "unknown"


def classify_role_by_position(bbox: dict, canvas_w: int, canvas_h: int) -> str | None:
    """위치/크기 기반 보완 룰."""
    x = bbox.get("x", 0)
    y = bbox.get("y", 0)
    w = bbox.get("width", 0)
    h = bbox.get("height", 0)
    if w <= 0 or h <= 0:
        return None

    area_ratio = (w * h) / max(canvas_w * canvas_h, 1)
    cy = (y + h / 2) / max(canvas_h, 1)

    if area_ratio >= 0.80:
        return "background"
    if area_ratio >= 0.15 and w / max(h, 1) < 3.0:
        return "main_image"
    if cy < 0.25 and w >= canvas_w * 0.05 and h <= canvas_h * 0.15:
        return "logo"
    if cy >= 0.75 and area_ratio <= 0.15:
        return "cta"
    if area_ratio >= 0.40:
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
        unknown_text = [l for l in result if l["role"] == "unknown" and l["type"] == "text"]
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
