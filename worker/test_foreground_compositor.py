"""Stage 21: Foreground compositor unit tests.

Coverage:
  T01  layer_role_classifier — human_subject rule
  T02  layer_role_classifier — product rule (not human_subject)
  T03  layer_role_classifier — human_subject takes priority over old main_image keywords
  T04  layer_role_classifier — PRIORITY_MAP includes human_subject and product
  T05  source_faithful_repair — _IMMUTABLE_ROLES includes human_subject
  T06  source_faithful_repair — _PRODUCT_ROLES includes product
  T07  extract_foreground_layers — empty psd_layers returns []
  T08  extract_foreground_layers — background role is excluded
  T09  extract_foreground_layers — _layer_obj=None layers are skipped
  T10  extract_foreground_layers — bbox scales correctly
  T11  extract_foreground_layers — zero-size layers are skipped
  T12  composite_foreground — empty foreground returns success with unchanged background
  T13  composite_foreground — single layer pastes at correct position
  T14  composite_foreground — z-order: cta above product
  T15  composite_foreground — product_placed flag set when product present
  T16  composite_foreground — human_subject_preserved flag set
  T17  composite_foreground — logo_placed, headline_placed, cta_placed flags
  T18  composite_foreground — fully out-of-bounds layer is skipped gracefully
  T19  ForegroundCompositeResult — success=True even with zero foreground layers
  T20  early break — SFR stops after first accepted attempt

실행:
  cd worker
  python test_foreground_compositor.py
"""

import sys
import os
import io
import tempfile
import shutil
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image
import numpy as np

PASS = 0
FAIL = 0


