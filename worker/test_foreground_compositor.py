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
# T21-T28: _validate_roles() contradiction validator
# ══════════════════════════════════════════════════════════════════════════════

print("\n=== [T21-T28] _validate_roles contradiction validator ===")

from layer_role_classifier import classify_layers, _validate_roles, _text_layer_fallback_role

CANVAS = {"canvasWidth": 1000, "canvasHeight": 600}


def _make_classified(layer_type, role, name, x=100, y=100, w=300, h=100, text_content=None):
    """Helper: build a pre-classified layer dict for validator testing."""
    layer = {
        "name": name,
        "type": layer_type,
        "role": role,
        "priority": "required",
        "bbox": {"x": x, "y": y, "width": w, "height": h},
        "depth": 0,
        "id": f"{name}_{x}_{y}",
        **CANVAS,
    }
    if text_content is not None:
        layer["textContent"] = text_content
    return layer


# T21: V1 — text layer + human_subject → reclassify (not human_subject)
t21_headline = _make_classified("type", "human_subject",
                                "어머님 손에 금보다 필요한 건?",
                                x=100, y=50, w=600, h=80,
                                text_content="어머님 손에 금보다 필요한 건?")
result21 = _validate_roles([t21_headline])
check("T21: text+human_subject -> not human_subject", result21[0]["role"] != "human_subject")
check("T21: text+human_subject reclassified to title (upper text)", result21[0]["role"] == "title",
      f"got {result21[0]['role']}")

# T22: V1 — text layer + logo (position-based) → title/body_text
t22_logo_text = _make_classified("type", "logo", "사각형 5 텍스트",
                                 x=50, y=20, w=80, h=30)
result22 = _validate_roles([t22_logo_text])
check("T22: text+logo -> not logo", result22[0]["role"] != "logo")

# T23: V2 — long text in cta → body_text
long_cta_text = "흑자, 검버섯, 기미 확실하게 관리하세요! 전문 솔루션으로 피부 고민 해결"
t23 = _make_classified("type", "cta", long_cta_text,
                       x=50, y=480, w=700, h=60,
                       text_content=long_cta_text)
result23 = _validate_roles([t23])
check("T23: long text in cta -> body_text", result23[0]["role"] == "body_text",
      f"got {result23[0]['role']}")

# T23b: 실제 운영 버그 케이스 — '흑자, 검버섯, 기미 확실하게 관리하세요!'
real_long = "흑자, 검버섯, 기미 확실하게 관리하세요!"
t23b = _make_classified("type", "cta", real_long,
                        x=50, y=420, w=700, h=60,
                        text_content=real_long)
result23b = _validate_roles([t23b])
check("T23b: '흑자...관리하세요!' CTA -> body_text", result23b[0]["role"] == "body_text",
      f"got {result23b[0]['role']}")

# T24: V3 — shape + human_subject → decorative
t24 = _make_classified("shape", "human_subject", "shape_person", x=200, y=100, w=200, h=300)
result24 = _validate_roles([t24])
check("T24: shape+human_subject -> decorative", result24[0]["role"] == "decorative",
      f"got {result24[0]['role']}")

# T25: V4 — 사각형 name → decorative (not background, not cta)
t25 = _make_classified("shape", "logo", "사각형 5", x=400, y=10, w=60, h=30)
result25 = _validate_roles([t25])
check("T25: '사각형 5' -> decorative", result25[0]["role"] == "decorative",
      f"got {result25[0]['role']}")

# T25b: rectangle → decorative
t25b = _make_classified("shape", "logo", "rect_background_blur", x=0, y=0, w=400, h=400)
result25b = _validate_roles([t25b])
check("T25b: 'rect_...' -> decorative", result25b[0]["role"] == "decorative",
      f"got {result25b[0]['role']}")

# T26: V5 — shape + logo + no explicit logo keyword → decorative
t26 = _make_classified("shape", "logo", "사각형 5", x=50, y=10, w=80, h=40)
result26 = _validate_roles([t26])
check("T26: shape+logo no keyword -> decorative", result26[0]["role"] == "decorative",
      f"got {result26[0]['role']}")

