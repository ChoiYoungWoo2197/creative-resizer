"""4단계: Safe zone 체크 pure functions.

safe zone = 광고 내 텍스트/CTA/로고가 반드시 위치해야 하는 안전 영역 (픽셀 inset).

hard-fail 역할: cta, headline, body_text, price, discount, logo
  → 이 역할의 layoutZone이 safe zone 밖이면 candidate 탈락 (passed=False)

main_image: safe zone 밖이어도 penalty만, hard fail 아님.
"""

# hard fail을 유발하는 역할 집합
HARD_FAIL_ROLES: frozenset[str] = frozenset({
    "cta", "headline", "body_text", "price", "discount", "logo",
})


# ─── pure functions (unit-testable) ──────────────────────────────────────────

def normalize_safe_zone(spec: dict, target_w: int, target_h: int) -> dict:
    """spec dict에서 safe zone을 읽거나 비율 기반 기본값을 계산한다.

    spec 키:
      safeZone     → {top, right, bottom, left} (픽셀)  — 일반 safe zone
      textSafeZone → {top, right, bottom, left}          — 텍스트 계열
      ctaSafeZone  → {top, right, bottom, left}          — CTA 전용

    값이 없거나 불완전하면 aspect ratio 기반 기본값 사용:
      가로형(ratio≥1.7):  general=top/bot 8% lr 6%,  text=all 8%,  cta=top/lr 8% bot 10%
      세로형(ratio≤0.6):  general=top 12% bot 18% lr 7%,  text=top 15% bot 20% lr 8%
      정방형(기타):       general=all 8%,  text=all 9%,  cta=top/lr 9% bot 11%

    반환: {"general": sz, "text": sz, "cta": sz}
    모든 값은 픽셀 정수.

    >>> sz = normalize_safe_zone({}, 1200, 628)
    >>> sz["general"]["top"] == int(628 * 8 / 100)
    True
    >>> sz_custom = normalize_safe_zone(
    ...     {"safeZone": {"top": 50, "right": 50, "bottom": 50, "left": 50}}, 1200, 628)
    >>> sz_custom["general"]["top"]
    50
    """
    ratio = target_w / max(target_h, 1)

    # 비율 기반 기본값
    if ratio >= 1.7:         # 가로형 wide
        default_gen = _pct(target_w, target_h, top=8,  right=6,  bottom=8,  left=6)
        default_txt = _pct(target_w, target_h, top=8,  right=8,  bottom=8,  left=8)
        default_cta = _pct(target_w, target_h, top=8,  right=8,  bottom=10, left=8)
    elif ratio <= 0.6:       # 세로형 9:16
        default_gen = _pct(target_w, target_h, top=12, right=7,  bottom=18, left=7)
        default_txt = _pct(target_w, target_h, top=15, right=8,  bottom=20, left=8)
        default_cta = _pct(target_w, target_h, top=15, right=10, bottom=22, left=10)
    else:                    # 정방형 근사
        default_gen = _pct(target_w, target_h, top=8,  right=8,  bottom=8,  left=8)
        default_txt = _pct(target_w, target_h, top=9,  right=9,  bottom=9,  left=9)
        default_cta = _pct(target_w, target_h, top=9,  right=9,  bottom=11, left=9)

    return {
        "general": _read_safe_zone(spec, "safeZone",     default_gen),
        "text":    _read_safe_zone(spec, "textSafeZone", default_txt),
        "cta":     _read_safe_zone(spec, "ctaSafeZone",  default_cta),
    }


def get_object_safe_zone(role: str, safe_zones: dict) -> dict:
    """역할에 맞는 safe zone dict 반환.

    cta → "cta" safe zone
    headline / body_text / price / discount / logo → "text" safe zone
    그 외 → "general" safe zone

    >>> sz = {"general": {"top":40,"right":40,"bottom":40,"left":40},
    ...       "text":    {"top":60,"right":60,"bottom":60,"left":60},
    ...       "cta":     {"top":60,"right":60,"bottom":80,"left":60}}
    >>> get_object_safe_zone("cta", sz)["bottom"]
    80
    >>> get_object_safe_zone("headline", sz)["top"]
    60
    >>> get_object_safe_zone("main_image", sz)["top"]
    40
    >>> get_object_safe_zone("unknown", sz)["top"]
    40
    """
    if role == "cta":
        return safe_zones.get("cta") or safe_zones.get("general") or {}
    if role in ("headline", "body_text", "price", "discount", "logo"):
        return safe_zones.get("text") or safe_zones.get("general") or {}
    return safe_zones.get("general") or {}


