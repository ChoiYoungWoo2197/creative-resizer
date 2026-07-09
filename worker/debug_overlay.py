"""7단계: Debug overlay 생성기.

CREATIVE_DEBUG_OVERLAY=true 환경변수가 설정된 경우에만 실행.
기본 ZIP/다운로드 결과에는 포함하지 않음.

생성 파일 (debug flag true인 경우에만):
  {base}.debug.png   — 결과 이미지 위에 safe zone + object bbox 오버레이
  {base}.layout.json — layout 품질 메타 JSON

overlay 생성 실패가 원본 이미지 생성 실패로 이어지지 않도록
generate_debug_files() 전체를 try/except로 감쌌다.
"""

import json
import os
import traceback
from PIL import Image, ImageDraw, ImageFont

from safe_zone import get_object_safe_zone, rect_inside_safe_zone

# ─── 색상 팔레트 (RGB) ───────────────────────────────────────────────────────

_ROLE_COLOR: dict[str, tuple] = {
    "background":  (150, 150, 150),
    "main_image":  (70,  130, 180),
    "person":      (60,  120, 170),
    "headline":    (220, 50,  50 ),
    "body_text":   (180, 70,  70 ),
    "cta":         (255, 140, 0  ),
    "logo":        (50,  100, 220),
    "badge":       (100, 180, 100),
    "price":       (180, 100, 20 ),
    "discount":    (200, 50,  50 ),
    "decoration":  (140, 140, 140),
}
_DEFAULT_COLOR    = (180, 100, 180)  # unknown role
_COLOR_GEN_SZ     = (0,   200, 80 )  # general safe zone: green
_COLOR_TEXT_SZ    = (240, 200, 0  )  # text safe zone: yellow
_COLOR_CTA_SZ     = (255, 140, 0  )  # cta safe zone: orange
_VIOLATION_COLOR  = (220, 40,  40 )  # safe zone violation: red  ← canonical name
_BORDER_W         = 2


# ─── 내부 유틸 ────────────────────────────────────────────────────────────────

def _font() -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=14)
    except TypeError:
        return ImageFont.load_default()


def _dashed_rect(draw: ImageDraw.ImageDraw,
                 x0: int, y0: int, x1: int, y1: int,
                 color: tuple, width: int = 2, dash: int = 10) -> None:
    """PIL 기본 API만 사용한 점선 사각형."""
    x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
    for x in range(x0, x1, dash * 2):
        draw.line([(x, y0), (min(x + dash, x1), y0)], fill=color, width=width)
        draw.line([(x, y1), (min(x + dash, x1), y1)], fill=color, width=width)
    for y in range(y0, y1, dash * 2):
        draw.line([(x0, y), (x0, min(y + dash, y1))], fill=color, width=width)
        draw.line([(x1, y), (x1, min(y + dash, y1))], fill=color, width=width)


def _alpha_rect(base: Image.Image,
                x0: int, y0: int, x1: int, y1: int,
                color_rgb: tuple, alpha: int = 30) -> Image.Image:
    """semi-transparent 색면을 base에 alpha_composite."""
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    d.rectangle([int(x0), int(y0), int(x1), int(y1)],
                fill=(*color_rgb, alpha))
    return Image.alpha_composite(base, overlay)


def _draw_label(draw: ImageDraw.ImageDraw,
                x: int, y: int, text: str,
                color: tuple, font: ImageFont.ImageFont) -> None:
    """텍스트 + 반투명 검정 배경 박스."""
    try:
        bbox = draw.textbbox((int(x), int(y)), text, font=font)
        bx0, by0, bx1, by1 = bbox
        draw.rectangle([bx0 - 2, by0 - 1, bx1 + 2, by1 + 1],
                       fill=(0, 0, 0, 160))
        draw.text((int(x), int(y)), text, fill=(*color, 255), font=font)
    except Exception:
        draw.text((int(x), int(y)), text, fill=(*color, 255))


def _object_sz_passed(p: dict, safe_zones: dict,
                      canvas_w: int, canvas_h: int) -> bool:
    """개별 placement의 safe zone 통과 여부."""
    role = p.get("role", "")
    sz   = get_object_safe_zone(role, safe_zones)
    if not sz:
        return True
    rect = {"x": p["x"], "y": p["y"], "width": p["width"], "height": p["height"]}
    return rect_inside_safe_zone(rect, sz, canvas_w, canvas_h)


# ─── overlay 이미지 생성 ───────────────────────────────────────────────────────