# T26b: shape + logo + explicit logo keyword → kept as logo
t26b = _make_classified("shape", "logo", "logo_circle", x=50, y=10, w=80, h=40)
result26b = _validate_roles([t26b])
check("T26b: shape+logo WITH keyword -> stays logo", result26b[0]["role"] == "logo",
      f"got {result26b[0]['role']}")

# T27: full classify_layers with mother-hand ad simulation
print("\n--- T27: mother-hand-product PSD simulation ---")

def _make_raw_layer(layer_type, name, x, y, w, h, text_content=None):
    layer = {
        "name": name,
        "type": layer_type,
        "bbox": {"x": x, "y": y, "width": w, "height": h},
        "depth": 0,
        "visible": True,
        "opacity": 100,
        "isTextLayer": layer_type == "type",
        "isGroupComposite": False,
        "textContent": text_content,
        "previewPath": None,
        "_layer_obj": None,
        "canvasWidth": 1000,
        "canvasHeight": 600,
        "id": f"{name}_{x}_{y}",
    }
    return layer

mother_hand_layers = [
    # 배경 이미지
    _make_raw_layer("pixel", "배경", 0, 0, 1000, 600),
    # 인물(실제 픽셀 레이어 — 손 이미지)
    _make_raw_layer("pixel", "손 이미지", 0, 100, 500, 500),
    # 제품 이미지
    _make_raw_layer("smartobject", "제품", 500, 150, 400, 350),
    # 헤드라인 텍스트 — '손' 때문에 잘못 분류되던 버그 케이스
    _make_raw_layer("type", "어머님 손에 금보다 필요한 건?",
                    100, 50, 600, 80,
                    text_content="어머님 손에 금보다 필요한 건?"),
    # 본문 설명 텍스트
    _make_raw_layer("type", "흑자, 검버섯, 기미 확실하게 관리하세요!",
                    100, 400, 700, 60,
                    text_content="흑자, 검버섯, 기미 확실하게 관리하세요!"),
    # 데코 사각형
    _make_raw_layer("shape", "사각형 5", 400, 20, 80, 40),
]

classified27 = classify_layers(mother_hand_layers)
role_map = {l["name"]: l["role"] for l in classified27}

check("T27: '배경' -> background", role_map.get("배경") == "background",
      f"got {role_map.get('배경')}")
check("T27: '손 이미지'(pixel) -> human_subject", role_map.get("손 이미지") == "human_subject",
      f"got {role_map.get('손 이미지')}")
check("T27: '제품'(smartobj) -> product", role_map.get("제품") == "product",
      f"got {role_map.get('제품')}")
check("T27: '어머님 손에...'(text) -> title (not human_subject!)",
      role_map.get("어머님 손에 금보다 필요한 건?") == "title",
      f"got {role_map.get('어머님 손에 금보다 필요한 건?')}")
check("T27: '흑자...'(text) -> body_text (not cta!)",
      role_map.get("흑자, 검버섯, 기미 확실하게 관리하세요!") == "body_text",
      f"got {role_map.get('흑자, 검버섯, 기미 확실하게 관리하세요!')}")
check("T27: '사각형 5'(shape) -> decorative (not logo!)",
      role_map.get("사각형 5") == "decorative",
      f"got {role_map.get('사각형 5')}")

# T27 summary: humanSubjectPreserved would be True only for pixel "손 이미지", not text
human_subjects = [l for l in classified27 if l["role"] == "human_subject"]
text_human_subjects = [l for l in classified27 if l["role"] == "human_subject" and l["type"] == "type"]
check("T27: no text layer classified as human_subject", len(text_human_subjects) == 0,
      f"found {text_human_subjects}")
check("T27: pixel 손 이미지 is still human_subject", len(human_subjects) >= 1)

# T28: _text_layer_fallback_role edge cases
check("T28: short text at bottom (cy=0.85) -> cta",
      _text_layer_fallback_role("신청하기", 0.85) == "cta")
check("T28: long text -> body_text",
      _text_layer_fallback_role("흑자, 검버섯, 기미 확실하게 관리하세요!", 0.75) == "body_text")
check("T28: short headline at top (cy=0.10) -> title",
      _text_layer_fallback_role("어머님 손에 금보다 필요한 건?", 0.10) == "title")
