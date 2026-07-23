"""Stage 21 Bundle C-1: Unified verdict model tests.

Categories:
  A — VerdictResult model
  B — UnifiedObjectManifest / manifest_builder
  C — technical_evaluator
  D — extraction_evaluator
  E — composition_evaluator
  F — layout_evaluator
  G — stage21_aggregator (fail-closed)
  H — aggregator edge cases (all-NOT_APPLICABLE, mixed)
  I — serializer (serialize_verdict_result, serialize_manifest, serialize_verdict_summary)
  J — extract_provenance_fields
  K — reason_codes completeness
  L — compositor placed_object_count / skipped_object_count fix
  M — ACTUAL_OPENAI_REQUESTS=0 guard

ACTUAL_OPENAI_REQUESTS = 0
"""
from __future__ import annotations

import sys, os

# Ensure worker/ is on path
_WORKER = os.path.dirname(os.path.abspath(__file__))
if _WORKER not in sys.path:
    sys.path.insert(0, _WORKER)

# ─────────────────────────────────────────────────────────────────────────────
# Category A — VerdictResult model
# ─────────────────────────────────────────────────────────────────────────────

from verdict.models import (
    VerdictResult, UnifiedObject, UnifiedObjectManifest, Stage21VerdictSummary,
    PASS, FAIL, NOT_TESTED, NOT_APPLICABLE,
    VALID_STATUSES, VALID_SOURCE_TYPES, VALID_COMPOSITION_OWNERS,
    SOURCE_TYPE_PSD_LAYER, SOURCE_TYPE_RASTER_CROP, SOURCE_TYPE_UNKNOWN,
    OWNER_FOREGROUND_REFLOW, OWNER_BACKGROUND,
    VERDICT_VERSION,
)


def test_a1_verdict_result_defaults():
    vr = VerdictResult()
    assert vr.status == NOT_TESTED
    assert vr.required is True
    assert vr.reasonCodes == []
    assert vr.version == VERDICT_VERSION


def test_a2_verdict_result_pass():
    vr = VerdictResult(name="technicalVerdict", status=PASS, required=True)
    assert vr.status == PASS
    assert vr.name == "technicalVerdict"


def test_a3_verdict_result_fail():
    vr = VerdictResult(status=FAIL, reasonCodes=["TECH_OUTPUT_MISSING"])
    assert vr.status == FAIL
    assert "TECH_OUTPUT_MISSING" in vr.reasonCodes


def test_a4_valid_statuses_contains_all():
    assert PASS in VALID_STATUSES
    assert FAIL in VALID_STATUSES
    assert NOT_TESTED in VALID_STATUSES
    assert NOT_APPLICABLE in VALID_STATUSES


def test_a5_source_type_enum():
    assert SOURCE_TYPE_PSD_LAYER in VALID_SOURCE_TYPES
    assert SOURCE_TYPE_RASTER_CROP in VALID_SOURCE_TYPES
    assert SOURCE_TYPE_UNKNOWN in VALID_SOURCE_TYPES


def test_a6_composition_owner_enum():
    assert OWNER_FOREGROUND_REFLOW in VALID_COMPOSITION_OWNERS
    assert OWNER_BACKGROUND in VALID_COMPOSITION_OWNERS


def test_a7_visual_verdict_defaults_not_required():
    vs = Stage21VerdictSummary()
    assert vs.visualVerdict.required is False
    assert vs.visualVerdict.status == NOT_TESTED


def test_a8_stage21_verdict_summary_required_list():
    vs = Stage21VerdictSummary()
    assert vs.technicalVerdict.name == "technicalVerdict"
    assert vs.extractionVerdict.name == "extractionVerdict"
    assert vs.compositionVerdict.name == "compositionVerdict"
    assert vs.layoutVerdict.name == "layoutVerdict"
    assert vs.visualVerdict.name == "visualVerdict"


# ─────────────────────────────────────────────────────────────────────────────
# Category B — manifest_builder
# ─────────────────────────────────────────────────────────────────────────────

from verdict.manifest_builder import build_manifest_from_fg_layers, _compute_sha256


def _make_layer(oid, role="product", w=300, h=200, x=0, y=0):
    return {
        "objectId": oid,
        "layerId": oid,
        "role": role,
        "name": f"layer_{oid}",
        "bbox": {"x": x, "y": y, "width": w, "height": h},
        "sourceBBox": {"x": 0, "y": 0, "width": w, "height": h},
        "sourcePixelSha256": "",
        "depth": 0,
    }


def test_b1_empty_layers():
    m = build_manifest_from_fg_layers([])
    assert m.inputObjectCount == 0
    assert m.uniqueObjectCount == 0
    assert m.manifestSha256 != ""


def test_b2_single_layer():
    m = build_manifest_from_fg_layers([_make_layer("obj1", "product")])
    assert m.inputObjectCount == 1
    assert m.uniqueObjectCount == 1
    assert m.requiredObjectCount == 1
    assert m.objects[0].required is True