def _build_overlay(result_img: Image.Image,
                   best: dict,
                   safe_zones: dict,
                   canvas_w: int, canvas_h: int) -> Image.Image:
    """결과 이미지 위에 safe zone 박스 + object placement bbox 그리기."""
    img  = result_img.copy().convert("RGBA")
    font = _font()

    def _inset(sz: dict) -> tuple:
        return (
            sz.get("left",   0),
            sz.get("top",    0),
            canvas_w - sz.get("right",  0),
            canvas_h - sz.get("bottom", 0),
        )

    gen_sz = safe_zones.get("general", {})
    txt_sz = safe_zones.get("text",    {})
    cta_sz = safe_zones.get("cta",     {})

    gx0, gy0, gx1, gy1 = _inset(gen_sz)
    tx0, ty0, tx1, ty1 = _inset(txt_sz)
    cx0, cy0, cx1, cy1 = _inset(cta_sz)

    # 1. safe zone 반투명 면
    img = _alpha_rect(img, gx0, gy0, gx1, gy1, _COLOR_GEN_SZ,  alpha=18)
    img = _alpha_rect(img, tx0, ty0, tx1, ty1, _COLOR_TEXT_SZ, alpha=14)
    img = _alpha_rect(img, cx0, cy0, cx1, cy1, _COLOR_CTA_SZ,  alpha=14)

    draw = ImageDraw.Draw(img, "RGBA")

    # 2. safe zone 점선 테두리
    _dashed_rect(draw, gx0, gy0, gx1, gy1, (*_COLOR_GEN_SZ,  210), width=2, dash=10)
    _dashed_rect(draw, tx0, ty0, tx1, ty1, (*_COLOR_TEXT_SZ, 180), width=1, dash=8)
    _dashed_rect(draw, cx0, cy0, cx1, cy1, (*_COLOR_CTA_SZ,  180), width=1, dash=8)

    # 3. 범례 (좌상단)
    _draw_label(draw,  6,  6, "GEN SZ",  _COLOR_GEN_SZ,  font)
    _draw_label(draw,  6, 24, "TEXT SZ", _COLOR_TEXT_SZ, font)
    _draw_label(draw,  6, 42, "CTA SZ",  _COLOR_CTA_SZ,  font)

    # 4. 각 object placement bbox
    placements = best.get("placements", [])
    for p in placements:
        if p.get("dropped"):
            continue
        role   = p.get("role", "unknown")
        px, py = int(p.get("x", 0)), int(p.get("y", 0))
        pw, ph = int(p.get("width", 0)), int(p.get("height", 0))
        color  = _ROLE_COLOR.get(role, _DEFAULT_COLOR)

        sz_ok  = _object_sz_passed(p, safe_zones, canvas_w, canvas_h)
        border = _VIOLATION_COLOR if not sz_ok else color

        # 반투명 fill
        img = _alpha_rect(img, px, py, px + pw, py + ph, border, alpha=28)
        draw = ImageDraw.Draw(img, "RGBA")

        # 실선 테두리
        draw.rectangle([px, py, px + pw, py + ph],
                       outline=(*border, 230), width=_BORDER_W)

        # 역할 라벨
        label_text = role if sz_ok else f"{role} !"
        _draw_label(draw, max(0, px + 3), max(0, py + 3), label_text, border, font)

    return img


# ─── layout JSON 생성 ────────────────────────────────────────────────────────