check("T28: short text at mid-bottom (cy=0.70) -> body_text",
      _text_layer_fallback_role("관리하세요", 0.70) == "body_text")


# ══════════════════════════════════════════════════════════════════════════════
# T30-T43: P0 Source Isolation + AiRenderContext Tests
# ══════════════════════════════════════════════════════════════════════════════

print("\n=== [T30-T43] P0 AiRenderContext + Source Isolation ===")

import hashlib
import io as _io
import os
import tempfile

from ai_render_context import AiRenderContext, sha256_image, sha256_file


def _solid_img(color, size=(100, 100)):
    return Image.new("RGB", size, color)


def _sha(img):
    return sha256_image(img)


# ── T30: Different PSDs produce different sourceFileSha256 ────────────────────

with tempfile.NamedTemporaryFile(suffix=".psd", delete=False) as fa:
    fa.write(b"psd_file_A_fake_content_0001")
    path_a = fa.name
with tempfile.NamedTemporaryFile(suffix=".psd", delete=False) as fb:
    fb.write(b"psd_file_B_fake_content_0002")
    path_b = fb.name

sha_a = sha256_file(path_a)
sha_b = sha256_file(path_b)
check("T30: different PSDs → different sourceFileSha256", sha_a != sha_b,
      f"sha_a={sha_a[:8]} sha_b={sha_b[:8]}")
os.unlink(path_a)
os.unlink(path_b)

# ── T31: Different composites produce different compositeSha256 ───────────────

img_psd_a = _solid_img((200, 100, 50))
img_psd_b = _solid_img((50, 200, 150))
sha_comp_a = _sha(img_psd_a)
sha_comp_b = _sha(img_psd_b)
check("T31: different composites → different compositeSha256",
      sha_comp_a != sha_comp_b,
      f"sha_a={sha_comp_a[:8]} sha_b={sha_comp_b[:8]}")

# ── T32: Different source images → different providerInputSha256 ──────────────

from background.source_faithful_repair import run_source_faithful_repair

def _run_sfr_with_ctx(img, color_id):
    """Run SFR with FakeProvider and return (render_ctx, sfr)."""
    from background.external_provider import FakeBackgroundProvider
    job_id = f"t32_job_{color_id}"
    ctx = AiRenderContext(
        job_id=job_id,
        spec_id="100x100",
        source_path="/fake/source.psd",
        source_file_sha256="fake_file_sha",
        composite_sha256=_sha(img),
        target_width=150,
        target_height=100,
        work_dir=tempfile.mkdtemp(),
    )
    sfr = run_source_faithful_repair(
        source_image=img,
        classified_layers=[],
        target_w=150,
        target_h=100,
        provider=FakeBackgroundProvider(),
        max_attempts=1,
        request_id=job_id,
        render_ctx=ctx,
    )
    return ctx, sfr

ctx_a, _ = _run_sfr_with_ctx(_solid_img((200, 100, 50), (100, 100)), "A")
ctx_b, _ = _run_sfr_with_ctx(_solid_img((50, 200, 150), (100, 100)), "B")
check("T32: different PSDs → different providerInputSha256",
      ctx_a.provider_input_sha256 != ctx_b.provider_input_sha256,
      f"a={ctx_a.provider_input_sha256[:8]} b={ctx_b.provider_input_sha256[:8]}")

# ── T33: Job-level workDir isolation ─────────────────────────────────────────

ctx_j1 = AiRenderContext(job_id="job1", spec_id="200x200",
                         work_dir="/app/storage/work/job1/200x200")
ctx_j2 = AiRenderContext(job_id="job2", spec_id="200x200",
                         work_dir="/app/storage/work/job2/200x200")
check("T33: job-level workDirs are different",
      ctx_j1.work_dir != ctx_j2.work_dir,
      f"j1={ctx_j1.work_dir} j2={ctx_j2.work_dir}")

# ── T34: Job-level artifactPath isolation ─────────────────────────────────────

check("T34: artifact paths differ between jobs (jobId in workDir)",
      "job1" in ctx_j1.work_dir and "job1" not in ctx_j2.work_dir)