def test_b3_non_required_role():
    m = build_manifest_from_fg_layers([_make_layer("obj1", "logo")])
    assert m.objects[0].required is False


def test_b4_duplicate_objectid():
    layers = [_make_layer("dup", "product"), _make_layer("dup", "title")]
    m = build_manifest_from_fg_layers(layers)
    assert m.inputObjectCount == 2
    assert m.uniqueObjectCount == 1
    assert "dup" in m.duplicateObjectIds


def test_b5_manifest_sha256_deterministic():
    layers = [_make_layer("a"), _make_layer("b", "logo")]
    m1 = build_manifest_from_fg_layers(layers)
    m2 = build_manifest_from_fg_layers(layers)
    assert m1.manifestSha256 == m2.manifestSha256
    assert len(m1.manifestSha256) == 64


def test_b6_manifest_sha256_order_independent():
    layers_ab = [_make_layer("a"), _make_layer("b", "logo")]
    layers_ba = [_make_layer("b", "logo"), _make_layer("a")]
    m1 = build_manifest_from_fg_layers(layers_ab)
    m2 = build_manifest_from_fg_layers(layers_ba)
    assert m1.manifestSha256 == m2.manifestSha256


def test_b7_invalid_bbox_recorded():
    layer = _make_layer("bad", w=0, h=0)
    m = build_manifest_from_fg_layers([layer])
    assert "bad" in m.invalidObjectIds


def test_b8_aspect_ratio_computed():
    m = build_manifest_from_fg_layers([_make_layer("x", w=400, h=200)])
    assert m.objects[0].aspectRatio == 2.0


def test_b9_source_type_passed_through():
    m = build_manifest_from_fg_layers([_make_layer("x")], source_type="raster_crop")
    assert m.sourceType == "raster_crop"
    assert m.objects[0].sourceType == "raster_crop"


def test_b10_auto_objectid_for_missing():
    layer = _make_layer("", "product")
    layer["objectId"] = ""
    m = build_manifest_from_fg_layers([layer])
    assert m.uniqueObjectCount == 1
    assert m.objects[0].objectId.startswith("_auto_")


# ─────────────────────────────────────────────────────────────────────────────
# Category C — technical_evaluator
# ─────────────────────────────────────────────────────────────────────────────

from verdict.technical_evaluator import evaluate_technical
from verdict import reason_codes as RC


def _tech_pass(**kwargs):
    defaults = dict(
        output_path="/tmp/out.jpg",
        output_size=(1250, 560),
        file_size=100000,
        target_w=1250,
        target_h=560,
        ai_provider="stability-ai",
        fail_closed=True,
        exception_occurred=False,
        blurFillUsed=False,
        forcedSmartFit=False,
    )
    defaults.update(kwargs)
    return evaluate_technical(**defaults)


def test_c1_technical_pass():
    vr = _tech_pass()
    assert vr.status == PASS
    assert vr.reasonCodes == []


def test_c2_no_output_path():
    vr = _tech_pass(output_path=None)
    assert vr.status == FAIL
    assert RC.TECH_OUTPUT_MISSING in vr.reasonCodes


def test_c3_file_empty():
    vr = _tech_pass(file_size=0)
    assert vr.status == FAIL
    assert RC.TECH_OUTPUT_FILE_EMPTY in vr.reasonCodes


def test_c4_size_mismatch():
    vr = _tech_pass(output_size=(1000, 500))
    assert vr.status == FAIL
    assert RC.TECH_OUTPUT_SIZE_INVALID in vr.reasonCodes


def test_c5_blur_fill_used():
    vr = _tech_pass(blurFillUsed=True)
    assert vr.status == FAIL
    assert RC.TECH_BLUR_FILL_FALLBACK in vr.reasonCodes


def test_c6_smart_fit_used():
    vr = _tech_pass(forcedSmartFit=True)
    assert vr.status == FAIL
    assert RC.TECH_SMART_FIT_FALLBACK in vr.reasonCodes


def test_c7_forbidden_provider_empty():
    vr = _tech_pass(ai_provider="")
    assert vr.status == FAIL
    assert RC.TECH_NO_AI_PROVIDER in vr.reasonCodes


def test_c8_forbidden_provider_native():
    vr = _tech_pass(ai_provider="native")
    assert vr.status == FAIL
    assert RC.TECH_FALLBACK_USED in vr.reasonCodes


def test_c9_exception_occurred():
    vr = _tech_pass(exception_occurred=True)
    assert vr.status == FAIL
    assert RC.TECH_EXCEPTION_OCCURRED in vr.reasonCodes


def test_c10_fail_closed_violated():
    vr = _tech_pass(fail_closed=False)
    assert vr.status == FAIL
    assert RC.TECH_FAIL_CLOSED_VIOLATED in vr.reasonCodes