def rect_inside_safe_zone(
    rect: dict,
    sz: dict,
    canvas_w: int,
    canvas_h: int,
) -> bool:
    """rect 전체가 safe zone 경계 안에 있는지 확인.

    rect: {x, y, width, height}  — canvas-absolute 픽셀
    sz:   {top, right, bottom, left}  — canvas 가장자리 inset 픽셀

    >>> sz = {"top": 80, "right": 80, "bottom": 80, "left": 80}
    >>> rect_inside_safe_zone({"x": 100, "y": 80, "width": 300, "height": 100}, sz, 1000, 600)
    True
    >>> rect_inside_safe_zone({"x": 10, "y": 80, "width": 300, "height": 100}, sz, 1000, 600)
    False
    >>> rect_inside_safe_zone({"x": 80, "y": 80, "width": 900, "height": 100}, sz, 1000, 600)
    False
    """
    safe_x1 = sz.get("left",   0)
    safe_y1 = sz.get("top",    0)
    safe_x2 = canvas_w - sz.get("right",  0)
    safe_y2 = canvas_h - sz.get("bottom", 0)

    rx1 = rect.get("x", 0)
    ry1 = rect.get("y", 0)
    rx2 = rx1 + rect.get("width",  0)
    ry2 = ry1 + rect.get("height", 0)

    return rx1 >= safe_x1 and ry1 >= safe_y1 and rx2 <= safe_x2 and ry2 <= safe_y2


def check_safe_zone_violations(
    laid_out_objects: list,
    canvas_w: int,
    canvas_h: int,
    safe_zones: dict,
) -> dict:
    """각 객체의 layoutZone이 safe zone 안에 있는지 체크.

    hard-fail 역할이 하나라도 밖이면 passed=False → object-reflow candidate 탈락.
    main_image는 밖이어도 violations에 기록만 (hard fail 아님).

    반환:
    {
      "passed": bool,
      "violations": list[str],                           — 간략 메시지 목록
      "safeZoneViolations": list[{role, objId, message, hardFail}]
    }

    >>> objects = [
    ...     {"id": "obj_cta_1", "role": "cta",
    ...      "layoutType": "zone",
    ...      "layoutZone": {"x": 5, "y": 400, "w": 200, "h": 60}},
    ... ]
    >>> sz = normalize_safe_zone({}, 800, 500)
    >>> result = check_safe_zone_violations(objects, 800, 500, sz)
    >>> result["passed"]
    False
    >>> result["violations"][0].startswith("cta")
    True
    """
    violations: list[str] = []
    sz_violations: list[dict] = []
    hard_fail = False

    for obj in laid_out_objects:
        role = obj.get("role", "unknown")
        layout_type = obj.get("layoutType", "dropped")
        layout_zone = obj.get("layoutZone")

        # fill(배경) / dropped 는 safe zone 체크 제외
        if layout_type in ("dropped", "fill") or layout_zone is None:
            continue

        sz = get_object_safe_zone(role, safe_zones)
        if not sz:
            continue

        rect = {
            "x":      layout_zone["x"],
            "y":      layout_zone["y"],
            "width":  layout_zone["w"],
            "height": layout_zone["h"],
        }

        if not rect_inside_safe_zone(rect, sz, canvas_w, canvas_h):
            is_hard = role in HARD_FAIL_ROLES
            short_msg = f"{role} outside safe zone"
            detail_msg = (
                f"{role}(id={obj.get('id', '?')}) outside safe zone: "
                f"zone=[x:{rect['x']},y:{rect['y']},w:{rect['width']},h:{rect['height']}] "
                f"safe=[t:{sz.get('top')},r:{sz.get('right')},b:{sz.get('bottom')},l:{sz.get('left')}]"
            )
            violations.append(short_msg)
            sz_violations.append({
                "role":     role,
                "objId":    obj.get("id", ""),
                "message":  detail_msg,
                "hardFail": is_hard,
            })
            if is_hard:
                hard_fail = True

    return {
        "passed":             not hard_fail,
        "violations":         violations,
        "safeZoneViolations": sz_violations,
    }


# ─── private helpers ──────────────────────────────────────────────────────────

def _pct(w: int, h: int, top: int, right: int, bottom: int, left: int) -> dict:
    """퍼센트(정수)를 픽셀 inset dict로 변환."""
    return {
        "top":    max(0, int(h * top    / 100)),
        "right":  max(0, int(w * right  / 100)),
        "bottom": max(0, int(h * bottom / 100)),
        "left":   max(0, int(w * left   / 100)),
    }


def _read_safe_zone(spec: dict, key: str, default: dict) -> dict:
    """spec dict에서 safe zone 값을 읽어 유효하면 반환, 아니면 default."""
    val = (spec or {}).get(key)
    if isinstance(val, dict) and all(k in val for k in ("top", "right", "bottom", "left")):
        return {k: max(0, int(val[k])) for k in ("top", "right", "bottom", "left")}
    return default