def _build_layout_json(comp_meta: dict,
                       layout_result: dict,
                       creative_object_set: dict,
                       safe_zones: dict,
                       target_w: int, target_h: int,
                       render_source: str,
                       actual_render_mode: str,
                       layout_score_status: str) -> dict:
    """layout JSON dict 조립."""
    best           = layout_result.get("best") or {}
    top_candidates = layout_result.get("topCandidates", [])
    all_candidates = layout_result.get("allCandidates", [])
    placements     = best.get("placements", [])

    objs_by_id: dict = {
        obj["id"]: obj
        for obj in (creative_object_set or {}).get("objects", [])
        if obj.get("id")
    }

    # general safe zone → 박스 표현
    gen_sz = safe_zones.get("general", {})
    sz_box = {
        "x":      gen_sz.get("left",   0),
        "y":      gen_sz.get("top",    0),
        "width":  target_w - gen_sz.get("left",  0) - gen_sz.get("right",  0),
        "height": target_h - gen_sz.get("top",   0) - gen_sz.get("bottom", 0),
    }

    # objects: 배치된 객체 + dropped 객체
    objects_info = []
    for p in placements:
        obj_id = p.get("objectId", "")
        dropped = p.get("dropped", False)
        if dropped:
            objects_info.append({
                "objectId":      obj_id,
                "role":          p.get("role"),
                "bbox":          None,
                "scale":         None,
                "crop":          None,
                "safeZonePassed": None,
                "dropped":       True,
                "imagePath":     None,
                "sourceType":    None,
            })
        else:
            sz_ok = _object_sz_passed(p, safe_zones, target_w, target_h)
            obj   = objs_by_id.get(obj_id, {})
            objects_info.append({
                "objectId":      obj_id,
                "role":          p.get("role"),
                "bbox": {
                    "x":      int(p.get("x", 0)),
                    "y":      int(p.get("y", 0)),
                    "width":  int(p.get("width", 0)),
                    "height": int(p.get("height", 0)),
                },
                "scale":         p.get("scale"),
                "crop":          p.get("crop"),
                "safeZonePassed": sz_ok,
                "dropped":       False,
                "imagePath":     obj.get("imagePath"),
                "sourceType":    obj.get("sourceType"),
            })

    # topCandidates 요약
    top_summary = []
    for c in top_candidates:
        top_summary.append({
            "candidateId":     c.get("candidateId"),
            "score":           c.get("score"),
            "hardFail":        c.get("hardFail", False),
            "hardFailReasons": c.get("hardFailReasons", []),
            "safeZonePassed":  not any(
                "safe zone" in r.lower() for r in c.get("hardFailReasons", [])
            ),
            "placementCount": len([
                p for p in c.get("placements", []) if not p.get("dropped")
            ]),
        })

    # allCandidates 요약 (debug용 — 왜 다른 template이 실패했는지 확인)
    all_summary = []
    for c in all_candidates:
        all_summary.append({
            "candidateId":     c.get("candidateId"),
            "score":           c.get("score"),
            "hardFail":        c.get("hardFail", False),
            "hardFailReasons": c.get("hardFailReasons", []),
        })

    return {
        "target":              {"width": target_w, "height": target_h},
        "renderSource":        render_source,
        "actualPsdRenderMode": actual_render_mode,
        "renderMode":          comp_meta.get("renderMode"),
        "layoutScore":         comp_meta.get("layoutScore"),
        "layoutScoreStatus":   layout_score_status,
        "selectedCandidateId": comp_meta.get("selectedCandidateId"),
        "safeZonePassed":      comp_meta.get("safeZonePassed"),
        "safeZone": {
            "general": safe_zones.get("general"),
            "text":    safe_zones.get("text"),
            "cta":     safe_zones.get("cta"),
        },
        "safeZoneBox":         sz_box,
        "backgroundMode":      comp_meta.get("backgroundMode"),
        "candidateCount":      comp_meta.get("candidateCount"),
        "objects":             objects_info,
        "droppedObjects":      comp_meta.get("droppedObjects", []),
        "safeZoneViolations":  comp_meta.get("safeZoneViolations", []),
        "hardFailReasons":     comp_meta.get("hardFailReasons", []),
        "warnings":            comp_meta.get("warnings", []),
        "missingRequiredAssets": comp_meta.get("missingRequiredAssets", []),
        "topCandidates":       top_summary,
        "allCandidates":       all_summary,
    }


# ─── 공개 진입점 ──────────────────────────────────────────────────────────────

def is_debug_enabled() -> bool:
    """CREATIVE_DEBUG_OVERLAY 환경변수가 true/1/yes이면 True."""
    return os.environ.get("CREATIVE_DEBUG_OVERLAY", "").lower() in ("1", "true", "yes")


def generate_debug_files(
    result_img: Image.Image,
    out_path: str,
    comp_meta: dict,
    layout_result: dict,
    creative_object_set: dict,
    safe_zones: dict,
    target_w: int,
    target_h: int,
    render_source: str = "psd_object_reflow",
    actual_render_mode: str = "object-layout-reflow",
    layout_score_status: str = "normal",
    job_id: str = None,
) -> list:
    """Debug overlay PNG + layout JSON 생성.

    CREATIVE_DEBUG_OVERLAY=true 일 때만 파일을 생성한다.
    overlay 생성 실패는 내부에서 catch해 원본 이미지에 영향을 주지 않는다.

    반환: 생성된 파일 경로 목록 (비활성 또는 실패 시 빈 리스트)
    """
    if not is_debug_enabled():
        return []

    try:
        base             = os.path.splitext(out_path)[0]
        debug_png_path   = base + ".debug.png"
        layout_json_path = base + ".layout.json"
        created: list    = []

        # ── overlay PNG ──────────────────────────────────────────────────────
        best        = layout_result.get("best") or {}
        overlay_img = _build_overlay(result_img, best, safe_zones, target_w, target_h)
        overlay_img.convert("RGB").save(debug_png_path)
        created.append(debug_png_path)
        print(f"[{job_id or 'job'}][DebugOverlay] {debug_png_path}")

        # ── layout JSON ──────────────────────────────────────────────────────
        layout_json = _build_layout_json(
            comp_meta, layout_result, creative_object_set,
            safe_zones, target_w, target_h,
            render_source, actual_render_mode, layout_score_status,
        )
        with open(layout_json_path, "w", encoding="utf-8") as f:
            json.dump(layout_json, f, ensure_ascii=False, indent=2)
        created.append(layout_json_path)
        print(f"[{job_id or 'job'}][DebugOverlay] {layout_json_path}")

        return created

    except Exception as e:
        print(f"[{job_id or 'job'}][DebugOverlay] 생성 실패 (원본 무영향): {e}")
        print(traceback.format_exc())
        return []