def test_c11_output_size_none_with_path():
    vr = _tech_pass(output_size=None)
    assert vr.status == FAIL
    assert RC.TECH_OUTPUT_DECODE_FAILED in vr.reasonCodes


def test_c12_multiple_failures():
    vr = _tech_pass(output_path=None, file_size=0, exception_occurred=True)
    assert vr.status == FAIL
    assert len(vr.reasonCodes) >= 2


# ─────────────────────────────────────────────────────────────────────────────
# Category D — extraction_evaluator
# ─────────────────────────────────────────────────────────────────────────────

from verdict.extraction_evaluator import evaluate_extraction


def _make_manifest(layers):
    return build_manifest_from_fg_layers(layers)


def test_d1_extraction_pass():
    m = _make_manifest([_make_layer("x", "product", w=300, h=200)])
    vr = evaluate_extraction(m, source_type=SOURCE_TYPE_PSD_LAYER)
    assert vr.status == PASS


def test_d2_extraction_not_applicable_raster():
    m = _make_manifest([_make_layer("x")])
    vr = evaluate_extraction(m, source_type="raster_crop")
    assert vr.status == NOT_APPLICABLE


def test_d3_extraction_not_applicable_unknown():
    vr = evaluate_extraction(None, source_type="unknown")
    assert vr.status == NOT_APPLICABLE


def test_d4_extraction_fail_none_manifest():
    vr = evaluate_extraction(None, source_type=SOURCE_TYPE_PSD_LAYER)
    assert vr.status == FAIL
    assert RC.EXTRACTION_MANIFEST_BUILD_FAILED in vr.reasonCodes


def test_d5_extraction_fail_on_duplicates():
    layers = [_make_layer("dup", "product"), _make_layer("dup", "title")]
    m = _make_manifest(layers)
    vr = evaluate_extraction(m, source_type=SOURCE_TYPE_PSD_LAYER)
    assert vr.status == FAIL
    assert RC.EXTRACTION_DUPLICATE_OBJECT_ID in vr.reasonCodes


def test_d6_extraction_fail_invalid_bbox():
    layer = _make_layer("bad", "product", w=0, h=0)
    m = _make_manifest([layer])
    vr = evaluate_extraction(m, source_type=SOURCE_TYPE_PSD_LAYER)
    assert vr.status == FAIL
    assert RC.EXTRACTION_INVALID_BBOX in vr.reasonCodes


def test_d7_extraction_empty_layers_fail():
    m = _make_manifest([])
    vr = evaluate_extraction(m, source_type=SOURCE_TYPE_PSD_LAYER)
    assert vr.status == FAIL
    assert RC.EXTRACTION_NO_FOREGROUND_LAYERS in vr.reasonCodes


# ─────────────────────────────────────────────────────────────────────────────
# Category E — composition_evaluator
# ─────────────────────────────────────────────────────────────────────────────

from verdict.composition_evaluator import evaluate_composition
from verdict.models import SOURCE_TYPE_AI_SEGMENTATION


class _FakeCompositorResult:
    def __init__(self, placed=None, skipped=None, dup=0, dup_ids=None, layer_count=0):
        placed = placed or []
        skipped = skipped or []
        all_entries = []
        for oid, role in placed:
            all_entries.append({"objectId": oid, "role": role, "compositedCount": 1})
        for oid, role, reason in skipped:
            all_entries.append({"objectId": oid, "role": role, "skippedReason": reason})
        self.object_manifest = all_entries
        self.unique_object_count = len(placed) + len(skipped)
        self.duplicate_count = dup
        self.duplicate_object_ids = dup_ids or []
        self.layer_count = layer_count or (len(placed) + len(skipped) + dup)


def test_e1_composition_pass():
    fg = _FakeCompositorResult(
        placed=[("obj1", "product"), ("obj2", "logo")],
    )
    vr = evaluate_composition(fg, None, source_type=SOURCE_TYPE_PSD_LAYER)
    assert vr.status == PASS
    assert vr.metrics["allObjectsCompositedOnce"] is True


def test_e2_composition_not_applicable_non_psd():
    fg = _FakeCompositorResult(placed=[("obj1", "product")])
    vr = evaluate_composition(fg, None, source_type="raster_crop")
    assert vr.status == NOT_APPLICABLE


def test_e3_composition_not_applicable_no_layers():
    vr = evaluate_composition(None, None, source_type=SOURCE_TYPE_PSD_LAYER)
    assert vr.status == NOT_APPLICABLE


def test_e4_composition_fail_duplicate():
    fg = _FakeCompositorResult(
        placed=[("obj1", "product")], dup=1, dup_ids=["obj0"]
    )
    vr = evaluate_composition(fg, None, source_type=SOURCE_TYPE_PSD_LAYER)
    assert vr.status == FAIL
    assert RC.COMPOSITION_DUPLICATE_OBJECT in vr.reasonCodes


