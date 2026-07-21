"""Stage 20A: Extended role resolver with 15 roles and full Korean/English aliases.

Priority order: user_override > layer_name > group_name > text_metadata > position > heuristic
"""
from __future__ import annotations
import unicodedata

# 15 role definitions with Korean and English aliases.
# ORDER MATTERS: more specific / compound roles must appear BEFORE general ones
# to prevent short keywords from matching sub-strings in unrelated names.
ROLE_ALIASES: list[tuple[str, list[str]]] = [
    ("background", [
        "배경", "bg", "background", "bkg", "backdrop",
        "베이스", "하단배경", "full_bg", "base_bg",
    ]),
    # Specific roles BEFORE the general roles they'd otherwise shadow
    ("product_detail", [
        "제품상세", "product_detail", "모델명", "model_name",
        "사양", "specification", "용량", "capacity",
        "productname", "상품명", "item_name",
    ]),
    ("brand_name", [
        "브랜드명", "brand_name", "상호명", "회사명",
        "brandname", "brand_text", "브랜드텍스트",
    ]),
    ("sub_logo", [
        "서브로고", "sub_logo", "파트너", "partner", "서브브랜드",
        "sub_brand", "co_brand", "협찬", "sponsor", "sublogo",
    ]),
    ("legal_text", [
        "법적", "legal", "disclaimer", "면책", "주의사항",
        "약관", "terms", "copyright", "저작권", "footnote",
        "각주", "smalltext", "small_text", "notice_text",
    ]),
    # General roles
    ("main_image", [
        "제품", "product", "주요이미지", "모델", "model", "main_img",
        "visual", "person", "item", "goods", "pack", "상품",
        "오브젝트", "object", "subject", "메인이미지", "mainimage",
        "main_visual", "핵심이미지", "hand", "main_image",
    ]),
    ("title", [
        "타이틀", "title", "headline", "제목", "주카피", "메인카피",
        "maintext", "main_text", "main_title", "헤드라인", "헤드",
        "head", "maincopy", "main_copy", "catchphrase",
        "슬로건", "slogan", "대제목",
    ]),
    ("body_text", [
        "설명", "desc", "description",
        "subcopy", "서브카피", "서브", "subtext", "sub_text", "subhead",
        "서브텍스트", "본문", "내용", "설명문구",
        "bodycopy", "body_copy", "body_text",
    ]),
    ("cta", [
        "액션", "cta", "button", "버튼", "신청", "구매",
        "btn", "apply", "buy", "order", "행동", "지금",
        "바로가기", "더보기", "자세히", "클릭", "click",
        "쇼핑", "purchase", "call_to_action",
    ]),
    ("logo", [
        "로고", "logo", "브랜드로고", "brand_logo", "ci", "bi", "emblem",
    ]),
    ("badge", [
        "배지", "badge", "sticker", "sale_tag", "discount_tag", "혜택",
        "ribbon", "seal", "이벤트태그", "할인", "쿠폰",
        "coupon", "라벨", "limited_tag", "한정",
    ]),
    ("scene", [
        "씬", "scene", "배경연출", "scenery", "소품", "props",
        "연출", "staging", "lifestyle",
    ]),
    ("decoration", [
        "장식", "decoration", "deco", "ornament", "꾸밈", "데코",
        "graphic_accent", "flourish",
    ]),
    ("pattern", [
        "패턴", "pattern", "texture", "텍스처", "반복패턴",
        "repeat_tile", "tile_bg",
    ]),
    ("overlay", [
        "오버레이", "overlay", "gradient", "그라데이션",
        "dim_layer", "vignette", "tint_layer", "반투명",
    ]),
]

PRIORITY_MAP = {
    "background":    "required",
    "main_image":    "required",
    "title":         "required",
    "cta":           "important",
    "logo":          "important",
    "body_text":     "important",
    "badge":         "important",
    "brand_name":    "important",
    "product_detail": "optional",
    "legal_text":    "optional",
    "sub_logo":      "optional",
    "scene":         "optional",
    "decoration":    "optional",
    "pattern":       "optional",
    "overlay":       "optional",
    "unknown":       "optional",
}

_KNOWN_ROLES = {r for r, _ in ROLE_ALIASES}


def _norm(s: str) -> str:
    """Lowercase + NFC normalize for consistent Korean matching."""
    return unicodedata.normalize("NFC", (s or "").lower()).strip()


def classify_role_by_name(name: str) -> str:
    n = _norm(name)
    for role, keywords in ROLE_ALIASES:
        if any(_norm(k) in n for k in keywords):
            return role
    return "unknown"


def classify_role_by_group(group_name: str) -> str:
    """Inherit role from parent group name."""
    if not group_name:
        return "unknown"
    return classify_role_by_name(group_name)


