"""Stage 14 + Stage 15 단위 테스트."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from layout_compiler import (
    score_product_candidate,
    find_isolated_product_candidates,
    choose_primary_product_object,
    _deduplicate_main_images,
    _objects_by_role,
    compile_layout,
    _competitor_style_adjustment,
)
from background_builder import compute_background_naturalness_score
from safe_zone import normalize_safe_zone

PASS = "✅ PASS"
FAIL = "❌ FAIL"
results = []


def _check(name, cond):
    tag = PASS if cond else FAIL
    print(f"  [{name}] {tag}")
    results.append(cond)
    return cond


# ─── Stage 15: background naturalness score ──────────────────────────────────

def test_naturalness_scores():
    print("\n[TEST] background naturalness score")
    _check("psd_cover >=80",   compute_background_naturalness_score("psd_background_cover") >= 80)
    _check("psd_extend >=65",  compute_background_naturalness_score("psd_background_extend") >= 65)
    _check("clean_area >=60",  compute_background_naturalness_score("flattened_clean_area_cover") >= 60)
    _check("gradient <=50",    compute_background_naturalness_score("dominant_gradient") <= 50)
    _check("solid <=30",       compute_background_naturalness_score("solid_brand_color") <= 30)
    _check("emergency <=20",   compute_background_naturalness_score("emergency_neutral") <= 20)
    _check("blur penalty",     compute_background_naturalness_score("psd_background_cover", blur_used=True)
           < compute_background_naturalness_score("psd_background_cover", blur_used=False))
    return True


# ─── Stage 14: score_product_candidate ───────────────────────────────────────

def test_score_product_candidate_isolated():
    print("\n[TEST] score_product_candidate — isolated product (Photoroom)")
    obj = {
        "id": "obj_jar",
        "role": "main_image",
        "sourceType": "psd_layer_smartobject",
        "imagePath": r"C:\tmp\4837230645181861_172995327-Photoroom.png",
        "bbox": {"x": 982, "y": 75, "width": 137, "height": 265},
        "qualityRisk": None,
    }
    score = score_product_candidate(obj, 1250, 560)
    print(f"  isolated product score={score:.1f}")
    _check("isolated product score >= 60", score >= 60)
    return True


def test_score_product_candidate_ai_bbox():
    print("\n[TEST] score_product_candidate — ai_bbox_crop (low isolation)")
    obj = {
        "id": "obj_scene",
        "role": "main_image",
        "sourceType": "ai_bbox_crop",
        "imagePath": r"C:\tmp\obj_main_image_1.png",
        "bbox": {"x": 0, "y": 0, "width": 800, "height": 560},
        "qualityRisk": "high",
    }
    score = score_product_candidate(obj, 1250, 560)
    print(f"  ai_bbox_crop scene score={score:.1f}")
    _check("ai_bbox_crop wide score < 40", score < 40)
    return True


def test_score_product_candidate_wide_obj():
    print("\n[TEST] score_product_candidate — wide object (hand+product)")
    obj = {
        "id": "obj_hand",
        "role": "main_image",
        "sourceType": "psd_layer",
        "imagePath": r"C:\tmp\hand_product.png",
        "bbox": {"x": 0, "y": 0, "width": 600, "height": 300},  # wide, 60% area
        "qualityRisk": None,
    }
    score = score_product_candidate(obj, 1000, 500)
    print(f"  wide hand+product score={score:.1f}")
    _check("wide large object score < 55", score < 55)
    return True


# ─── Stage 14: choose_primary_product_object ─────────────────────────────────

def test_choose_primary_isolated_wins():
    print("\n[TEST] choose_primary_product_object — isolated beats large")
    isolated = {
        "id": "obj_jar",
        "role": "main_image",
        "sourceType": "psd_layer_smartobject",
        "imagePath": r"C:\tmp\product-Photoroom.png",
        "bbox": {"x": 982, "y": 75, "width": 137, "height": 265},
        "qualityRisk": None,
    }
    large_scene = {
        "id": "obj_scene",
        "role": "main_image",
        "sourceType": "ai_bbox_crop",
        "imagePath": r"C:\tmp\scene.png",
        "bbox": {"x": 0, "y": 0, "width": 900, "height": 560},
        "qualityRisk": "high",
    }
    chosen, meta = choose_primary_product_object([large_scene, isolated], 1250, 560)
    print(f"  chosen={chosen['id']}  score={meta['productCandidateScore']}  reason={meta['productIsolationReason']}")
    _check("isolated product chosen", chosen["id"] == "obj_jar")
    _check("isolatedProductCandidateUsed=True", meta["isolatedProductCandidateUsed"] is True)
    _check("reason=isolated_product_selected", "isolated" in meta["productIsolationReason"])
    return True


def test_deduplicate_uses_isolation():
    print("\n[TEST] _deduplicate_main_images — isolation-aware")
    isolated = {
        "id": "obj_jar",
        "role": "main_image",
        "sourceType": "psd_layer_smartobject",
        "imagePath": r"C:\tmp\product-Photoroom.png",
        "bbox": {"x": 982, "y": 75, "width": 137, "height": 265},
        "qualityRisk": None,
    }
    large = {
        "id": "obj_large",
        "role": "main_image",
        "sourceType": "ai_bbox_crop",
        "imagePath": r"C:\tmp\scene.png",
        "bbox": {"x": 0, "y": 0, "width": 900, "height": 560},
        "qualityRisk": "high",
    }
    objects = [isolated, large, {"id": "obj_hl", "role": "headline"}]
    objs_by_role = _objects_by_role(objects)
    by_role, filtered, dropped, meta = _deduplicate_main_images(
        objs_by_role, objects, 1250, 560
    )
    main_imgs = by_role.get("main_image", [])
    print(f"  main_image after dedup: {[o['id'] for o in main_imgs]}  dropped={dropped}")
    print(f"  meta: {meta}")
    _check("exactly 1 main_image remains", len(main_imgs) == 1)
    _check("isolated product kept", main_imgs[0]["id"] == "obj_jar")
    _check("large scene dropped", "obj_large" in dropped)
    _check("isolation meta present", "productCandidateScore" in meta)
    return True


# ─── Stage 15: bg_naturalness affects competitor style adj ───────────────────

def test_bg_naturalness_in_competitor_adj():
    print("\n[TEST] _competitor_style_adjustment — bg_naturalness bonus")
    candidate = {
        "placements": [
            {"role": "main_image", "x": 800, "y": 50, "width": 400, "height": 460,
             "dropped": False},
            {"role": "headline",   "x": 100, "y": 50, "width": 300, "height": 80,
             "dropped": False},
            {"role": "cta",        "x": 100, "y": 420, "width": 200, "height": 60,
             "dropped": False},
        ]
    }
    adj_no_bg   = _competitor_style_adjustment(candidate, 1250, 560, bg_naturalness=None)
    adj_high_bg = _competitor_style_adjustment(candidate, 1250, 560, bg_naturalness=85.0)
    adj_low_bg  = _competitor_style_adjustment(candidate, 1250, 560, bg_naturalness=30.0)
    print(f"  adj no_bg={adj_no_bg}  high_bg={adj_high_bg}  low_bg={adj_low_bg}")
    _check("high naturalness bonus > no_bg", adj_high_bg > adj_no_bg)
    _check("low naturalness penalty < no_bg", adj_low_bg < adj_no_bg)
    _check("high - low = 1.0", abs((adj_high_bg - adj_low_bg) - 1.0) < 0.01)
    return True


# ─── Stage 14+15 integration: compile_layout ─────────────────────────────────

def test_compile_layout_with_isolated_product_and_bg():
    print("\n[TEST] compile_layout — Stage 14+15 metadata present")
    cos = {
        "canvas": {"width": 1250, "height": 560},
        "warnings": [],
        "objects": [
            {
                "id": "obj_bg", "role": "background",
                "imagePath": None,
                "bbox": {"x": 0, "y": 0, "width": 1250, "height": 560},
                "sourceType": "psd_layer", "qualityRisk": None,
                "canCrop": True, "canDrop": False,
                "mustBeReadable": False, "mustBeInsideSafeZone": False,
                "minScale": 0.5, "maxScale": 2.0, "keepAspectRatio": False,
            },
            {
                "id": "obj_jar", "role": "main_image",
                "imagePath": r"C:\tmp\product-Photoroom.png",
                "bbox": {"x": 982, "y": 75, "width": 137, "height": 265},
                "sourceType": "psd_layer_smartobject", "qualityRisk": None,
                "canCrop": True, "canDrop": True,
                "mustBeReadable": False, "mustBeInsideSafeZone": False,
                "minScale": 0.5, "maxScale": 5.0, "keepAspectRatio": True,
            },
            {
                "id": "obj_scene", "role": "main_image",
                "imagePath": r"C:\tmp\scene.png",
                "bbox": {"x": 0, "y": 0, "width": 900, "height": 560},
                "sourceType": "ai_bbox_crop", "qualityRisk": "high",
                "canCrop": True, "canDrop": True,
                "mustBeReadable": False, "mustBeInsideSafeZone": False,
                "minScale": 0.5, "maxScale": 1.5, "keepAspectRatio": True,
            },
            {
                "id": "obj_hl", "role": "headline",
                "imagePath": r"C:\tmp\hl.png",
                "bbox": {"x": 100, "y": 50, "width": 400, "height": 80},
                "sourceType": "ai_bbox_crop", "qualityRisk": "high",
                "canCrop": False, "canDrop": False,
                "mustBeReadable": True, "mustBeInsideSafeZone": True,
                "minScale": 0.5, "maxScale": 2.0, "keepAspectRatio": True,
            },
        ],
    }
    safe_zones = normalize_safe_zone({
        "safeZone": {"top": 50, "right": 240, "bottom": 35, "left": 240},
        "textSafeZone": {"top": 44, "right": 100, "bottom": 44, "left": 100},
        "ctaSafeZone": {"top": 44, "right": 100, "bottom": 56, "left": 100},
        "safeZoneParseStatus": "parsed_text",
    }, 1250, 560)

    result = compile_layout(cos, 1250, 560, safe_zones=safe_zones, bg_naturalness=85.0)
    meta = result.get("metadata", {})

    print(f"  selectedCandidateId={meta.get('selectedCandidateId')}")
    print(f"  layoutScore={meta.get('layoutScore')}")
    print(f"  isolatedProductCandidateUsed={meta.get('isolatedProductCandidateUsed')}")
    print(f"  isolatedProductCandidateId={meta.get('isolatedProductCandidateId')}")
    print(f"  productCandidateScore={meta.get('productCandidateScore')}")
    print(f"  productIsolationReason={meta.get('productIsolationReason')}")
    print(f"  backgroundNaturalnessScore={meta.get('backgroundNaturalnessScore')}")
    print(f"  duplicateObjectsRemoved={meta.get('duplicateObjectsRemoved')}")

    _check("Stage 14 meta present", "isolatedProductCandidateUsed" in meta)
    _check("isolated product selected", meta.get("isolatedProductCandidateId") == "obj_jar")
    _check("large scene dropped", "obj_scene" in meta.get("duplicateObjectsRemoved", []))
    _check("Stage 15 meta present", meta.get("backgroundNaturalnessScore") == 85.0)
    _check("valid layout found", meta.get("validCount", 0) >= 1)
    return True


# ─── main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Stage 14 + Stage 15 unit tests")
    print("=" * 60)
    tests = [
        test_naturalness_scores,
        test_score_product_candidate_isolated,
        test_score_product_candidate_ai_bbox,
        test_score_product_candidate_wide_obj,
        test_choose_primary_isolated_wins,
        test_deduplicate_uses_isolation,
        test_bg_naturalness_in_competitor_adj,
        test_compile_layout_with_isolated_product_and_bg,
    ]
    for fn in tests:
        try:
            fn()
        except Exception as e:
            import traceback
            print(f"  ❌ ERROR: {e}")
            traceback.print_exc()
            results.append(False)

    passed = sum(results)
    total  = len(results)
    print(f"\n{'='*60}")
    print(f"결과: {passed}/{total} PASS  {total-passed} FAIL")
    print("=" * 60)


if __name__ == "__main__":
    main()
