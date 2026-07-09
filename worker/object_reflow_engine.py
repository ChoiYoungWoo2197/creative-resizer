"""4차-9: 객체별 레이아웃 존 배치 엔진.
target 비율(가로형/정방형/세로형)에 따라 역할별 존을 계산한다.
"""

# 역할별 레이아웃 기본 존 (normalized 0.0~1.0: x, y, w, h)
# background는 항상 fill, decoration/unknown은 optional_drop 처리
_PRESETS_HORIZONTAL = {
    "background":  {"layout": "fill"},
    "main_image":  {"layout": "zone", "zone": (0.00, 0.00, 0.55, 1.00)},
    "title":       {"layout": "zone", "zone": (0.56, 0.05, 0.42, 0.28)},
    "body_text":   {"layout": "zone", "zone": (0.56, 0.36, 0.42, 0.20)},
    "cta":         {"layout": "zone", "zone": (0.60, 0.62, 0.30, 0.16)},
    "logo":        {"layout": "zone", "zone": (0.57, 0.83, 0.18, 0.12)},
    "badge":       {"layout": "zone", "zone": (0.00, 0.00, 0.18, 0.16)},
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


def _get_presets(dst_w: int, dst_h: int) -> dict:
    ratio = dst_w / max(dst_h, 1)
    if ratio >= 1.3:
        return _PRESETS_HORIZONTAL
    elif ratio >= 0.8:
        return _PRESETS_SQUARE
    else:
        return _PRESETS_VERTICAL


def compute_layout(objects: list, dst_w: int, dst_h: int) -> list:
    """
    objects: PsdObjectAnalysis.objects (dict) 목록
    반환: 각 object에 layoutZone, layoutType 추가된 복사본 목록
    """
    presets = _get_presets(dst_w, dst_h)
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