def test_e5_composition_fail_required_skipped():
    fg = _FakeCompositorResult(
        placed=[("obj1", "logo")],
        skipped=[("obj2", "product", "no_image")],
    )
    vr = evaluate_composition(fg, None, source_type=SOURCE_TYPE_PSD_LAYER)
    assert vr.status == FAIL
    assert RC.COMPOSITION_REQUIRED_OBJECT_SKIPPED in vr.reasonCodes
    assert vr.metrics["allRequiredObjectsPlaced"] is False


def test_e6_composition_skipped_non_required_ok():
    fg = _FakeCompositorResult(
        placed=[("obj1", "product")],
        skipped=[("obj2", "decorative", "out_of_bounds")],
    )
    vr = evaluate_composition(fg, None, source_type=SOURCE_TYPE_PSD_LAYER)
    # Non-required skip → no COMPOSITION_REQUIRED_OBJECT_SKIPPED
    assert RC.COMPOSITION_REQUIRED_OBJECT_SKIPPED not in vr.reasonCodes
    # But allObjectsCompositedOnce is False because something was skipped
    assert vr.metrics["allObjectsCompositedOnce"] is False


def test_e7_placed_object_count_vs_role_count():
    # 3 objects all with role "product" — placedObjectCount must be 3 not 1
    fg = _FakeCompositorResult(
        placed=[("p1", "product"), ("p2", "product"), ("p3", "product")],
    )
    vr = evaluate_composition(fg, None, source_type=SOURCE_TYPE_PSD_LAYER)
    assert vr.metrics["placedObjectCount"] == 3
    assert vr.metrics["uniqueObjectCount"] == 3


def test_e8_5_objects_4_roles():
    # 5 objects in 4 roles → placedObjectCount=5
    placed = [
        ("a", "product"), ("b", "product"),
        ("c", "logo"), ("d", "title"), ("e", "cta"),
    ]
    fg = _FakeCompositorResult(placed=placed)
    vr = evaluate_composition(fg, None, source_type=SOURCE_TYPE_PSD_LAYER)
    assert vr.metrics["placedObjectCount"] == 5


def test_e9_compositor_none_with_manifest():
    m = _make_manifest([_make_layer("x", "product")])
    vr = evaluate_composition(None, m, source_type=SOURCE_TYPE_PSD_LAYER)
    assert vr.status == FAIL
    assert RC.COMPOSITION_COMPOSITOR_FAILED in vr.reasonCodes


# ─────────────────────────────────────────────────────────────────────────────
# Category F — layout_evaluator
# ─────────────────────────────────────────────────────────────────────────────

from verdict.layout_evaluator import evaluate_layout


class _FakeLayoutPlan:
    def __init__(self, success=True, candidate_id="cand-A", hard_fails=None,
                 all_required=True, sz_avail=True, sz_enforced=True,
                 sz_violations=0, clip_violations=0, overlap_violations=0):
        self.success = success
        self.selectedCandidateId = candidate_id
        self.hardFailReasons = hard_fails or []
        self.allRequiredObjectsPlaced = all_required
        self.safeZoneAvailable = sz_avail
        self.safeZoneEnforced = sz_enforced
        self.safeZoneViolationCount = sz_violations
        self.clippingViolationCount = clip_violations
        self.overlapViolationCount = overlap_violations
        self.warnings = []


def test_f1_layout_pass():
    plan = _FakeLayoutPlan()
    vr = evaluate_layout(plan, source_type=SOURCE_TYPE_PSD_LAYER)
    assert vr.status == PASS
    assert vr.metrics["selectedCandidateId"] == "cand-A"


def test_f2_layout_not_applicable_non_psd():
    vr = evaluate_layout(_FakeLayoutPlan(), source_type="raster_crop")
    assert vr.status == NOT_APPLICABLE


def test_f3_layout_not_tested_none_plan():
    vr = evaluate_layout(None, source_type=SOURCE_TYPE_PSD_LAYER)
    assert vr.status == NOT_TESTED
    assert RC.LAYOUT_ENGINE_ERROR in vr.reasonCodes


def test_f4_layout_fail_no_candidate():
    plan = _FakeLayoutPlan(candidate_id="")
    vr = evaluate_layout(plan, source_type=SOURCE_TYPE_PSD_LAYER)
    assert vr.status == FAIL
    assert RC.LAYOUT_NO_VALID_CANDIDATE in vr.reasonCodes


def test_f5_layout_fail_hard_fail():
    plan = _FakeLayoutPlan(hard_fails=["PRODUCT_CLIPPED"])
    vr = evaluate_layout(plan, source_type=SOURCE_TYPE_PSD_LAYER)
    assert vr.status == FAIL
    assert RC.LAYOUT_HARD_FAIL in vr.reasonCodes


def test_f6_layout_fail_safe_zone_violation():
    plan = _FakeLayoutPlan(sz_enforced=True, sz_violations=1)
    vr = evaluate_layout(plan, source_type=SOURCE_TYPE_PSD_LAYER)
    assert vr.status == FAIL
    assert RC.LAYOUT_SAFE_ZONE_VIOLATION in vr.reasonCodes


