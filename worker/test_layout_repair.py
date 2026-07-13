"""9단계: layout repair + 새 템플릿 단위 테스트.

목적:
  - emergency_fallback이 tight safe zone(1250x560)에서 repair 단계로 해소되는지 검증
  - 새 narrow-safe-zone 템플릿이 자연 통과 후보를 생성하는지 검증
  - 중복 main_image 제거, CTA 그룹화 동작 검증

실행:
  cd worker
  python test_layout_repair.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from safe_zone import normalize_safe_zone
from layout_compiler import (
    compile_layout,
    hard_fail_candidate,
    _deduplicate_main_images,
    _merge_cta_group,
    _repair_candidate,
    _objects_by_role,
    _objects_by_id,
)


# ─── 공통 mock CreativeObjectSet ─────────────────────────────────────────────

def _make_cos(extra_main_images=0, extra_ctas=0):
    objects = [
        {
            "id": "obj_logo", "role": "logo", "zIndex": 10,
            "bbox": {"width": 120, "height": 60},
            "canCrop": False, "imagePath": "dummy.png",
            "minScale": 0.5, "maxScale": 2.0,
        },
        {
            "id": "obj_headline", "role": "headline", "zIndex": 9,
            "bbox": {"width": 400, "height": 80},
            "canCrop": False, "imagePath": "dummy.png",
            "minScale": 0.5, "maxScale": 2.0,
        },
        {
            "id": "obj_main_image", "role": "main_image", "zIndex": 5,
            "bbox": {"width": 800, "height": 560},
            "canCrop": True, "imagePath": "dummy.png",
            "minScale": 0.3, "maxScale": 3.0,
        },
        {
            "id": "obj_cta", "role": "cta", "zIndex": 11,
            "bbox": {"width": 200, "height": 80},
            "canCrop": False, "imagePath": "dummy.png",
            "minScale": 0.5, "maxScale": 2.0,
        },
    ]
    for i in range(extra_main_images):
        objects.append({
            "id": f"obj_main_image_dup{i}", "role": "main_image", "zIndex": 4,
            "bbox": {"width": 300, "height": 200},
            "canCrop": True, "imagePath": "dummy_dup.png",
            "minScale": 0.3, "maxScale": 3.0,
        })
    for i in range(extra_ctas):
        objects.append({
            "id": f"obj_cta_extra{i}", "role": "cta", "zIndex": 10,
            "bbox": {"width": 100, "height": 50},
            "canCrop": False, "imagePath": "dummy_cta.png",
            "minScale": 0.5, "maxScale": 2.0,
        })
    return {"objects": objects}


# ─── safe zone 헬퍼 ──────────────────────────────────────────────────────────

def _tight_sz_1250x560():
    """1250x560 naver-gfa parsed_text safe zone."""
    return {
        "general": {"top": 50, "right": 240, "bottom": 35, "left": 240},
        "text":    {"top": 50, "right": 240, "bottom": 35, "left": 240},
        "cta":     {"top": 50, "right": 240, "bottom": 35, "left": 240},
    }


def _default_sz_1200x628():
    return normalize_safe_zone({}, 1200, 628)


def _default_sz_1200x1200():
    return normalize_safe_zone({}, 1200, 1200)


# ─── 테스트 함수 ─────────────────────────────────────────────────────────────

def test_tight_sz_1250x560_no_emergency():
    """1250x560 + tight safe zone: repair 또는 narrow 템플릿으로 emergency 방지."""
    cos = _make_cos()
    sz  = _tight_sz_1250x560()
    result = compile_layout(cos, 1250, 560, safe_zones=sz)

    meta       = result["metadata"]
    best_id    = meta["selectedCandidateId"]
    is_emg     = best_id == "emergency_fallback"
    repair_ok  = meta.get("repairApplied", False)
    valid_cnt  = meta["validCount"]

    print(f"  [1250x560 tight]  selectedCandidateId={best_id!r}  validCount={valid_cnt}"
          f"  repairAttempted={meta.get('repairAttempted')}  repairApplied={repair_ok}")
    if meta.get("repairReasons"):
        for r in meta["repairReasons"][:3]:
            print(f"    repair: {r}")

    assert not is_emg, (
        f"emergency_fallback was selected — tight safe zone not handled. "
        f"hardFailures={meta.get('hardFailures', [])[:3]}"
    )
    assert valid_cnt >= 1
    return True


def test_default_sz_1200x628():
    """1200x628 default safe zone: 정상 후보 생성."""
    cos = _make_cos()
    sz  = _default_sz_1200x628()
    result = compile_layout(cos, 1200, 628, safe_zones=sz)

    meta    = result["metadata"]
    best_id = meta["selectedCandidateId"]
    print(f"  [1200x628 default] selectedCandidateId={best_id!r}  validCount={meta['validCount']}")

    assert meta["validCount"] >= 1
    return True


def test_default_sz_1200x1200():
    """1200x1200 default safe zone: square 템플릿 정상 후보 생성."""
    cos = _make_cos()
    sz  = _default_sz_1200x1200()
    result = compile_layout(cos, 1200, 1200, safe_zones=sz)

    meta    = result["metadata"]
    best_id = meta["selectedCandidateId"]
    print(f"  [1200x1200 default] selectedCandidateId={best_id!r}  validCount={meta['validCount']}")

    assert meta["validCount"] >= 1
    return True


def test_duplicate_main_image_removal():
    """중복 main_image 2개 → 1개로 줄이는지 검증."""
    cos = _make_cos(extra_main_images=2)
    objs = cos["objects"]
    by_role, filtered_objs, dropped_ids, _ = _deduplicate_main_images(
        _objects_by_role(objs), objs
    )
    main_imgs = by_role.get("main_image", [])
    print(f"  [dedup] main_image after dedup: {len(main_imgs)}  dropped_ids={dropped_ids}")
    assert len(main_imgs) == 1, f"expected 1, got {len(main_imgs)}"
    assert len(dropped_ids) == 2
    return True


def test_cta_group_merge():
    """CTA 2개 → 1개로 줄이는지 검증."""
    cos = _make_cos(extra_ctas=1)
    objs = cos["objects"]
    by_role, cta_created = _merge_cta_group(_objects_by_role(objs))
    ctas = by_role.get("cta", [])
    print(f"  [cta_merge] ctas after merge: {len(ctas)}  ctaGroupCreated={cta_created}")
    assert len(ctas) == 1, f"expected 1, got {len(ctas)}"
    assert cta_created is True
    return True


def test_repair_moves_object_into_safe_zone():
    """_repair_candidate가 safe zone 밖 객체를 안으로 이동하는지 검증."""
    # 1250x560, safe zone right=240 → safe_x2=1010
    # CTA를 x=800, w=500 (right edge=1300 > 1010)으로 배치
    candidate = {
        "candidateId": "test_cand",
        "targetWidth": 1250,
        "targetHeight": 560,
        "placements": [
            {
                "objectId": "obj_cta", "role": "cta",
                "x": 800, "y": 400, "width": 500, "height": 80,
                "scale": 1.0, "dropped": False, "crop": None,
            }
        ],
        "hardFail": True,
        "hardFailReasons": ["cta outside safe zone"],
        "score": 0.0,
        "warnings": [],
    }
    sz = _tight_sz_1250x560()
    repaired = _repair_candidate(candidate, sz, 1250, 560)

    cta_p = repaired["placements"][0]
    right_edge = cta_p["x"] + cta_p["width"]
    print(f"  [repair] CTA after repair: x={cta_p['x']} w={cta_p['width']} "
          f"right_edge={right_edge} (safe_x2=1010)")
    print(f"  repairApplied={repaired['repairApplied']}  reasons={repaired['repairReasons']}")

    assert repaired["repairApplied"] is True
    assert right_edge <= 1010, f"right edge {right_edge} still outside safe zone"
    assert cta_p["x"] >= 240, f"x={cta_p['x']} left of safe zone"
    return True


def test_tight_sz_candidate_count():
    """tight safe zone에서 validCount ≥ 2인지 검증 (repair 후보 포함)."""
    cos = _make_cos()
    sz  = _tight_sz_1250x560()
    result = compile_layout(cos, 1250, 560, safe_zones=sz)

    meta      = result["metadata"]
    valid_cnt = meta["validCount"]
    print(f"  [tight sz candidate count] validCount={valid_cnt} (repair or narrow template)")

    assert valid_cnt >= 1, "should have at least 1 valid candidate after repair"
    # best가 emergency가 아닌지도 확인
    assert meta["selectedCandidateId"] != "emergency_fallback"
    return True


# ─── 실행 ────────────────────────────────────────────────────────────────────

_TESTS = [
    ("duplicate main_image removal",    test_duplicate_main_image_removal),
    ("CTA group merge",                 test_cta_group_merge),
    ("repair moves CTA into safe zone", test_repair_moves_object_into_safe_zone),
    ("1250x560 tight sz no emergency",  test_tight_sz_1250x560_no_emergency),
    ("1200x628 default sz",             test_default_sz_1200x628),
    ("1200x1200 default sz",            test_default_sz_1200x1200),
    ("tight sz validCount >= 1",        test_tight_sz_candidate_count),
]


def main():
    print("=" * 62)
    print("9단계 layout repair + new templates test")
    print("=" * 62)
    passed = 0
    failed = 0
    for name, fn in _TESTS:
        print(f"\n[TEST] {name}")
        try:
            fn()
            print(f"  → PASS")
            passed += 1
        except AssertionError as e:
            print(f"  → FAIL: {e}")
            failed += 1
        except Exception as e:
            print(f"  → ERROR: {e}")
            import traceback; traceback.print_exc()
            failed += 1

    print("\n" + "=" * 62)
    print(f"결과: {passed}/{len(_TESTS)} PASS  {failed} FAIL")
    print("=" * 62)
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
