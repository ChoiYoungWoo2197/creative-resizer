"""4차-5: Layer Reflow Engine.
분류된 레이어 → 타겟 캔버스 배치 좌표 계산.
현재 MVP 대상: Naver GFA 모바일DA 1250×560.
"""

# Naver GFA 1250×560 safe zone (top/right/bottom/left)
SAFE_ZONES = {
    (1250, 560): {"top": 50, "right": 50, "bottom": 35, "left": 240},
}

REQUIRED_ROLES = {"title", "main_image", "cta", "logo"}
IMPORTANT_ROLES = {"body_text", "badge"}

# 1250×560 레이아웃 후보 (§8 GPT 문서)
_LAYOUTS_1250x560 = {
    "layout_candidate_1": {   # main_image 좌측 / text 우측
        "background": {"x": 0,   "y": 0,   "w": 1250, "h": 560, "mode": "cover"},
        "main_image": {"x": 260, "y": 80,  "w": 360,  "h": 420, "mode": "contain"},
        "title":      {"x": 650, "y": 120, "w": 480,  "h": 90,  "mode": "contain"},
        "body_text":  {"x": 650, "y": 220, "w": 480,  "h": 80,  "mode": "contain"},
        "cta":        {"x": 650, "y": 340, "w": 480,  "h": 70,  "mode": "contain"},
        "logo":       {"x": 260, "y": 55,  "w": 160,  "h": 60,  "mode": "contain"},
        "badge":      {"x": 650, "y": 430, "w": 200,  "h": 70,  "mode": "contain"},
        "decorative": {"x": 950, "y": 430, "w": 200,  "h": 100, "mode": "contain"},
    },
    "layout_candidate_2": {   # text 좌측 / main_image 우측
        "background": {"x": 0,   "y": 0,   "w": 1250, "h": 560, "mode": "cover"},
        "title":      {"x": 260, "y": 120, "w": 480,  "h": 90,  "mode": "contain"},
        "body_text":  {"x": 260, "y": 220, "w": 480,  "h": 80,  "mode": "contain"},
        "cta":        {"x": 260, "y": 340, "w": 480,  "h": 70,  "mode": "contain"},
        "main_image": {"x": 780, "y": 80,  "w": 360,  "h": 420, "mode": "contain"},
        "logo":       {"x": 260, "y": 55,  "w": 160,  "h": 60,  "mode": "contain"},
        "badge":      {"x": 260, "y": 430, "w": 200,  "h": 70,  "mode": "contain"},
        "decorative": {"x": 780, "y": 430, "w": 200,  "h": 100, "mode": "contain"},
    },
}


def get_safe_zone(target_w: int, target_h: int) -> dict:
    return SAFE_ZONES.get((target_w, target_h), {"top": 40, "right": 40, "bottom": 40, "left": 40})


def check_required_layers(classified: list) -> tuple:
    """필수 레이어 존재 여부. (ok: bool, reason: str|None)"""
    roles = {l["role"] for l in classified}
    missing = REQUIRED_ROLES - roles
    if missing:
        return False, f"required roles missing: {sorted(missing)}"
    return True, None


def _select_best_layout(classified: list) -> str:
    """main_image 위치 기준으로 레이아웃 후보 선택."""
    # main_image 레이어의 x 중심이 화면 좌측이면 candidate_1, 우측이면 candidate_2
    main_layers = [l for l in classified if l["role"] == "main_image"]
    if not main_layers:
        return "layout_candidate_1"
    canvas_w = main_layers[0].get("canvasWidth", 1200) or 1200
    cx = main_layers[0]["bbox"]["x"] + main_layers[0]["bbox"]["width"] / 2
    return "layout_candidate_1" if cx <= canvas_w / 2 else "layout_candidate_2"