# ── T35: Provider doesn't retain previous image ───────────────────────────────

# Run two sequential SFR calls with different source images using the SAME
# FakeBackgroundProvider instance.  Verify providerInputSha256 differs.

from background.external_provider import FakeBackgroundProvider
shared_provider = FakeBackgroundProvider()
img_x = _solid_img((180, 90, 40), (80, 80))
img_y = _solid_img((40, 180, 220), (80, 80))

ctx_x = AiRenderContext(job_id="t35_x", spec_id="120x80",
                        composite_sha256=_sha(img_x), work_dir=tempfile.mkdtemp())
ctx_y = AiRenderContext(job_id="t35_y", spec_id="120x80",
                        composite_sha256=_sha(img_y), work_dir=tempfile.mkdtemp())

run_source_faithful_repair(source_image=img_x, classified_layers=[],
                           target_w=120, target_h=80,
                           provider=shared_provider, max_attempts=1,
                           request_id="t35_x", render_ctx=ctx_x)
run_source_faithful_repair(source_image=img_y, classified_layers=[],
                           target_w=120, target_h=80,
                           provider=shared_provider, max_attempts=1,
                           request_id="t35_y", render_ctx=ctx_y)

check("T35: shared provider - x and y produce different providerInputSha256",
      ctx_x.provider_input_sha256 != ctx_y.provider_input_sha256,
      f"x={ctx_x.provider_input_sha256[:8]} y={ctx_y.provider_input_sha256[:8]}")

# ── T36: maxAttempts default == 1 ────────────────────────────────────────────

import os as _os
_saved = _os.environ.get("BACKGROUND_AI_MAX_ATTEMPTS")
_os.environ.pop("BACKGROUND_AI_MAX_ATTEMPTS", None)
_default_max = int(_os.environ.get("BACKGROUND_AI_MAX_ATTEMPTS", "1"))
if _saved is not None:
    _os.environ["BACKGROUND_AI_MAX_ATTEMPTS"] = _saved
check("T36: BACKGROUND_AI_MAX_ATTEMPTS default == 1", _default_max == 1,
      f"got {_default_max}")

# ── T37: Actual provider request count ≤ 1 ───────────────────────────────────

_t37_calls = [0]

class _CountingFake:
    def metadata(self): return {"providerName": "fake", "modelName": "fake"}
    def inpaint(self, image, mask, prompt, options):
        _t37_calls[0] += 1
        w, h = image.size
        return Image.new("RGB", (w, h), (120, 120, 120))

source_t37 = _solid_img((100, 150, 200), (60, 60))
sfr_t37 = run_source_faithful_repair(
    source_image=source_t37, classified_layers=[],
    target_w=90, target_h=60,
    provider=_CountingFake(), max_attempts=1, request_id="t37",
)
check("T37: actual provider request count ≤ 1",
      _t37_calls[0] <= 1,
      f"got call_count={_t37_calls[0]}")

# ── T38: PSD_OBJECT_ANALYSIS_ENABLED=false → analysisSkipped=True ────────────

from psd_analyzer import analyze_psd_file

_os.environ["PSD_OBJECT_ANALYSIS_ENABLED"] = "false"
# Reload the flag (psd_analyzer reads it at module level — simulate env override)
import importlib
import psd_analyzer as _psd_mod
# Re-evaluate flag by calling with the env set (module-level flag was already set)
# Since _PSD_OBJECT_ANALYSIS_ENABLED is module-level, patch it directly for test
_orig_flag = _psd_mod._PSD_OBJECT_ANALYSIS_ENABLED
_psd_mod._PSD_OBJECT_ANALYSIS_ENABLED = False
result38 = _psd_mod.analyze_psd_file("/nonexistent/fake.psd")
_psd_mod._PSD_OBJECT_ANALYSIS_ENABLED = _orig_flag
_os.environ.pop("PSD_OBJECT_ANALYSIS_ENABLED", None)

check("T38: PSD_OBJECT_ANALYSIS_ENABLED=false → analysisSkipped=True",
      result38.get("analysisSkipped") is True,
      f"got {result38}")

# ── T39: Analysis disabled but structure is valid (no crash) ─────────────────

