"""4차-9: 객체별 레이아웃 존 배치 엔진.
target 비율(가로형/정방형/세로형)에 따라 역할별 존을 계산한다.
가로형: main_image 원본 bbox 좌/우 위치에 따라 이미지-텍스트 배치 방향 결정.
"""

# 가로형 — image 좌측 / text 우측
_PRESETS_HORIZONTAL_IMG_LEFT = {
    "background":  {"layout": "fill"},
    "main_image":  {"layout": "zone", "zone": (0.02, 0.04, 0.50, 0.92)},
    "logo":        {"layout": "zone", "zone": (0.55, 0.04, 0.18, 0.12)},
    "title":       {"layout": "zone", "zone": (0.54, 0.19, 0.44, 0.25)},
    "body_text":   {"layout": "zone", "zone": (0.54, 0.47, 0.44, 0.18)},
    "cta":         {"layout": "zone", "zone": (0.57, 0.68, 0.32, 0.16)},
    "badge":       {"layout": "zone", "zone": (0.02, 0.02, 0.16, 0.14)},
    "decoration":  {"layout": "optional_drop"},
    "unknown":     {"layout": "optional_drop"},
}

# 가로형 — text 좌측 / image 우측
_PRESETS_HORIZONTAL_IMG_RIGHT = {
    "background":  {"layout": "fill"},
    "logo":        {"layout": "zone", "zone": (0.04, 0.04, 0.18, 0.12)},
    "title":       {"layout": "zone", "zone": (0.04, 0.19, 0.44, 0.25)},
    "body_text":   {"layout": "zone", "zone": (0.04, 0.47, 0.44, 0.18)},
    "cta":         {"layout": "zone", "zone": (0.07, 0.68, 0.32, 0.16)},
    "main_image":  {"layout": "zone", "zone": (0.50, 0.04, 0.48, 0.92)},
    "badge":       {"layout": "zone", "zone": (0.50, 0.02, 0.16, 0.14)},
    "decoration":  {"layout": "optional_drop"},
    "unknown":     {"layout": "optional_drop"},
}

_PRESETS_SQUARE = {
    "background":  {"layout": "fill"},
    "main_image":  {"layout": "zone", "zone": (0.05, 0.05, 0.90, 0.55)},
    "title":       {"layout": "zone", "zone": (0.05, 0.62, 0.90, 0.18)},
    "body_text":   {"layout": "zone", "zone": (0.05, 0.82, 0.90, 0.10)},
    "cta":         {"layout": "zone", "zone": (0.30, 0.88, 0.40, 0.10)},
    "logo":        {"layout": "zone", "zone": (0.70, 0.03, 0.22, 0.10)},
    "badge":       {"layout": "zone", "zone": (0.02, 0.02, 0.18, 0.12)},
    "decoration":  {"layout": "optional_drop"},
    "unknown":     {"layout": "optional_drop"},
}

_PRESETS_VERTICAL = {
    "background":  {"layout": "fill"},
    "logo":        {"layout": "zone", "zone": (0.05, 0.03, 0.20, 0.07)},
    "main_image":  {"layout": "zone", "zone": (0.00, 0.10, 1.00, 0.50)},
    "title":       {"layout": "zone", "zone": (0.05, 0.63, 0.90, 0.12)},
    "body_text":   {"layout": "zone", "zone": (0.05, 0.77, 0.90, 0.08)},
    "cta":         {"layout": "zone", "zone": (0.20, 0.87, 0.60, 0.08)},
    "badge":       {"layout": "zone", "zone": (0.02, 0.10, 0.18, 0.08)},
    "decoration":  {"layout": "optional_drop"},
    "unknown":     {"layout": "optional_drop"},
}


def _detect_main_image_side(objects: list) -> str:
    """main_image bbox 중심 x가 artboard 50% 기준 좌/우 반환."""
    for obj in objects:
        if obj.get("role") == "main_image":
            bbox = obj.get("bbox")
            if bbox:
                ab_w = None
                # artboard_width: bbox x + width 의 2배를 추정값으로 사용 (없으면 50% 가정)
                cx_ratio = (bbox["x"] + bbox["width"] / 2) / max(bbox["x"] + bbox["width"] + 1, 1)
                # bbox x가 절반 이하면 좌측
                cx = bbox["x"] + bbox["width"] / 2
                # artboard 너비를 모르므로 bbox 우끝 기준 rough estimate
                est_w = max(cx * 2.5, bbox["x"] + bbox["width"] + 10)
                return "left" if cx <= est_w / 2 else "right"
    return "left"


def _get_presets(dst_w: int, dst_h: int, objects: list) -> dict:
    ratio = dst_w / max(dst_h, 1)
    if ratio >= 1.3:
        side = _detect_main_image_side(objects)
        return _PRESETS_HORIZONTAL_IMG_LEFT if side == "left" else _PRESETS_HORIZONTAL_IMG_RIGHT
    elif ratio >= 0.8:
        return _PRESETS_SQUARE
    else:
        return _PRESETS_VERTICAL


def compute_layout(objects: list, dst_w: int, dst_h: int) -> list:
    """
    objects: PsdObjectAnalysis.objects (dict) 목록
    반환: 각 object에 layoutZone, layoutType 추가된 복사본 목록
    """
    presets = _get_presets(dst_w, dst_h, objects)
    result = []

    for obj in objects:
        role = obj.get("role", "unknown")
        preset = presets.get(role, {"layout": "optional_drop"})

        if preset["layout"] == "fill":
            item = {**obj, "layoutZone": {"x": 0, "y": 0, "w": dst_w, "h": dst_h}, "layoutType": "fill"}
        elif preset["layout"] == "zone":
            zn = preset["zone"]
            item = {**obj, "layoutZone": {
                "x": int(zn[0] * dst_w),
                "y": int(zn[1] * dst_h),
                "w": max(1, int(zn[2] * dst_w)),
                "h": max(1, int(zn[3] * dst_h)),
            }, "layoutType": "zone"}
        else:
            item = {**obj, "layoutZone": None, "layoutType": "dropped"}

        result.append(item)

    return result