def check(label: str, condition: bool, detail: str = ""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        info = f" ({detail})" if detail else ""
        print(f"  [FAIL] {label}{info}")


def _solid(w: int, h: int, color=(180, 100, 50)) -> Image.Image:
    return Image.new("RGB", (w, h), color)


def _rgba(w: int, h: int, color=(180, 100, 50, 200)) -> Image.Image:
    return Image.new("RGBA", (w, h), color)


def _noise_rgba(w: int, h: int) -> Image.Image:
    arr = np.random.randint(0, 255, (h, w, 4), dtype=np.uint8)
    arr[:, :, 3] = 200  # semi-transparent
    return Image.fromarray(arr, "RGBA")


# ── Fake _layer_obj ────────────────────────────────────────────────────────────

class FakeLayerObj:
    def __init__(self, img: Image.Image):
        self._img = img

    def composite(self) -> Image.Image:
        return self._img


class FakeLayerObjFailing:
    def composite(self):
        raise RuntimeError("composite() failed")


def _make_layer(role, name, bbox, lobj=None, depth=0):
    return {
        "role": role,
        "name": name,
        "id": f"{name}_{bbox['x']}_{bbox['y']}",
        "bbox": bbox,
        "depth": depth,
        "_layer_obj": lobj,
        "type": "pixel",
        "canvasWidth": 1000,
        "canvasHeight": 600,
    }


# ══════════════════════════════════════════════════════════════════════════════
# T01-T04: layer_role_classifier
# ══════════════════════════════════════════════════════════════════════════════

print("\n=== [T01-T04] layer_role_classifier ===")

from layer_role_classifier import classify_role_by_name, PRIORITY_MAP

# T01: 모델/person → human_subject
check("T01: '모델' -> human_subject", classify_role_by_name("모델") == "human_subject")
check("T01: 'person_layer' -> human_subject", classify_role_by_name("person_layer") == "human_subject")
check("T01: 'model_img' -> human_subject", classify_role_by_name("model_img") == "human_subject")
check("T01: 'human_cut' -> human_subject", classify_role_by_name("human_cut") == "human_subject")

# T02: 제품/product → product (not human_subject)
check("T02: '제품이미지' -> product", classify_role_by_name("제품이미지") == "product")
check("T02: 'product_shot' -> product", classify_role_by_name("product_shot") == "product")
check("T02: 'goods_main' -> product", classify_role_by_name("goods_main") == "product")
check("T02: 'pack_front' -> product", classify_role_by_name("pack_front") == "product")

# T03: old main_image keywords now split
check("T03: 'main_visual' -> main_image (not product)", classify_role_by_name("main_visual") == "main_image")
check("T03: 'image_layer' -> main_image", classify_role_by_name("image_layer") == "main_image")

# T04: PRIORITY_MAP includes new roles
check("T04: human_subject in PRIORITY_MAP", "human_subject" in PRIORITY_MAP)
check("T04: product in PRIORITY_MAP", "product" in PRIORITY_MAP)
check("T04: human_subject priority=required", PRIORITY_MAP["human_subject"] == "required")
check("T04: product priority=required", PRIORITY_MAP["product"] == "required")


# ══════════════════════════════════════════════════════════════════════════════
# T05-T06: source_faithful_repair role sets
# ══════════════════════════════════════════════════════════════════════════════

print("\n=== [T05-T06] source_faithful_repair role sets ===")

from background.source_faithful_repair import _IMMUTABLE_ROLES, _PRODUCT_ROLES

check("T05: human_subject in _IMMUTABLE_ROLES", "human_subject" in _IMMUTABLE_ROLES)
check("T05: person in _IMMUTABLE_ROLES", "person" in _IMMUTABLE_ROLES)
check("T05: person_or_hand in _IMMUTABLE_ROLES", "person_or_hand" in _IMMUTABLE_ROLES)
check("T06: product in _PRODUCT_ROLES", "product" in _PRODUCT_ROLES)
check("T06: main_image in _PRODUCT_ROLES", "main_image" in _PRODUCT_ROLES)


# ══════════════════════════════════════════════════════════════════════════════
# T07-T11: extract_foreground_layers
# ══════════════════════════════════════════════════════════════════════════════

print("\n=== [T07-T11] extract_foreground_layers ===")

from foreground.layer_extractor import extract_foreground_layers

# T07: empty input
result = extract_foreground_layers([], 1000, 600, 500, 300)
check("T07: empty psd_layers -> []", result == [])

# T08: background role excluded
bg_layer = _make_layer(
    "background", "bg", {"x": 0, "y": 0, "width": 1000, "height": 600},
    lobj=FakeLayerObj(_rgba(1000, 600)),
)
result = extract_foreground_layers([bg_layer], 1000, 600, 500, 300)
check("T08: background role excluded", result == [])

# T09: _layer_obj=None skipped
null_obj_layer = _make_layer(
    "product", "prod", {"x": 100, "y": 100, "width": 200, "height": 200},
    lobj=None,
)
result = extract_foreground_layers([null_obj_layer], 1000, 600, 500, 300)
check("T09: None _layer_obj skipped", result == [])

# T10: bbox scales correctly 1000x600 → 500x300 (scale 0.5)
prod_img = _rgba(200, 200)
prod_layer = _make_layer(
    "product", "prod", {"x": 100, "y": 100, "width": 200, "height": 200},
    lobj=FakeLayerObj(prod_img),
)
result = extract_foreground_layers([prod_layer], 1000, 600, 500, 300)
check("T10: one product layer extracted", len(result) == 1)
if result:
    check("T10: x scaled 100 -> 50", result[0]["bbox"]["x"] == 50)
    check("T10: y scaled 100 -> 50", result[0]["bbox"]["y"] == 50)
    check("T10: width scaled 200 -> 100", result[0]["bbox"]["width"] == 100)
    check("T10: height scaled 200 -> 100", result[0]["bbox"]["height"] == 100)
    check("T10: image resized to 100x100", result[0]["image"].size == (100, 100))

# T11: zero-size layer skipped
zero_layer = _make_layer(
    "logo", "logo", {"x": 10, "y": 10, "width": 0, "height": 50},
    lobj=FakeLayerObj(_rgba(0, 50)),
)
result = extract_foreground_layers([zero_layer], 1000, 600, 500, 300)
check("T11: zero-width layer skipped", result == [])


# ══════════════════════════════════════════════════════════════════════════════
# T12-T19: composite_foreground
# ══════════════════════════════════════════════════════════════════════════════

print("\n=== [T12-T19] composite_foreground ===")

from foreground.compositor import composite_foreground, ForegroundCompositeResult

bg = _solid(500, 300, (50, 50, 200))

# T12: empty foreground → success, background unchanged
res = composite_foreground(bg, [])
check("T12: empty fg -> success=True", res.success)
check("T12: composite_image is not None", res.composite_image is not None)
check("T12: placed_roles is []", res.placed_roles == [])

# T13: single layer at known position
red_patch = _rgba(50, 50, (255, 0, 0, 255))
layers = [{"role": "product", "name": "prod", "image": red_patch,
           "bbox": {"x": 10, "y": 10, "width": 50, "height": 50}, "depth": 0}]
res = composite_foreground(bg, layers)
check("T13: product layer -> success", res.success)
if res.composite_image:
    r, g, b = res.composite_image.getpixel((35, 35))[:3]
    check("T13: red patch visible at (35,35)", r > 200 and g < 50 and b < 50, f"actual=({r},{g},{b})")

# T14: z-order — cta (z=9) on top of product (z=2)
green_layer = {"role": "product", "name": "prod", "image": _rgba(100, 100, (0, 255, 0, 255)),
               "bbox": {"x": 100, "y": 100, "width": 100, "height": 100}, "depth": 0}
blue_cta = {"role": "cta", "name": "cta", "image": _rgba(100, 100, (0, 0, 255, 255)),
            "bbox": {"x": 100, "y": 100, "width": 100, "height": 100}, "depth": 0}
res = composite_foreground(bg, [blue_cta, green_layer])  # intentionally reversed input order
if res.composite_image:
    r, g, b = res.composite_image.getpixel((150, 150))[:3]
    check("T14: cta (blue) rendered on top of product (green)", b > 200 and g < 50, f"actual=({r},{g},{b})")

# T15: product_placed flag
product_layer = {"role": "product", "name": "prod", "image": _rgba(50, 50),
                 "bbox": {"x": 0, "y": 0, "width": 50, "height": 50}, "depth": 0}
res = composite_foreground(bg, [product_layer])
check("T15: product_placed=True", res.product_placed)

# T16: human_subject_preserved flag
hs_layer = {"role": "human_subject", "name": "model", "image": _rgba(100, 200),
            "bbox": {"x": 50, "y": 0, "width": 100, "height": 200}, "depth": 0}
res = composite_foreground(bg, [hs_layer])
check("T16: human_subject_preserved=True", res.human_subject_preserved)

# T17: logo, headline, cta flags
logo_l = {"role": "logo",      "name": "logo", "image": _rgba(50, 30),
           "bbox": {"x": 400, "y": 0, "width": 50, "height": 30}, "depth": 0}
title_l = {"role": "title",    "name": "title", "image": _rgba(200, 40),
            "bbox": {"x": 100, "y": 240, "width": 200, "height": 40}, "depth": 0}
cta_l   = {"role": "cta",      "name": "cta", "image": _rgba(120, 40),
            "bbox": {"x": 180, "y": 260, "width": 120, "height": 40}, "depth": 0}
res = composite_foreground(bg, [logo_l, title_l, cta_l])
check("T17: logo_placed=True", res.logo_placed)
check("T17: headline_placed=True", res.headline_placed)
check("T17: cta_placed=True", res.cta_placed)

# T18: completely out-of-bounds layer skipped gracefully
oob_layer = {"role": "product", "name": "oob", "image": _rgba(50, 50),
             "bbox": {"x": 600, "y": 400, "width": 50, "height": 50}, "depth": 0}
res = composite_foreground(bg, [oob_layer])
check("T18: out-of-bounds layer -> success=True (no crash)", res.success)
check("T18: oob layer in skipped_roles", "product" in res.skipped_roles)

# T19: ForegroundCompositeResult success with zero layers
res = composite_foreground(bg, [])
check("T19: no layers -> success=True", res.success)
check("T19: layer_count=0", res.layer_count == 0)


# ══════════════════════════════════════════════════════════════════════════════
# T20: SFR early break after first accepted attempt
# ══════════════════════════════════════════════════════════════════════════════

print("\n=== [T20] SFR early break ===")

from background.source_faithful_repair import run_source_faithful_repair

_call_count = [0]  # mutable container to allow mutation from nested scope

class CountingProvider:
    def metadata(self):
        return {"providerName": "counting", "modelName": "count-1"}

    def inpaint(self, image, mask, prompt, options):
        _call_count[0] += 1
        # Return valid noise image so acceptance triggers early break
        w, h = image.size
        arr = np.random.randint(30, 200, (h, w, 3), dtype=np.uint8)
        return Image.fromarray(arr, "RGB")


# Use target larger than source so outpaint mask > 0 → AI provider is called.
source = Image.new("RGB", (200, 200), (100, 100, 100))
sfr = run_source_faithful_repair(
    source_image=source,
    classified_layers=[],
    target_w=300,    # outpaint → forces AI call
    target_h=200,
    provider=CountingProvider(),
    max_attempts=3,
    request_id="test-early-break",
)
check("T20: SFR succeeded", sfr.success)
check("T20: provider called exactly once (early break)", _call_count[0] == 1,
      f"actual call_count={_call_count[0]}")
check("T20: background_ai_attempt_count=1", sfr.background_ai_attempt_count == 1,
      f"actual={sfr.background_ai_attempt_count}")


# ══════════════════════════════════════════════════════════════════════════════
# Result
# ══════════════════════════════════════════════════════════════════════════════

total = PASS + FAIL
print(f"\n{'='*60}")
print(f"RESULT: {PASS}/{total} PASS  ({FAIL} FAIL)")
print(f"{'='*60}")
sys.exit(0 if FAIL == 0 else 1)