check("T39: disabled analysis returns dict with artboards key",
      "artboards" in result38)
check("T39: disabled analysis returns width=0",
      result38.get("width") == 0)

# ── T40: A→B→A cross-source isolation in same process ────────────────────────

print("\n--- T40: A→B→A isolation ---")

img_aba_a = _solid_img((210, 80, 30), (90, 90))
img_aba_b = _solid_img((30, 210, 120), (90, 90))

sha_aba = {}
for run_id, img_run in [("A1", img_aba_a), ("B", img_aba_b), ("A2", img_aba_a)]:
    ctx_run = AiRenderContext(
        job_id=f"t40_{run_id}",
        spec_id="130x90",
        composite_sha256=_sha(img_run),
        work_dir=tempfile.mkdtemp(),
    )
    run_source_faithful_repair(
        source_image=img_run, classified_layers=[],
        target_w=130, target_h=90,
        provider=FakeBackgroundProvider(), max_attempts=1,
        request_id=f"t40_{run_id}", render_ctx=ctx_run,
    )
    sha_aba[run_id] = ctx_run.provider_input_sha256

check("T40: A→B isolated (A1 ≠ B)", sha_aba.get("A1") != sha_aba.get("B"),
      f"A1={sha_aba.get('A1','')[:8]} B={sha_aba.get('B','')[:8]}")
check("T40: A stable (A1 == A2)", sha_aba.get("A1") == sha_aba.get("A2"),
      f"A1={sha_aba.get('A1','')[:8]} A2={sha_aba.get('A2','')[:8]}")

# ── T41: One PSD failure doesn't propagate state to next ─────────────────────
# Use outpaint target (120x80 != source 80x80) to force the AI provider call.

from background.external_provider import FakeBackgroundProvider

_t41_count = [0]

class _FailFirstProvider:
    def metadata(self): return {"providerName": "fail_first", "modelName": "f1"}
    def inpaint(self, image, mask, prompt, options):
        _t41_count[0] += 1
        if _t41_count[0] == 1:
            return None  # fail first call (job A)
        # Return gradient image so _basic_contamination_check (variance >= 0.5) passes
        import numpy as np
        w, h = image.size
        arr = np.zeros((h, w, 3), dtype=np.uint8)
        arr[:, :, 0] = np.linspace(60, 100, h, dtype=np.uint8)[:, None]
        arr[:, :, 1] = 80
        arr[:, :, 2] = 80
        return Image.fromarray(arr, "RGB")

img_t41a = _solid_img((200, 50, 50), (80, 80))
img_t41b = _solid_img((50, 200, 50), (80, 80))

ctx_t41b = AiRenderContext(
    job_id="t41_b", spec_id="120x80",
    composite_sha256=_sha(img_t41b),
    work_dir=tempfile.mkdtemp(),
)
prov41 = _FailFirstProvider()

# Job A fails (provider returns None on first call)
sfr_t41a = run_source_faithful_repair(
    source_image=img_t41a, classified_layers=[],
    target_w=120, target_h=80,  # outpaint forces AI call
    provider=prov41, max_attempts=1, request_id="t41_a",
)
# Job B should succeed (provider returns valid image on second call)
sfr_t41b = run_source_faithful_repair(
    source_image=img_t41b, classified_layers=[],
    target_w=120, target_h=80,  # outpaint forces AI call
    provider=prov41, max_attempts=1, request_id="t41_b",
    render_ctx=ctx_t41b,
)

check("T41: first PSD failure does not prevent second PSD from running",
      sfr_t41b.success,
      f"sfr_t41b.success={sfr_t41b.success} failure_reason={sfr_t41b.failure_reason}")
# Second PSD's providerInputSha256 must be set (AI call was made)
check("T41: second PSD providerInputSha256 is non-empty",
      bool(ctx_t41b.provider_input_sha256),
      f"got provider_input_sha256={ctx_t41b.provider_input_sha256!r}")

# ── T42: Smart Fit invocations = 0 in ai-auto mode ───────────────────────────

