"""Stage 11 품질 검증 v3: 배경 사진만 사용 + 투명 제품으로 경쟁사 레이아웃 재현.

v2 문제: 배경 = 전체 PSD composite → 제품 중복, 텍스트 겹침
v3 개선: 배경 = tai00470000632 (사람 사진만) + 우측 그라디언트 추가
         헤드라인 = type layer 직접 composite (or improved crop)
"""
import os, sys
sys.path.insert(0, r"C:\company\source\creative-resizer\worker")

from PIL import Image, ImageDraw
import psd_tools

from layout_compiler   import compile_layout
from layout_compositor import composite_layout
from safe_zone         import normalize_safe_zone

PSD_PATH   = r"C:\Users\heeil\Downloads\1200x628_야다화장품_네이버GFA.psd"
OUTPUT_DIR = r"C:\Users\heeil\AppData\Local\Temp\stage11_quality_test_v3"
ASSET_DIR  = os.path.join(OUTPUT_DIR, "assets")
os.makedirs(ASSET_DIR, exist_ok=True)

JOB_ID = "stage11_v3"

print("[1] PSD 에셋 추출...")
psd          = psd_tools.PSDImage.open(PSD_PATH)
artboard_img = psd.composite().convert("RGBA")

def find_layer(parent, name_prefix):
    for layer in (parent if hasattr(parent, "__iter__") else []):
        aname = layer.name.encode("ascii", "replace").decode("ascii")
        if aname.startswith(name_prefix) and layer.is_visible():
            return layer
        result = find_layer(layer, name_prefix)
        if result:
            return result
    return None

# ── 배경: 사람 사진 레이어만 ──────────────────────────────────────────────────
bg_path  = os.path.join(ASSET_DIR, "bg_photo.png")
bg_layer = find_layer(psd, "tai00470000632")
if bg_layer:
    bg_pil = bg_layer.composite().convert("RGBA")
    bg_pil.save(bg_path)
    r = bg_layer._record
    print(f"  bg_photo: {bg_pil.width}x{bg_pil.height}  L{r.left}T{r.top}")
else:
    artboard_img.save(bg_path)
    print("  bg_photo: fallback to artboard composite")

# ── 제품 jar (투명 배경 RGBA) ─────────────────────────────────────────────────
jar_path   = os.path.join(ASSET_DIR, "product_jar.png")
jar_layer  = find_layer(psd, "4837230645181861_172995327-Photoroom")
if jar_layer:
    jar_pil = jar_layer.composite().convert("RGBA")
    jar_pil.save(jar_path)
    r = jar_layer._record
    product_bbox = {"x": r.left, "y": r.top,
                    "width": r.right - r.left, "height": r.bottom - r.top}
    print(f"  product_jar: {jar_pil.width}x{jar_pil.height} RGBA  L{r.left}T{r.top}")
else:
    artboard_img.crop((982, 75, 1119, 340)).save(jar_path)
    product_bbox = {"x": 982, "y": 75, "width": 137, "height": 265}
    print("  product_jar: fallback to artboard crop")

# ── 헤드라인: type layer에서 composite (또는 artboard crop) ────────────────────
headline_path  = os.path.join(ASSET_DIR, "headline.png")
headline_bbox  = None
hl_found       = False

for layer in psd.descendants():
    if not layer.is_visible():
        continue
    if str(layer.kind) == "type":
        try:
            r = layer._record
            w_l = r.right - r.left
            h_l = r.bottom - r.top
            if w_l < 300 or h_l < 50:
                continue
            hl_img = layer.composite().convert("RGBA")
            if hl_img.width > 300:
                hl_img.save(headline_path)
                headline_bbox = {"x": r.left, "y": r.top, "width": w_l, "height": h_l}
                aname = layer.name.encode("ascii", "replace").decode("ascii")
                print(f"  headline (type): {hl_img.width}x{hl_img.height}  L{r.left}T{r.top}  '{aname[:40]}'")
                hl_found = True
                break
        except Exception as e:
            pass

if not hl_found:
    crop = artboard_img.crop((604, 353, 1120, 548))
    crop.save(headline_path)
    headline_bbox = {"x": 604, "y": 353, "width": 516, "height": 195}
    print("  headline: fallback to artboard crop")

print(f"  product_bbox  = {product_bbox}")
print(f"  headline_bbox = {headline_bbox}")