def test_f7_layout_fail_required_missing():
    plan = _FakeLayoutPlan(all_required=False)
    vr = evaluate_layout(plan, source_type=SOURCE_TYPE_PSD_LAYER)
    assert vr.status == FAIL
    assert RC.LAYOUT_REQUIRED_OBJECT_MISSING in vr.reasonCodes


def test_f8_layout_safe_zone_unavailable_warn_only_without_parsed():
    # safeZoneParseStatus is not parsed_text/parsed_diagram → warn only
    plan = _FakeLayoutPlan(sz_avail=False, sz_enforced=False)
    vr = evaluate_layout(plan, source_type=SOURCE_TYPE_PSD_LAYER, safe_zone_status="missing")
    assert vr.status == PASS
    assert RC.LAYOUT_SAFE_ZONE_UNAVAILABLE not in vr.reasonCodes


def test_f9_layout_fail_clipping():
    plan = _FakeLayoutPlan(clip_violations=1)
    vr = evaluate_layout(plan, source_type=SOURCE_TYPE_PSD_LAYER)
    assert vr.status == FAIL
    assert RC.LAYOUT_REQUIRED_OBJECT_CLIPPED in vr.reasonCodes


def test_f10_layout_metrics_complete():
    plan = _FakeLayoutPlan()
    vr = evaluate_layout(plan, source_type=SOURCE_TYPE_PSD_LAYER)
    assert "selectedCandidateId" in vr.metrics
    assert "safeZoneAvailable" in vr.metrics
    assert "safeZoneEnforced" in vr.metrics


# ─────────────────────────────────────────────────────────────────────────────
# Category G — stage21_aggregator (fail-closed)
# ─────────────────────────────────────────────────────────────────────────────

from verdict.stage21_aggregator import aggregate_stage21_verdict


def _vr(status, name="", required=True, codes=None):
    return VerdictResult(name=name, status=status, required=required, reasonCodes=codes or [])


def test_g1_all_pass_overall_pass():
    summary = aggregate_stage21_verdict(
        _vr(PASS, "technicalVerdict"),
        _vr(PASS, "extractionVerdict"),
        _vr(PASS, "compositionVerdict"),
        _vr(PASS, "layoutVerdict"),
        _vr(NOT_TESTED, "visualVerdict", required=False),
    )
    assert summary.overallStatus == PASS


def test_g2_one_required_fail():
    summary = aggregate_stage21_verdict(
        _vr(FAIL, "technicalVerdict", codes=["TECH_OUTPUT_MISSING"]),
        _vr(PASS, "extractionVerdict"),
        _vr(PASS, "compositionVerdict"),
        _vr(PASS, "layoutVerdict"),
        _vr(NOT_TESTED, "visualVerdict", required=False),
    )
    assert summary.overallStatus == FAIL
    assert RC.OVERALL_REQUIRED_VERDICT_FAILED in summary.overallReasonCodes
    assert "technicalVerdict" in summary.failedVerdicts


def test_g3_one_required_not_tested():
    summary = aggregate_stage21_verdict(
        _vr(PASS, "technicalVerdict"),
        _vr(NOT_TESTED, "extractionVerdict"),
        _vr(PASS, "compositionVerdict"),
        _vr(PASS, "layoutVerdict"),
        _vr(NOT_TESTED, "visualVerdict", required=False),
    )
    assert summary.overallStatus == FAIL
    assert RC.OVERALL_REQUIRED_VERDICT_NOT_TESTED in summary.overallReasonCodes
    assert "extractionVerdict" in summary.notTestedVerdicts


def test_g4_visual_not_tested_does_not_fail():
    # visual is NOT required — its NOT_TESTED should not cause overall FAIL
    summary = aggregate_stage21_verdict(
        _vr(PASS, "technicalVerdict"),
        _vr(PASS, "extractionVerdict"),
        _vr(PASS, "compositionVerdict"),
        _vr(PASS, "layoutVerdict"),
        _vr(NOT_TESTED, "visualVerdict", required=False),
    )
    assert summary.overallStatus == PASS


def test_g5_multiple_required_fail():
    summary = aggregate_stage21_verdict(
        _vr(FAIL, "technicalVerdict"),
        _vr(FAIL, "extractionVerdict"),
        _vr(PASS, "compositionVerdict"),
        _vr(PASS, "layoutVerdict"),
        _vr(NOT_TESTED, "visualVerdict", required=False),
    )
    assert summary.overallStatus == FAIL
    assert len(summary.failedVerdicts) == 2


def test_g6_required_verdicts_list():
    summary = aggregate_stage21_verdict(
        _vr(PASS, "technicalVerdict"),
        _vr(PASS, "extractionVerdict"),
        _vr(PASS, "compositionVerdict"),
        _vr(PASS, "layoutVerdict"),
        _vr(NOT_TESTED, "visualVerdict", required=False),
    )
    assert "technicalVerdict" in summary.requiredVerdicts
    assert "extractionVerdict" in summary.requiredVerdicts
    assert "compositionVerdict" in summary.requiredVerdicts
    assert "layoutVerdict" in summary.requiredVerdicts
    assert len(summary.requiredVerdicts) == 4