def classify_role_by_text_content(text: str) -> str:
    """Infer role from text content characteristics."""
    if not text:
        return "unknown"
    n = _norm(text)
    # CTA phrases
    cta_phrases = ["지금 바로", "신청하기", "구매하기", "더 알아보기", "자세히 보기",
                   "클릭", "shop now", "buy now", "learn more", "get started"]
    if any(p in n for p in cta_phrases):
        return "cta"
    # Legal text: long, small characters
    if len(text) > 80 and ("※" in text or "·" in text or text.startswith("*")):
        return "legal_text"
    return "unknown"


def classify_role_by_position(bbox: dict, canvas_w: int, canvas_h: int,
                               layer_type: str = "pixel") -> str | None:
    """Geometry-based role fallback."""
    x = bbox.get("x", 0)
    y = bbox.get("y", 0)
    w = bbox.get("width", 0)
    h = bbox.get("height", 0)
    if w <= 0 or h <= 0 or canvas_w <= 0 or canvas_h <= 0:
        return None

    area_ratio = (w * h) / (canvas_w * canvas_h)
    cx = (x + w / 2) / canvas_w
    cy = (y + h / 2) / canvas_h
    aspect = w / max(h, 1)
    width_ratio = w / canvas_w
    height_ratio = h / canvas_h

    if area_ratio >= 0.70:
        return "background"
    if cy < 0.15 and area_ratio < 0.10 and aspect >= 0.5:
        return "logo"
    if cy < 0.35 and aspect >= 1.5 and width_ratio >= 0.30 and height_ratio <= 0.25:
        return "title"
    if cy >= 0.75 and aspect >= 1.2 and area_ratio <= 0.15:
        return "cta"
    if cy >= 0.65 and aspect >= 2.0 and height_ratio <= 0.15:
        return "body_text"
    if area_ratio >= 0.15 and aspect < 3.0 and 0.20 <= cy <= 0.80:
        return "main_image"
    if area_ratio >= 0.35:
        return "main_image"
    return None


def resolve_layer_role(layer: dict, user_override: str = "") -> tuple[str, str]:
    """Resolve role for a single layer. Returns (role, role_source)."""
    if user_override and user_override in _KNOWN_ROLES:
        return user_override, "user_override"

    # Layer name
    role = classify_role_by_name(layer.get("name", ""))
    if role != "unknown":
        return role, "name"

    # Group name inheritance
    group = layer.get("groupName", "") or layer.get("group_name", "")
    role = classify_role_by_group(group)
    if role != "unknown":
        return role, "group"

    # Text content analysis
    text = layer.get("textContent", "") or ""
    role = classify_role_by_text_content(text)
    if role != "unknown":
        return role, "text_metadata"

    # Position-based
    pos_role = classify_role_by_position(
        layer.get("bbox", {}),
        layer.get("canvasWidth", 1) or 1,
        layer.get("canvasHeight", 1) or 1,
        layer.get("type", "pixel"),
    )
    if pos_role:
        return pos_role, "position"

    return "unknown", "heuristic"


def resolve_roles(layers: list[dict], user_overrides: dict | None = None) -> list[dict]:
    """Classify all layers with extended role resolution.

    Each layer gets: role, priority, roleSource added in-place.
    Also applies heuristics for large-image → main_image and text → title.
    """
    overrides = user_overrides or {}
    result = []
    for layer in layers:
        lid = layer.get("id", "")
        role, source = resolve_layer_role(layer, overrides.get(lid, ""))
        result.append({**layer, "role": role, "priority": PRIORITY_MAP.get(role, "optional"),
                       "roleSource": source})

    # Heuristic: largest pixel/smartobject with unknown → main_image
    unknown_img = [l for l in result if l["role"] == "unknown"
                   and l.get("type") in ("pixel", "smartobject")]
    if unknown_img:
        biggest = max(unknown_img, key=lambda l: l["bbox"]["width"] * l["bbox"]["height"])
        biggest["role"] = "main_image"
        biggest["priority"] = "required"
        biggest["roleSource"] = "heuristic"

    # Heuristic: largest text with unknown → title (if no title yet)
    if not any(l["role"] == "title" for l in result):
        unknown_text = [l for l in result if l["role"] == "unknown"
                        and l.get("type") in ("type", "text")]
        if unknown_text:
            biggest_text = max(unknown_text, key=lambda l: l["bbox"]["width"] * l["bbox"]["height"])
            biggest_text["role"] = "title"
            biggest_text["priority"] = "required"
            biggest_text["roleSource"] = "heuristic"

    return result


def get_role_stats(classified: list[dict]) -> dict:
    total = len(classified)
    known = sum(1 for l in classified if l.get("role") != "unknown")
    roles = sorted({l.get("role", "unknown") for l in classified})
    return {
        "total": total,
        "known": known,
        "classifyRate": round(known / total, 3) if total > 0 else 0.0,
        "roles": roles,
    }