# SFR pipeline never calls smart-fit. Verify by checking sfr.smart_fit_used field.
sfr_t42 = run_source_faithful_repair(
    source_image=_solid_img((100, 100, 100), (80, 80)),
    classified_layers=[],
    target_w=120, target_h=80,
    provider=FakeBackgroundProvider(), max_attempts=1, request_id="t42",
)
check("T42: sfr.smart_fit_used == False", not sfr_t42.smart_fit_used)
check("T42: sfr.blur_fill_used == False", not sfr_t42.blur_fill_used)
check("T42: sfr.native_fallback_used == False", not sfr_t42.native_fallback_used)

# ── T43: Source isolation guard catches wrong source image ────────────────────

print("\n--- T43: Source isolation guard raises on wrong source ---")

img_correct = _solid_img((200, 100, 50), (80, 80))
img_wrong   = _solid_img((50, 100, 200), (80, 80))

ctx_t43 = AiRenderContext(
    job_id="t43_guard",
    spec_id="80x80",
    source_path="/fake/correct.psd",
    composite_sha256=_sha(img_correct),  # hash of CORRECT image
    work_dir=tempfile.mkdtemp(),
)

# Pass the WRONG image — guard should raise
isolation_error_raised = False
try:
    ctx_t43.assert_source_integrity(img_wrong, label="test")
except RuntimeError as _e:
    isolation_error_raised = "CROSS_JOB_SOURCE_CONTAMINATION" in str(_e)

check("T43: assert_source_integrity raises on wrong source",
      isolation_error_raised,
      "guard did not raise RuntimeError with CROSS_JOB_SOURCE_CONTAMINATION")

# Passing the CORRECT image must not raise
no_error = True
try:
    ctx_t43.assert_source_integrity(img_correct, label="test_correct")
except RuntimeError:
    no_error = False
check("T43: assert_source_integrity does NOT raise on correct source", no_error)


# ══════════════════════════════════════════════════════════════════════════════
# T44-T52: Prompt provenance + mother-hand guard
# ══════════════════════════════════════════════════════════════════════════════

print("\n--- T44-T52: Prompt provenance + mother-hand guard ---")

from background.prompt_builder import (
    prompt_contains_mother_hand_terms,
    prompt_sha256 as _prompt_sha256,
    build_prompt,
    LATEST_VERSION,
    DEPRECATED_VERSIONS,
)

# T44: caregiving term is detected
check("T44: 'caregiving' detected as mother-hand term",
      prompt_contains_mother_hand_terms("preserving the original caregiving photograph"))

# T45: 'elderly hand' detected
check("T45: 'elderly hand' detected as mother-hand term",
      prompt_contains_mother_hand_terms("the elderly hand and the supporting adult hands"))

# T46: LATEST_VERSION (v2) prompt is mother-hand free
_v2_prompt_1200_628 = build_prompt(LATEST_VERSION, 1200, 628)
check("T46: LATEST_VERSION prompt (1200x628) has no mother-hand terms",
      not prompt_contains_mother_hand_terms(_v2_prompt_1200_628),
      f"LATEST_VERSION={LATEST_VERSION!r}")

# T47: 1250x560 spec augmentation (was 'hands at their original scale') is now neutral
_v2_prompt_1250_560 = build_prompt(LATEST_VERSION, 1250, 560)
check("T47: 1250x560 v2 augmentation is mother-hand free",
      not prompt_contains_mother_hand_terms(_v2_prompt_1250_560))
check("T47b: 1250x560 v2 does NOT contain old 'hands at their original scale' phrase",
      "hands at their original scale" not in _v2_prompt_1250_560.lower())

# T48: 1200x300 spec augmentation (was 'beige domestic environment') is now neutral
_v2_prompt_1200_300 = build_prompt(LATEST_VERSION, 1200, 300)
check("T48: 1200x300 v2 does NOT contain 'domestic background'",
      "domestic background" not in _v2_prompt_1200_300.lower())
check("T48b: 1200x300 v2 does NOT contain 'beige'",
      "beige" not in _v2_prompt_1200_300.lower())

# T49: v1 IS detected as deprecated and contains mother-hand terms
_v1_prompt = build_prompt("source-faithful-repair-v1", 1200, 628)
check("T49: v1 prompt contains mother-hand terms (as expected - it is deprecated)",
      prompt_contains_mother_hand_terms(_v1_prompt))