# ─────────────────────────────────────────────────────────────────────────────
# Category H — aggregator edge cases
# ─────────────────────────────────────────────────────────────────────────────

def test_h1_all_required_not_applicable_is_fail():
    summary = aggregate_stage21_verdict(
        _vr(NOT_APPLICABLE, "technicalVerdict"),
        _vr(NOT_APPLICABLE, "extractionVerdict"),
        _vr(NOT_APPLICABLE, "compositionVerdict"),
        _vr(NOT_APPLICABLE, "layoutVerdict"),
        _vr(NOT_TESTED, "visualVerdict", required=False),
    )
    assert summary.overallStatus == FAIL
    assert RC.OVERALL_ALL_REQUIRED_NOT_APPLICABLE in summary.overallReasonCodes


def test_h2_mixed_na_and_pass():
    # Some NOT_APPLICABLE (non-PSD path) + some PASS → overall PASS
    summary = aggregate_stage21_verdict(
        _vr(PASS, "technicalVerdict"),
        _vr(NOT_APPLICABLE, "extractionVerdict"),
        _vr(NOT_APPLICABLE, "compositionVerdict"),
        _vr(NOT_APPLICABLE, "layoutVerdict"),
        _vr(NOT_TESTED, "visualVerdict", required=False),
    )
    assert summary.overallStatus == PASS


def test_h3_fail_propagates_reason_codes():
    summary = aggregate_stage21_verdict(
        _vr(FAIL, "technicalVerdict", codes=["TECH_OUTPUT_MISSING", "TECH_FALLBACK_USED"]),
        _vr(PASS, "extractionVerdict"),
        _vr(PASS, "compositionVerdict"),
        _vr(PASS, "layoutVerdict"),
        _vr(NOT_TESTED, "visualVerdict", required=False),
    )
    assert "TECH_OUTPUT_MISSING" in summary.overallReasonCodes
    assert "TECH_FALLBACK_USED" in summary.overallReasonCodes


def test_h4_not_tested_propagates_code():
    summary = aggregate_stage21_verdict(
        _vr(NOT_TESTED, "technicalVerdict", codes=["TECH_SOME_CODE"]),
        _vr(PASS, "extractionVerdict"),
        _vr(PASS, "compositionVerdict"),
        _vr(PASS, "layoutVerdict"),
        _vr(NOT_TESTED, "visualVerdict", required=False),
    )
    assert summary.overallStatus == FAIL
    # reason codes from NOT_TESTED verdict are propagated
    assert "TECH_SOME_CODE" in summary.overallReasonCodes


# ─────────────────────────────────────────────────────────────────────────────
# Category I — serializer
# ─────────────────────────────────────────────────────────────────────────────

from verdict.serializer import (
    serialize_verdict_result, serialize_manifest, serialize_verdict_summary,
    extract_provenance_fields,
)


def test_i1_serialize_verdict_result():
    vr = VerdictResult(name="technicalVerdict", status=PASS, reasonCodes=["X", "A"])
    d = serialize_verdict_result(vr)
    assert d["name"] == "technicalVerdict"
    assert d["status"] == PASS
    assert d["reasonCodes"] == ["A", "X"]  # sorted


def test_i2_serialize_manifest():
    m = build_manifest_from_fg_layers([_make_layer("a"), _make_layer("b", "logo")])
    d = serialize_manifest(m)
    assert "sourceType" in d
    assert "manifestSha256" in d
    assert isinstance(d["objects"], list)
    # Objects are sorted by objectId
    assert d["objects"][0]["objectId"] <= d["objects"][-1]["objectId"]


def test_i3_serialize_verdict_summary_structure():
    vs = Stage21VerdictSummary(
        technicalVerdict=_vr(PASS, "technicalVerdict"),
        extractionVerdict=_vr(PASS, "extractionVerdict"),
        compositionVerdict=_vr(PASS, "compositionVerdict"),
        layoutVerdict=_vr(PASS, "layoutVerdict"),
        visualVerdict=_vr(NOT_TESTED, "visualVerdict", required=False),
        overallStatus=PASS,
    )
    d = serialize_verdict_summary(vs)
    assert d["overallStatus"] == PASS
    assert "technicalVerdict" in d
    assert d["technicalVerdict"]["status"] == PASS
    assert "visualVerdict" in d


def test_i4_serialize_reason_codes_sorted():
    vr = VerdictResult(reasonCodes=["Z", "A", "M"])
    d = serialize_verdict_result(vr)
    assert d["reasonCodes"] == ["A", "M", "Z"]


# ─────────────────────────────────────────────────────────────────────────────
# Category J — extract_provenance_fields
# ─────────────────────────────────────────────────────────────────────────────

