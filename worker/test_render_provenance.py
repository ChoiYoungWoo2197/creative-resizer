"""Stage 20.3 Render Provenance 단위 테스트.

renderProvenance 딕셔너리가 6가지 입력 유형 × 3가지 규격에서
올바른 blurFillUsed / forcedSmartFit / effectiveResizeMode 값을 가지는지 검증.

실행:
  cd worker
  python test_render_provenance.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image
from resizer import _apply_resize, is_wide_banner_case

# ─── 테스트 규격 (3종) ────────────────────────────────────────────────────────

SPECS = [
    {"name": "square",       "w": 800,  "h": 800},
    {"name": "wide",         "w": 1250, "h": 560},
    {"name": "ultravertical","w": 300,  "h": 1200},
]

STRENGTH = "balanced"
FOCAL    = "center"

# ─── 헬퍼 ─────────────────────────────────────────────────────────────────────

PASS = 0
FAIL = 0

def check(label: str, condition: bool):
    global PASS, FAIL
    status = "PASS" if condition else "FAIL"
    if not condition:
        FAIL += 1
        print(f"  [{status}] {label}")
    else:
        PASS += 1
        print(f"  [{status}] {label}")

def make_img(w: int, h: int) -> Image.Image:
    return Image.new("RGB", (w, h), color=(200, 150, 100))

def build_provenance_image_path(resize_mode: str, enhance_meta: dict, source_type: str = "image") -> dict:
    """이미지 경로에서 renderProvenance를 조립하는 로직 (resizer.py image path와 동일)."""
    eff_mode  = enhance_meta.get("resizeStrategy") or resize_mode
    blur_used = enhance_meta.get("blurFillUsed", resize_mode == "smart-fit")
    return {
        "requestedResizeMode":     resize_mode,
        "effectiveResizeMode":     eff_mode,
        "blurFillUsed":            blur_used,
        "forcedSmartFit":          False,   # 이미지 경로에서는 강제 없음
        "sourceType":              source_type,
        "psdMode":                 None,
        "backgroundPipelineUsed":  False,
        "sourceFaithfulRepairUsed": False,
    }

def build_provenance_artboard(resize_mode: str, actual_render_mode: str, enhance_meta: dict) -> dict:
    """PSD artboard-first 경로에서 renderProvenance를 조립하는 로직 (resizer.py와 동일)."""
    eff_mode    = resize_mode if actual_render_mode != "artboard" else "smart-fit"
    forced_sf   = (eff_mode != resize_mode)
    psd_eff     = enhance_meta.get("resizeStrategy") or eff_mode
    psd_blur    = enhance_meta.get("blurFillUsed", eff_mode == "smart-fit")
    return {
        "requestedResizeMode":     resize_mode,
        "effectiveResizeMode":     psd_eff,
        "blurFillUsed":            psd_blur,
        "forcedSmartFit":          forced_sf,
        "sourceType":              "psd",
        "psdMode":                 "artboard-first",
        "backgroundPipelineUsed":  False,
        "sourceFaithfulRepairUsed": False,
    }

def build_provenance_layer_reflow(resize_mode: str, layer_reflow_succeeded: bool, enhance_meta: dict) -> dict:
    """PSD layer-reflow 경로에서 renderProvenance를 조립하는 로직."""
    lr_eff  = enhance_meta.get("resizeStrategy") or ("psd-layer-reflow" if layer_reflow_succeeded else "smart-fit")
    lr_blur = not layer_reflow_succeeded
    return {
        "requestedResizeMode":     resize_mode,
        "effectiveResizeMode":     lr_eff,
        "blurFillUsed":            lr_blur,
        "forcedSmartFit":          not layer_reflow_succeeded,
        "sourceType":              "psd",
        "psdMode":                 "layer-reflow",
        "backgroundPipelineUsed":  False,
        "sourceFaithfulRepairUsed": False,
    }

def build_provenance_object_reflow(resize_mode: str, obj_reflow_succeeded: bool) -> dict:
    """PSD object-reflow 경로에서 renderProvenance를 조립하는 로직."""
    return {
        "requestedResizeMode":     resize_mode,
        "effectiveResizeMode":     "psd-object-layout-reflow" if obj_reflow_succeeded else "smart-fit",
        "blurFillUsed":            not obj_reflow_succeeded,
        "forcedSmartFit":          not obj_reflow_succeeded,
        "sourceType":              "psd",
        "psdMode":                 "object-reflow",
        "backgroundPipelineUsed":  False,
        "sourceFaithfulRepairUsed": False,
    }

# ─── 테스트 1: _apply_resize blurFillUsed 정확도 ──────────────────────────────

print("\n=== [T1] _apply_resize blurFillUsed 정확도 ===")
print("소스 이미지: 800x600 (정사각/가로 중간)")
src = make_img(800, 600)

for spec in SPECS:
    w, h = spec["w"], spec["h"]
    wide_case = is_wide_banner_case(src.width, src.height, w, h)
    print(f"\n  spec={spec['name']} ({w}x{h}) wide_banner_case={wide_case}")

    # smart-fit
    _, meta_sf = _apply_resize(src, w, h, "smart-fit", STRENGTH, FOCAL)
    if wide_case:
        # wide-banner: candidateScore 없으면 blur 사용(=True), 있고 ≥50이면 False
        expected_blur = meta_sf.get("candidateScore") is None or meta_sf.get("candidateScore", 50) < 50
        check(f"smart-fit wide-banner blurFillUsed={meta_sf['blurFillUsed']} == {expected_blur}",
              meta_sf["blurFillUsed"] == expected_blur)
    else:
        check(f"smart-fit blurFillUsed=True",
              meta_sf["blurFillUsed"] is True)

    # cover (non-blur)
    _, meta_cov = _apply_resize(src, w, h, "cover", STRENGTH, FOCAL)
    check(f"cover   blurFillUsed=False",
          meta_cov["blurFillUsed"] is False)

    # contain (non-blur)
    _, meta_cnt = _apply_resize(src, w, h, "contain", STRENGTH, FOCAL)
    check(f"contain blurFillUsed=False",
          meta_cnt["blurFillUsed"] is False)

# ─── 테스트 2: 이미지 경로(PNG/JPG) renderProvenance 조립 ────────────────────

print("\n=== [T2] 이미지 경로 renderProvenance (PNG/JPG) ===")
src_png = make_img(1200, 628)   # 대표 이미지

for spec in SPECS:
    w, h = spec["w"], spec["h"]
    print(f"\n  spec={spec['name']} ({w}x{h})")

    for mode in ("smart-fit", "cover", "contain"):
        _, meta = _apply_resize(src_png, w, h, mode, STRENGTH, FOCAL)
        prov    = build_provenance_image_path(mode, meta, source_type="image")

        # 구조 필드 존재 확인
        check(f"[{mode}] requestedResizeMode == '{mode}'",
              prov["requestedResizeMode"] == mode)
        check(f"[{mode}] sourceType == 'image'",
              prov["sourceType"] == "image")
        check(f"[{mode}] psdMode is None",
              prov["psdMode"] is None)
        check(f"[{mode}] forcedSmartFit == False",
              prov["forcedSmartFit"] is False)

        # blurFillUsed 검증
        if mode == "smart-fit":
            check(f"[{mode}] blurFillUsed consistent with meta",
                  prov["blurFillUsed"] == meta["blurFillUsed"])
        else:
            check(f"[{mode}] blurFillUsed == False",
                  prov["blurFillUsed"] is False)

        # effectiveResizeMode
        check(f"[{mode}] effectiveResizeMode present",
              prov["effectiveResizeMode"] is not None)

# ─── 테스트 3: PSD artboard-first 경로 (actual_render_mode 분기) ─────────────

print("\n=== [T3] PSD artboard-first renderProvenance ===")
src_psd = make_img(1200, 1200)  # PSD 아트보드 크기

for spec in SPECS:
    w, h = spec["w"], spec["h"]
    print(f"\n  spec={spec['name']} ({w}x{h})")

    for mode in ("smart-fit", "cover"):
        _, meta = _apply_resize(src_psd, w, h, "smart-fit", STRENGTH, FOCAL)
        # actual_render_mode="artboard" 이면 eff_mode 강제 smart-fit
        prov_ab = build_provenance_artboard(mode, actual_render_mode="artboard", enhance_meta=meta)
        check(f"[{mode}→artboard] sourceType == 'psd'",
              prov_ab["sourceType"] == "psd")
        check(f"[{mode}→artboard] psdMode == 'artboard-first'",
              prov_ab["psdMode"] == "artboard-first")
        if mode != "smart-fit":
            check(f"[{mode}→artboard] forcedSmartFit == True (non-smart-fit mode forced)",
                  prov_ab["forcedSmartFit"] is True)
            # wide 규격은 _apply_resize에서 wide-banner-smart-fit으로 승격될 수 있음
            eff = prov_ab["effectiveResizeMode"]
            check(f"[{mode}→artboard] effectiveResizeMode is smart-fit family ({eff})",
                  eff in ("smart-fit", "wide-banner-smart-fit"))
            check(f"[{mode}→artboard] blurFillUsed == True (forced smart-fit → blur)",
                  prov_ab["blurFillUsed"] is True)
        else:
            check(f"[smart-fit→artboard] forcedSmartFit == False",
                  prov_ab["forcedSmartFit"] is False)

        # actual_render_mode="full-canvas" 이면 강제 없음
        _, meta2 = _apply_resize(src_psd, w, h, mode, STRENGTH, FOCAL)
        prov_fc = build_provenance_artboard(mode, actual_render_mode="full-canvas", enhance_meta=meta2)
        check(f"[{mode}→full-canvas] forcedSmartFit == False",
              prov_fc["forcedSmartFit"] is False)

# ─── 테스트 4: PSD layer-reflow 경로 ─────────────────────────────────────────

print("\n=== [T4] PSD layer-reflow renderProvenance ===")

for spec in SPECS:
    w, h = spec["w"], spec["h"]
    print(f"\n  spec={spec['name']} ({w}x{h})")

    for succeeded in (True, False):
        meta_lr = {"resizeStrategy": "psd-layer-reflow"} if succeeded else {}
        prov_lr = build_provenance_layer_reflow("smart-fit", succeeded, meta_lr)
        check(f"[reflow={succeeded}] psdMode == 'layer-reflow'",
              prov_lr["psdMode"] == "layer-reflow")
        check(f"[reflow={succeeded}] sourceType == 'psd'",
              prov_lr["sourceType"] == "psd")
        if succeeded:
            check(f"[reflow=True]  blurFillUsed == False",
                  prov_lr["blurFillUsed"] is False)
            check(f"[reflow=True]  forcedSmartFit == False",
                  prov_lr["forcedSmartFit"] is False)
            check(f"[reflow=True]  effectiveResizeMode == 'psd-layer-reflow'",
                  prov_lr["effectiveResizeMode"] == "psd-layer-reflow")
        else:
            check(f"[reflow=False] blurFillUsed == True (fallback to smart-fit)",
                  prov_lr["blurFillUsed"] is True)
            check(f"[reflow=False] forcedSmartFit == True",
                  prov_lr["forcedSmartFit"] is True)
            check(f"[reflow=False] effectiveResizeMode == 'smart-fit'",
                  prov_lr["effectiveResizeMode"] == "smart-fit")

# ─── 테스트 5: PSD object-reflow 경로 ────────────────────────────────────────

print("\n=== [T5] PSD object-reflow renderProvenance ===")

for spec in SPECS:
    w, h = spec["w"], spec["h"]
    print(f"\n  spec={spec['name']} ({w}x{h})")

    for succeeded in (True, False):
        prov_or = build_provenance_object_reflow("smart-fit", succeeded)
        check(f"[obj_reflow={succeeded}] psdMode == 'object-reflow'",
              prov_or["psdMode"] == "object-reflow")
        if succeeded:
            check(f"[obj_reflow=True]  blurFillUsed == False",
                  prov_or["blurFillUsed"] is False)
            check(f"[obj_reflow=True]  forcedSmartFit == False",
                  prov_or["forcedSmartFit"] is False)
            check(f"[obj_reflow=True]  effectiveResizeMode == 'psd-object-layout-reflow'",
                  prov_or["effectiveResizeMode"] == "psd-object-layout-reflow")
        else:
            check(f"[obj_reflow=False] blurFillUsed == True",
                  prov_or["blurFillUsed"] is True)
            check(f"[obj_reflow=False] forcedSmartFit == True",
                  prov_or["forcedSmartFit"] is True)
            check(f"[obj_reflow=False] effectiveResizeMode == 'smart-fit'",
                  prov_or["effectiveResizeMode"] == "smart-fit")

# ─── 테스트 6: is_wide_banner_case 정확도 (wide spec에서만 True) ─────────────

print("\n=== [T6] is_wide_banner_case 분기 확인 ===")
sq  = make_img(1000, 1000)   # 정사각 소스
port = make_img(600, 900)    # 세로 소스

check("정사각 소스 → square spec   wide_banner=False", not is_wide_banner_case(sq.width, sq.height, 800, 800))
check("정사각 소스 → wide spec     wide_banner=True",  is_wide_banner_case(sq.width, sq.height, 1250, 560))
check("정사각 소스 → vertical spec wide_banner=False", not is_wide_banner_case(sq.width, sq.height, 300, 1200))
check("세로 소스   → wide spec     wide_banner=True",  is_wide_banner_case(port.width, port.height, 1250, 560))
check("가로 소스   → wide spec     wide_banner=False", not is_wide_banner_case(1250, 400, 1250, 560))

# ─── 결과 ─────────────────────────────────────────────────────────────────────

total = PASS + FAIL
print(f"\n{'='*60}")
print(f"RESULT: {PASS}/{total} PASS  ({FAIL} FAIL)")
print(f"{'='*60}")
sys.exit(0 if FAIL == 0 else 1)