# ── CreativeObjectSet 구성 ────────────────────────────────────────────────────
print("[2] CreativeObjectSet 구성...")
creative_object_set = {
    "canvas":   {"width": 1200, "height": 628},
    "warnings": [],
    "objects":  [
        {
            "id":        "obj_bg",
            "role":      "background",
            "imagePath": bg_path,
            "bbox":      {"x": 38, "y": 0, "width": 526, "height": 628},
            "sourceType": "psd_layer_smartobject",
            "quality":   "high",
            "properties": {
                "canCrop": True, "canDrop": False, "mustBeReadable": False,
                "mustBeInsideSafeZone": False, "minScale": 0.5, "maxScale": 3.0,
                "keepAspectRatio": False
            },
        },
        {
            "id":        "obj_product",
            "role":      "main_image",
            "imagePath": jar_path,
            "bbox":      product_bbox,
            "sourceType": "psd_layer_smartobject",
            "quality":   "high",
            "properties": {
                "canCrop": True, "canDrop": True, "mustBeReadable": False,
                "mustBeInsideSafeZone": False, "minScale": 0.5, "maxScale": 5.0,
                "keepAspectRatio": True
            },
        },
        {
            "id":        "obj_headline",
            "role":      "headline",
            "imagePath": headline_path,
            "bbox":      headline_bbox,
            "sourceType": "ai_bbox_crop",
            "quality":   "medium",
            "properties": {
                "canCrop": False, "canDrop": False, "mustBeReadable": True,
                "mustBeInsideSafeZone": True, "minScale": 0.4, "maxScale": 2.0,
                "keepAspectRatio": True
            },
        },
    ],
}

# ── 테스트 Specs ───────────────────────────────────────────────────────────────
SPECS = [
    {
        "label": "A_1250x560_parsed_text",
        "media": "naver-gfa", "width": 1250, "height": 560,
        "slug":  "naver-gfa-native-1250x560",
        "name":  "네이버 GFA 스마트채널 1250×560",
        "safeZone":     {"top": 50, "right": 240, "bottom": 35, "left": 240},
        "textSafeZone": {"top": 44, "right": 100, "bottom": 44, "left": 100},
        "ctaSafeZone":  {"top": 44, "right": 100, "bottom": 56, "left": 100},
        "safeZoneParseStatus": "parsed_text",
    },
    {
        "label": "B_1200x628_default",
        "media": "naver-gfa", "width": 1200, "height": 628,
        "slug":  "naver-gfa-native-1200x628",
        "name":  "네이버 GFA 피드 1200×628",
    },
    {
        "label": "C_1200x1200_default",
        "media": "naver-gfa", "width": 1200, "height": 1200,
        "slug":  "naver-gfa-square-1200x1200",
        "name":  "네이버 GFA 피드 1200×1200",
    },
]

# ── 레이아웃 + 합성 ───────────────────────────────────────────────────────────
print("[3] 레이아웃 + 합성...")
bg_pil_src = Image.open(bg_path).convert("RGBA")

for spec in SPECS:
    label = spec["label"]
    w     = spec["width"]
    h     = spec["height"]
    safe_zones = normalize_safe_zone(spec, w, h)

    print(f"\n  === {label} ({w}x{h}) ===")

    # 배경: 인물 사진을 cover-scale 후 우측에 그라디언트 추가
    scale    = max(w / bg_pil_src.width, h / bg_pil_src.height)
    scaled_w = max(w, int(bg_pil_src.width  * scale))
    scaled_h = max(h, int(bg_pil_src.height * scale))
    bg_scaled = bg_pil_src.resize((scaled_w, scaled_h), Image.LANCZOS)
    left = 0
    top_c = (scaled_h - h) // 2
    bg_cropped = bg_scaled.crop((left, top_c, left + w, top_c + h)).convert("RGBA")

    # 우측 절반에 점진적 어두운 오버레이 (경쟁사 스타일)
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw    = ImageDraw.Draw(overlay)
    mid_x   = w // 3
    for px in range(mid_x, w):
        a = int(200 * (px - mid_x) / (w - mid_x))
        draw.line([(px, 0), (px, h)], fill=(8, 8, 20, a))
    bg_img = Image.alpha_composite(bg_cropped, overlay)

    # 레이아웃 컴파일
    layout_result = compile_layout(creative_object_set, w, h, safe_zones)
    lmeta = layout_result.get("metadata", {})
    selected_id  = lmeta.get("selectedCandidateId", "")
    layout_score = lmeta.get("layoutScore", 0)
    valid_count  = lmeta.get("validCount", 0)
    fallback     = lmeta.get("fallbackUsed", False)

    print(f"  selected={selected_id}  score={layout_score}  valid={valid_count}  fallback={fallback}")

    top_cands = layout_result.get("topCandidates", [])
    for i, c in enumerate(top_cands[:5]):
        cid = c.get("candidateId", "")
        sc  = c.get("score", 0)
        csb = c.get("scoringBreakdown", {}) or {}
        cs  = csb.get("competitorStyleScore", "")
        adj = csb.get("competitorStyleAdj", "")
        print(f"    [{i+1}] {cid:55s}  score={sc}  cs={cs}  adj={adj}")

    # 합성
    try:
        bg_meta = {"method": "clean_bg_v3"}
        final_img, comp_meta = composite_layout(
            bg_img, bg_meta, layout_result,
            creative_object_set, w, h, OUTPUT_DIR, JOB_ID
        )
        out_path = os.path.join(OUTPUT_DIR, f"v3_{label}.png")
        final_img.save(out_path)
        print(f"  OUTPUT: {out_path}")
    except Exception as e:
        import traceback
        print(f"  [ERROR] {e}")
        traceback.print_exc()

print("\n완료.")