def _make_summary_all_pass():
    comp = VerdictResult(
        name="compositionVerdict", status=PASS,
        metrics={
            "allRequiredObjectsPlaced": True,
            "noDuplicateComposition": True,
            "allObjectsCompositedOnce": True,
        }
    )
    layout = VerdictResult(
        name="layoutVerdict", status=PASS,
        metrics={
            "safeZoneAvailable": True,
            "safeZoneEnforced": True,
            "selectedCandidateId": "cand-B",
        }
    )
    return aggregate_stage21_verdict(
        _vr(PASS, "technicalVerdict"),
        _vr(PASS, "extractionVerdict"),
        comp,
        layout,
        _vr(NOT_TESTED, "visualVerdict", required=False),
    )


def test_j1_provenance_overall_verdict():
    summary = _make_summary_all_pass()
    prov = extract_provenance_fields(summary)
    assert prov["overallVerdict"] == PASS


def test_j2_provenance_verdict_strings():
    summary = _make_summary_all_pass()
    prov = extract_provenance_fields(summary)
    assert prov["technicalVerdict"] == PASS
    assert prov["extractionVerdict"] == PASS
    assert prov["compositionVerdict"] == PASS
    assert prov["layoutVerdict"] == PASS
    assert prov["visualVerdict"] == NOT_TESTED


def test_j3_provenance_composition_metrics():
    summary = _make_summary_all_pass()
    prov = extract_provenance_fields(summary)
    assert prov["allRequiredObjectsPlaced"] is True
    assert prov["noDuplicateComposition"] is True
    assert prov["allObjectsCompositedOnce"] is True


def test_j4_provenance_layout_metrics():
    summary = _make_summary_all_pass()
    prov = extract_provenance_fields(summary)
    assert prov["safeZoneAvailable"] is True
    assert prov["safeZoneEnforced"] is True
    assert prov["selectedCandidateId"] == "cand-B"


def test_j5_provenance_with_manifest():
    summary = _make_summary_all_pass()
    m = build_manifest_from_fg_layers([_make_layer("x")])
    prov = extract_provenance_fields(summary, m)
    assert "manifestSha256" in prov
    assert len(prov["manifestSha256"]) == 64
    assert "unifiedObjectManifest" in prov
    assert prov["unifiedObjectManifest"]["inputObjectCount"] == 1


def test_j6_provenance_fail_overall():
    summary = aggregate_stage21_verdict(
        _vr(FAIL, "technicalVerdict"),
        _vr(PASS, "extractionVerdict"),
        _vr(PASS, "compositionVerdict"),
        _vr(PASS, "layoutVerdict"),
        _vr(NOT_TESTED, "visualVerdict", required=False),
    )
    prov = extract_provenance_fields(summary)
    assert prov["overallVerdict"] == FAIL


def test_j7_provenance_version_present():
    summary = _make_summary_all_pass()
    prov = extract_provenance_fields(summary)
    assert "verdictVersion" in prov
    assert prov["verdictVersion"] == VERDICT_VERSION


# ─────────────────────────────────────────────────────────────────────────────
# Category K — reason_codes completeness
# ─────────────────────────────────────────────────────────────────────────────

from verdict import reason_codes as RC_MODULE


def test_k1_all_codes_list_exists():
    assert hasattr(RC_MODULE, "ALL_CODES")
    assert isinstance(RC_MODULE.ALL_CODES, list)
    assert len(RC_MODULE.ALL_CODES) > 0


def test_k2_all_codes_uppercase_snake():
    for code in RC_MODULE.ALL_CODES:
        assert code == code.upper(), f"Code {code!r} is not UPPER_SNAKE"
        assert " " not in code, f"Code {code!r} contains space"


def test_k3_all_codes_sorted():
    assert RC_MODULE.ALL_CODES == sorted(RC_MODULE.ALL_CODES)


def test_k4_required_reason_codes_present():
    required = [
        "TECH_PROVIDER_FAILED", "TECH_OUTPUT_MISSING", "TECH_FALLBACK_USED",
        "TECH_BLUR_FILL_FALLBACK", "TECH_SMART_FIT_FALLBACK",
        "EXTRACTION_MANIFEST_BUILD_FAILED", "EXTRACTION_DUPLICATE_OBJECT_ID",
        "EXTRACTION_NO_FOREGROUND_LAYERS", "EXTRACTION_INVALID_BBOX",
        "COMPOSITION_DUPLICATE_OBJECT", "COMPOSITION_REQUIRED_OBJECT_SKIPPED",
        "COMPOSITION_COMPOSITOR_FAILED",
        "LAYOUT_ENGINE_ERROR", "LAYOUT_NO_VALID_CANDIDATE", "LAYOUT_HARD_FAIL",
        "LAYOUT_SAFE_ZONE_VIOLATION", "LAYOUT_REQUIRED_OBJECT_MISSING",
        "OVERALL_REQUIRED_VERDICT_FAILED", "OVERALL_REQUIRED_VERDICT_NOT_TESTED",
        "OVERALL_ALL_REQUIRED_NOT_APPLICABLE",
    ]
    for code in required:
        assert hasattr(RC_MODULE, code), f"Missing reason code: {code}"
        assert getattr(RC_MODULE, code) == code