def _safe_zone_pass(placements: list, safe_zone: dict, canvas_w: int, canvas_h: int) -> bool:
    """필수 레이어가 safe zone 내 50% 이상 포함되는지 검사."""
    sx = safe_zone["left"]
    sy = safe_zone["top"]
    sw = canvas_w - safe_zone["left"] - safe_zone["right"]
    sh = canvas_h - safe_zone["top"] - safe_zone["bottom"]
    for p in placements:
        if p["role"] not in REQUIRED_ROLES or p["role"] == "background":
            continue
        x, y, w, h = p["x"], p["y"], p["w"], p["h"]
        ox = max(0, min(x + w, sx + sw) - max(x, sx))
        oy = max(0, min(y + h, sy + sh) - max(y, sy))
        if (ox * oy) / max(w * h, 1) < 0.5:
            return False
    return True


def _overlap_risk(placements: list) -> bool:
    """필수 레이어 간 겹침 여부."""
    non_bg = [p for p in placements if p["role"] != "background"]
    for i, p1 in enumerate(non_bg):
        for p2 in non_bg[i + 1:]:
            ox = max(0, min(p1["x"] + p1["w"], p2["x"] + p2["w"]) - max(p1["x"], p2["x"]))
            oy = max(0, min(p1["y"] + p1["h"], p2["y"] + p2["h"]) - max(p1["y"], p2["y"]))
            if ox * oy > 0:
                return True
    return False


def compute_layout(classified: list, target_w: int, target_h: int) -> dict:
    """분류된 레이어 목록으로 최적 레이아웃 배치 계산.

    반환: {
        success: bool,
        layoutType: str,
        placements: list[{layerId, role, x, y, w, h, scale, mode, previewPath}],
        quality: {safeZonePass, requiredLayerMissing, overlapRisk},
        error: str | None,
    }
    """
    if not (target_w == 1250 and target_h == 560):
        return {
            "success": False,
            "error": f"unsupported target: {target_w}x{target_h}",
            "requiredLayerMissing": False,
        }

    ok, reason = check_required_layers(classified)
    if not ok:
        return {
            "success": False,
            "error": reason,
            "requiredLayerMissing": True,
            "placements": [],
            "quality": {"safeZonePass": False, "requiredLayerMissing": True, "overlapRisk": False},
        }

    layout_name = _select_best_layout(classified)
    slots = _LAYOUTS_1250x560[layout_name]
    safe_zone = get_safe_zone(target_w, target_h)

    placements = []
    roles_placed: set = set()

    for layer in classified:
        role = layer["role"]
        if role in roles_placed or role not in slots:
            continue
        slot = slots[role]
        src_w = layer["bbox"]["width"]
        src_h = layer["bbox"]["height"]
        if src_w > 0 and src_h > 0:
            if slot.get("mode") == "cover":
                scale = max(slot["w"] / src_w, slot["h"] / src_h)
            else:
                scale = min(slot["w"] / src_w, slot["h"] / src_h)
        else:
            scale = 1.0

        placements.append({
            "layerId":     layer["id"],
            "role":        role,
            "x":           slot["x"],
            "y":           slot["y"],
            "w":           slot["w"],
            "h":           slot["h"],
            "scale":       round(scale, 4),
            "mode":        slot.get("mode", "contain"),
            "previewPath": layer.get("previewPath"),
        })
        roles_placed.add(role)

    szp = _safe_zone_pass(placements, safe_zone, target_w, target_h)
    req_missing = bool(REQUIRED_ROLES - roles_placed - {"background"})
    ovr = _overlap_risk(placements)

    return {
        "success":     True,
        "layoutType":  layout_name,
        "placements":  placements,
        "quality": {
            "safeZonePass":        szp,
            "requiredLayerMissing": req_missing,
            "overlapRisk":         ovr,
        },
        "error": None,
    }


def calc_reflow_score(placements: list, quality: dict) -> float:
    """layer-reflow 품질 점수 (0~100). 65 미만 → smart-fit-enhanced fallback."""
    score = 55.0
    if quality.get("safeZonePass"):
        score += 20
    if not quality.get("requiredLayerMissing"):
        score += 15
    if not quality.get("overlapRisk"):
        score += 5
    bg = any(p["role"] == "background" for p in placements)
    if bg:
        score += 5
    return round(min(100.0, max(0.0, score)), 2)