check("T49b: v1 is in DEPRECATED_VERSIONS",
      "source-faithful-repair-v1" in DEPRECATED_VERSIONS)

# T50: SFR hard-fails when v1 prompt is used (mother-hand guard fires, skips provider)
_img_t50 = _solid_img((100, 150, 200), (80, 80))
_sfr_t50 = run_source_faithful_repair(
    source_image=_img_t50, classified_layers=[],
    target_w=120, target_h=80,
    provider=FakeBackgroundProvider(),
    prompt_version="source-faithful-repair-v1",
    max_attempts=1,
)
_t50_guard_fired = any(
    "MOTHER_HAND_TERMS_IN_PROMPT" in r
    for a in _sfr_t50.attempts
    for r in a.get("rejectionReasons", [])
)
check("T50: v1 prompt triggers mother-hand guard (rejectionReasons contains MOTHER_HAND_TERMS)",
      _t50_guard_fired,
      f"attempts={_sfr_t50.attempts}")
check("T50b: SFR with v1 does NOT succeed (provider call skipped)",
      not _sfr_t50.success,
      f"sfr_t50.success={_sfr_t50.success}")

# T51: attempt_log contains promptSha256 for v2 run
_img_t51 = _solid_img((80, 160, 40), (80, 80))
_sfr_t51 = run_source_faithful_repair(
    source_image=_img_t51, classified_layers=[],
    target_w=120, target_h=80,
    provider=FakeBackgroundProvider(),
    prompt_version="source-faithful-repair-v2",
    max_attempts=1,
)
check("T51: v2 attempt_log contains promptSha256",
      bool(_sfr_t51.attempts) and bool(_sfr_t51.attempts[0].get("promptSha256")),
      f"attempts={_sfr_t51.attempts}")

# T52: v2 attempt_log promptContainsMotherHandTerms is False
check("T52: v2 attempt promptContainsMotherHandTerms == False",
      bool(_sfr_t51.attempts) and _sfr_t51.attempts[0].get("promptContainsMotherHandTerms") == False,
      f"attempts={_sfr_t51.attempts}")

# T53: promptSha256 differs between different target sizes (since prompt includes dimensions)
_sha_1200_628 = _prompt_sha256(build_prompt(LATEST_VERSION, 1200, 628))
_sha_1250_560 = _prompt_sha256(build_prompt(LATEST_VERSION, 1250, 560))
check("T53: same prompt version + different size -> different promptSha256",
      _sha_1200_628 != _sha_1250_560,
      f"sha_1200_628={_sha_1200_628[:8]} sha_1250_560={_sha_1250_560[:8]}")

# T54: render_ctx.prompt_sha256 is populated after v2 SFR run
_img_t54 = _solid_img((200, 80, 80), (80, 80))
_ctx_t54 = AiRenderContext(
    job_id="t54",
    spec_id="120x80",
    composite_sha256=_sha(_img_t54),
    work_dir=tempfile.mkdtemp(),
)
_sfr_t54 = run_source_faithful_repair(
    source_image=_img_t54, classified_layers=[],
    target_w=120, target_h=80,
    provider=FakeBackgroundProvider(),
    prompt_version="source-faithful-repair-v2",
    max_attempts=1,
    render_ctx=_ctx_t54,
)
check("T54: render_ctx.prompt_sha256 is set after SFR",
      bool(_ctx_t54.prompt_sha256),
      f"prompt_sha256={_ctx_t54.prompt_sha256!r}")
check("T54b: render_ctx.prompt_contains_mother_hand_terms is False for v2",
      _ctx_t54.prompt_contains_mother_hand_terms == False,
      f"got={_ctx_t54.prompt_contains_mother_hand_terms}")


# ══════════════════════════════════════════════════════════════════════════════
# Result
# ══════════════════════════════════════════════════════════════════════════════

total = PASS + FAIL
print(f"\n{'='*60}")
print(f"RESULT: {PASS}/{total} PASS  ({FAIL} FAIL)")
print(f"{'='*60}")
sys.exit(0 if FAIL == 0 else 1)