# ─────────────────────────────────────────────────────────────────────────────
# Category L — compositor placed_object_count / skipped_object_count fix
# ─────────────────────────────────────────────────────────────────────────────

from unittest.mock import MagicMock, patch
from foreground.compositor import ForegroundCompositeResult, composite_foreground
from PIL import Image


def _blank_image(w=100, h=100):
    return Image.new("RGBA", (w, h), (255, 0, 0, 255))


def test_l1_placed_object_count_not_role_count():
    # 3 product objects → placed_object_count must be 3, not 1
    bg = _blank_image(600, 400)
    layers = [
        {
            "objectId": f"p{i}", "role": "product",
            "image": _blank_image(50, 50),
            "bbox": {"x": i * 60, "y": 0, "width": 50, "height": 50},
            "depth": i,
        }
        for i in range(3)
    ]
    res = composite_foreground(bg, layers)
    assert res.placed_object_count == 3
    assert res.unique_object_count == 3
    assert res.skipped_object_count == 0


def test_l2_skipped_object_count():
    bg = _blank_image(600, 400)
    layers = [
        {"objectId": "good", "role": "product",
         "image": _blank_image(50, 50),
         "bbox": {"x": 0, "y": 0, "width": 50, "height": 50}, "depth": 0},
        {"objectId": "bad", "role": "logo",
         "image": None,  # no image → skip
         "bbox": {"x": 0, "y": 0, "width": 50, "height": 50}, "depth": 0},
    ]
    res = composite_foreground(bg, layers)
    assert res.placed_object_count == 1
    assert res.skipped_object_count == 1


def test_l3_all_objects_composited_once_false_when_skipped():
    bg = _blank_image(600, 400)
    layers = [
        {"objectId": "x", "role": "product",
         "image": _blank_image(50, 50),
         "bbox": {"x": 0, "y": 0, "width": 50, "height": 50}, "depth": 0},
        {"objectId": "y", "role": "logo", "image": None,
         "bbox": {"x": 0, "y": 0, "width": 50, "height": 50}, "depth": 0},
    ]
    res = composite_foreground(bg, layers)
    # Skipped object → all_objects_composited_once must be False
    assert res.all_objects_composited_once is False


def test_l4_all_objects_composited_once_true_when_all_placed():
    bg = _blank_image(600, 400)
    layers = [
        {"objectId": "a", "role": "product",
         "image": _blank_image(50, 50),
         "bbox": {"x": 0, "y": 0, "width": 50, "height": 50}, "depth": 0},
        {"objectId": "b", "role": "logo",
         "image": _blank_image(30, 30),
         "bbox": {"x": 100, "y": 0, "width": 30, "height": 30}, "depth": 0},
    ]
    res = composite_foreground(bg, layers)
    assert res.all_objects_composited_once is True
    assert res.placed_object_count == 2
    assert res.skipped_object_count == 0


def test_l5_duplicate_objectid_counted():
    bg = _blank_image(600, 400)
    layers = [
        {"objectId": "dup", "role": "product",
         "image": _blank_image(50, 50),
         "bbox": {"x": 0, "y": 0, "width": 50, "height": 50}, "depth": 0},
        {"objectId": "dup", "role": "product",
         "image": _blank_image(50, 50),
         "bbox": {"x": 60, "y": 0, "width": 50, "height": 50}, "depth": 1},
    ]
    res = composite_foreground(bg, layers)
    assert res.duplicate_count == 1
    assert res.placed_object_count == 1   # only one unique placed
    assert res.all_objects_composited_once is False


# ─────────────────────────────────────────────────────────────────────────────
# Category M — ACTUAL_OPENAI_REQUESTS = 0
# ─────────────────────────────────────────────────────────────────────────────

def test_m1_no_openai_import_in_verdict_package():
    """No verdict module should import openai."""
    import importlib
    import pkgutil
    import verdict as verdict_pkg

    for _finder, modname, _ispkg in pkgutil.walk_packages(
        verdict_pkg.__path__, prefix="verdict."
    ):
        mod = importlib.import_module(modname)
        # openai must not be imported in any verdict module
        assert "openai" not in dir(mod), f"openai found in {modname}"


def test_m2_verdict_models_no_pil():
    """verdict.models must not import PIL."""
    import verdict.models as vm
    assert not hasattr(vm, "Image"), "PIL Image found in verdict.models"


def test_m3_extraction_evaluator_no_pil():
    import verdict.extraction_evaluator as ev
    assert not hasattr(ev, "Image"), "PIL Image found in extraction_evaluator"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
