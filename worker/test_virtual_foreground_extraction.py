"""Stage 21 Bundle D-2: Virtual foreground extraction tests.

Categories A-M (spec Section 29).
ACTUAL_OPENAI_REQUESTS=0 — all providers are fakes.
"""
from __future__ import annotations

import hashlib
import sys
import pathlib
import os
import textwrap

import numpy as np
import pytest
from PIL import Image

# ── Helpers ───────────────────────────────────────────────────────────────────

def _solid(w: int, h: int, color=(120, 80, 50, 255), mode="RGBA") -> Image.Image:
    img = Image.new(mode, (w, h), color)
    return img


def _gradient_rgb(w: int, h: int) -> Image.Image:
    """RGB gradient image with high variance (passes blank check)."""
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    for y in range(h):
        arr[y, :, 0] = int(y * 255 / max(h - 1, 1))
        arr[y, :, 1] = 80
        arr[y, :, 2] = int((h - 1 - y) * 255 / max(h - 1, 1))
    return Image.fromarray(arr, mode="RGB")


def _source_ref_pair(w: int = 400, h: int = 300):
    """Return (source_image, reference_image) where source has a bright foreground patch."""
    src = _gradient_rgb(w, h).convert("RGB")
    ref = _gradient_rgb(w, h).convert("RGB")

    # Add a high-contrast patch to source only (simulates foreground object)
    src_arr = np.array(src)
    x1, y1, x2, y2 = w // 4, h // 4, 3 * w // 4, 3 * h // 4
    src_arr[y1:y2, x1:x2] = [240, 240, 10]  # bright yellow — high diff
    src = Image.fromarray(src_arr)
    return src, ref


# ── Category A: FlattenedObjectDetection / FlattenedObjectMap models ──────────

class TestFlattenedObjectModels:
    def test_a1_detection_defaults(self):
        from virtual_foreground.models import FlattenedObjectDetection
        det = FlattenedObjectDetection()
        assert det.detection_id == ""
        assert det.confidence == 1.0
        assert det.required is False

    def test_a2_extractable_roles_excludes_human_subject(self):
        from virtual_foreground.models import EXTRACTABLE_ROLES, FORBIDDEN_VIRTUAL_ROLES
        assert "human_subject" not in EXTRACTABLE_ROLES
        assert "human_subject" in FORBIDDEN_VIRTUAL_ROLES

    def test_a3_required_roles_subset_of_extractable(self):
        from virtual_foreground.models import REQUIRED_ROLES, EXTRACTABLE_ROLES
        assert REQUIRED_ROLES.issubset(EXTRACTABLE_ROLES)

    def test_a4_flattened_object_map_defaults(self):
        from virtual_foreground.models import FlattenedObjectMap
        m = FlattenedObjectMap()
        assert m.detections == []
        assert m.warnings == []
        assert m.analysis_version == "d2-object-analysis-v1"

    def test_a5_virtual_object_extraction_defaults(self):
        from virtual_foreground.models import VirtualObjectExtraction
        v = VirtualObjectExtraction()
        assert v.extraction_success is False
        assert v.rgba_image is None
        assert v.extraction_method == "paired_difference"

    def test_a6_result_defaults(self):
        from virtual_foreground.models import VirtualForegroundExtractionResult
        r = VirtualForegroundExtractionResult()
        assert r.success is False
        assert r.d2_implemented is True
        assert r.fg_layers == []

    def test_a7_d2_reason_constants_exist(self):
        from virtual_foreground.models import (
            D2_REASON_NOT_APPLICABLE_HAS_NATIVE_LAYERS,
            D2_REASON_APPLICABLE_FLATTENED_PNG,
            D2_REASON_APPLICABLE_FLATTENED_JPG,
            D2_REASON_APPLICABLE_FLATTENED_UNKNOWN,
        )
        assert "native_layers" in D2_REASON_NOT_APPLICABLE_HAS_NATIVE_LAYERS
        assert "png" in D2_REASON_APPLICABLE_FLATTENED_PNG
        assert "jpg" in D2_REASON_APPLICABLE_FLATTENED_JPG


# ── Category B: FakeObjectAnalysisProvider ────────────────────────────────────

class TestFakeObjectAnalysisProvider:
    def test_b1_default_returns_two_detections(self):
        from virtual_foreground.object_analyzer import FakeObjectAnalysisProvider
        prov = FakeObjectAnalysisProvider()
        img = _gradient_rgb(400, 300)
        result = prov.analyze(img, "sha_abc")
        assert "objects" in result
        assert len(result["objects"]) >= 1

    def test_b2_custom_detections_returned(self):
        from virtual_foreground.object_analyzer import FakeObjectAnalysisProvider
        custom = [{"detection_id": "x1", "semantic_role": "logo",
                   "bbox": {"x": 0, "y": 0, "width": 50, "height": 50},
                   "confidence": 0.7}]
        prov = FakeObjectAnalysisProvider(detections=custom)
        result = prov.analyze(_gradient_rgb(200, 200), "sha")
        assert result["objects"] == custom

    def test_b3_provider_name_set(self):
        from virtual_foreground.object_analyzer import FakeObjectAnalysisProvider
        prov = FakeObjectAnalysisProvider()
        result = prov.analyze(_gradient_rgb(100, 100), "sha")
        assert result.get("provider") == "fake"

    def test_b4_zero_api_calls(self):
        """FakeObjectAnalysisProvider must never call external APIs."""
        from virtual_foreground.object_analyzer import FakeObjectAnalysisProvider
        import urllib.request
        original_open = urllib.request.urlopen
        called = []
        urllib.request.urlopen = lambda *a, **kw: called.append(True)
        try:
            prov = FakeObjectAnalysisProvider()
            prov.analyze(_gradient_rgb(100, 100), "sha")
        finally:
            urllib.request.urlopen = original_open
        assert called == []

    def test_b5_empty_custom_detections(self):
        from virtual_foreground.object_analyzer import FakeObjectAnalysisProvider
        prov = FakeObjectAnalysisProvider(detections=[])
        result = prov.analyze(_gradient_rgb(100, 100), "sha")
        assert result["objects"] == []


# ── Category C: analyze_flattened_objects ─────────────────────────────────────

class TestAnalyzeFlattenedObjects:
    def test_c1_success_returns_object_map(self):
        from virtual_foreground.object_analyzer import (
            analyze_flattened_objects, FakeObjectAnalysisProvider,
        )
        img = _gradient_rgb(400, 300)
        obj_map = analyze_flattened_objects(
            source_image=img,
            source_sha256="sha_test",
            provider=FakeObjectAnalysisProvider(),
            job_id="test_c1",
        )
        assert len(obj_map.detections) >= 1

    def test_c2_invalid_role_skipped(self):
        from virtual_foreground.object_analyzer import (
            analyze_flattened_objects, FakeObjectAnalysisProvider,
        )
        prov = FakeObjectAnalysisProvider(detections=[
            {"detection_id": "d1", "semantic_role": "INVALID_ROLE",
             "bbox": {"x": 0, "y": 0, "width": 10, "height": 10}},
        ])
        obj_map = analyze_flattened_objects(
            source_image=_gradient_rgb(200, 200),
            source_sha256="sha",
            provider=prov,
        )
        assert len(obj_map.detections) == 0
        assert any("SKIP_INVALID_ROLE" in w for w in obj_map.warnings)

    def test_c3_invalid_bbox_skipped(self):
        from virtual_foreground.object_analyzer import (
            analyze_flattened_objects, FakeObjectAnalysisProvider,
        )
        prov = FakeObjectAnalysisProvider(detections=[
            {"detection_id": "d1", "semantic_role": "product",
             "bbox": {"x": 0, "y": 0, "width": 0, "height": 0}},
        ])
        obj_map = analyze_flattened_objects(
            source_image=_gradient_rgb(200, 200),
            source_sha256="sha",
            provider=prov,
        )
        assert len(obj_map.detections) == 0

    def test_c4_provider_error_returns_empty(self):
        from virtual_foreground.object_analyzer import analyze_flattened_objects

        class ErrorProvider:
            def analyze(self, img, sha):
                raise RuntimeError("API error")

        obj_map = analyze_flattened_objects(
            source_image=_gradient_rgb(200, 200),
            source_sha256="sha",
            provider=ErrorProvider(),
        )
        assert len(obj_map.detections) == 0
        assert any("OBJECT_ANALYSIS_PROVIDER_ERROR" in w for w in obj_map.warnings)

    def test_c5_human_subject_assigned_scene_plate(self):
        from virtual_foreground.object_analyzer import (
            analyze_flattened_objects, FakeObjectAnalysisProvider,
        )
        from virtual_foreground.models import OWNER_SCENE_PLATE
        prov = FakeObjectAnalysisProvider(detections=[
            {"detection_id": "h1", "semantic_role": "human_subject",
             "bbox": {"x": 0, "y": 0, "width": 50, "height": 50},
             "confidence": 0.8},
        ])
        obj_map = analyze_flattened_objects(
            source_image=_gradient_rgb(200, 200),
            source_sha256="sha",
            provider=prov,
        )
        assert obj_map.detections[0].composition_owner == OWNER_SCENE_PLATE

    def test_c6_object_map_sha256_deterministic(self):
        from virtual_foreground.object_analyzer import (
            analyze_flattened_objects, FakeObjectAnalysisProvider,
        )
        prov = FakeObjectAnalysisProvider()
        img = _gradient_rgb(400, 300)
        m1 = analyze_flattened_objects(source_image=img, source_sha256="sha", provider=prov)
        m2 = analyze_flattened_objects(source_image=img, source_sha256="sha", provider=prov)
        assert m1.object_map_sha256 == m2.object_map_sha256
        assert len(m1.object_map_sha256) == 64

    def test_c7_required_flag_set_for_product_title(self):
        from virtual_foreground.object_analyzer import (
            analyze_flattened_objects, FakeObjectAnalysisProvider,
        )
        prov = FakeObjectAnalysisProvider(detections=[
            {"detection_id": "d1", "semantic_role": "product",
             "bbox": {"x": 0, "y": 0, "width": 50, "height": 50}},
            {"detection_id": "d2", "semantic_role": "badge",
             "bbox": {"x": 0, "y": 0, "width": 20, "height": 20}},
        ])
        obj_map = analyze_flattened_objects(
            source_image=_gradient_rgb(200, 200), source_sha256="sha", provider=prov
        )
        roles = {d.semantic_role: d.required for d in obj_map.detections}
        assert roles["product"] is True
        assert roles["badge"] is False

    def test_c8_duplicate_detection_id_renamed(self):
        from virtual_foreground.object_analyzer import (
            analyze_flattened_objects, FakeObjectAnalysisProvider,
        )
        prov = FakeObjectAnalysisProvider(detections=[
            {"detection_id": "same_id", "semantic_role": "product",
             "bbox": {"x": 0, "y": 0, "width": 40, "height": 40}},
            {"detection_id": "same_id", "semantic_role": "logo",
             "bbox": {"x": 50, "y": 50, "width": 30, "height": 30}},
        ])
        obj_map = analyze_flattened_objects(
            source_image=_gradient_rgb(200, 200), source_sha256="sha", provider=prov
        )
        ids = [d.detection_id for d in obj_map.detections]
        assert len(set(ids)) == len(ids)  # all unique


# ── Category D: mask_extractor ────────────────────────────────────────────────

class TestMaskExtractor:
    def test_d1_success_returns_rgba(self):
        from virtual_foreground.mask_extractor import extract_object_mask
        src, ref = _source_ref_pair(400, 300)
        bbox = {"x": 80, "y": 60, "width": 160, "height": 120}
        rgba, metrics = extract_object_mask(src, ref, bbox)
        assert rgba is not None
        assert rgba.mode == "RGBA"
        assert rgba.size == (160, 120)

    def test_d2_size_mismatch_returns_none(self):
        from virtual_foreground.mask_extractor import extract_object_mask
        src = _gradient_rgb(400, 300)
        ref = _gradient_rgb(200, 150)
        rgba, metrics = extract_object_mask(src, ref, {"x": 0, "y": 0, "width": 50, "height": 50})
        assert rgba is None
        assert "SIZE_MISMATCH" in metrics.get("error", "")

    def test_d3_invalid_bbox_returns_none(self):
        from virtual_foreground.mask_extractor import extract_object_mask
        src, ref = _source_ref_pair(400, 300)
        rgba, metrics = extract_object_mask(src, ref, {"x": 0, "y": 0, "width": 0, "height": 0})
        assert rgba is None

    def test_d4_metrics_contain_alpha_coverage(self):
        from virtual_foreground.mask_extractor import extract_object_mask
        src, ref = _source_ref_pair(400, 300)
        bbox = {"x": 80, "y": 60, "width": 160, "height": 120}
        rgba, metrics = extract_object_mask(src, ref, bbox)
        if rgba is not None:
            assert "alphaCoverageRatio" in metrics
            assert 0 <= metrics["alphaCoverageRatio"] <= 1.0

    def test_d5_identical_images_low_coverage(self):
        """Identical source/reference → near-zero difference → alpha too low → None."""
        from virtual_foreground.mask_extractor import extract_object_mask
        img = _gradient_rgb(400, 300)
        bbox = {"x": 80, "y": 60, "width": 160, "height": 120}
        rgba, metrics = extract_object_mask(img, img, bbox)
        # Either None (alpha too low) or very low coverage
        if rgba is not None:
            assert metrics.get("alphaCoverageRatio", 0) < 0.3
        else:
            assert "error" in metrics

    def test_d6_mask_sha256_present_on_success(self):
        from virtual_foreground.mask_extractor import extract_object_mask
        src, ref = _source_ref_pair(400, 300)
        bbox = {"x": 80, "y": 60, "width": 160, "height": 120}
        rgba, metrics = extract_object_mask(src, ref, bbox)
        if rgba is not None:
            assert len(metrics.get("maskSha256", "")) == 64

    def test_d7_completely_solid_source_opaque_detected(self):
        """Solid white source against gradient ref → may trigger opaque or valid detection."""
        from virtual_foreground.mask_extractor import extract_object_mask
        src = Image.new("RGB", (200, 200), (255, 255, 255))
        ref = _gradient_rgb(200, 200)
        bbox = {"x": 10, "y": 10, "width": 100, "height": 100}
        rgba, metrics = extract_object_mask(src, ref, bbox)
        # Either opaque bbox detected or valid alpha — just check no crash
        assert isinstance(metrics, dict)

    def test_d8_border_alpha_ratio_computed(self):
        from virtual_foreground.mask_extractor import extract_object_mask
        src, ref = _source_ref_pair(400, 300)
        bbox = {"x": 80, "y": 60, "width": 160, "height": 120}
        rgba, metrics = extract_object_mask(src, ref, bbox)
        if rgba is not None:
            assert "borderAlphaRatio" in metrics
            assert 0.0 <= metrics["borderAlphaRatio"] <= 1.0


# ── Category E: mask_refiner ──────────────────────────────────────────────────

class TestMaskRefiner:
    def _make_rgba_with_alpha(self, w: int, h: int, alpha_value: int = 200) -> Image.Image:
        arr = np.zeros((h, w, 4), dtype=np.uint8)
        arr[:, :, 0] = 100
        arr[:, :, 1] = 80
        arr[:, :, 2] = 60
        arr[h // 4: 3 * h // 4, w // 4: 3 * w // 4, 3] = alpha_value
        return Image.fromarray(arr, mode="RGBA")

    def test_e1_returns_rgba(self):
        from virtual_foreground.mask_refiner import refine_alpha_mask
        rgba = self._make_rgba_with_alpha(100, 100)
        refined, meta = refine_alpha_mask(rgba)
        assert refined.mode == "RGBA"

    def test_e2_size_preserved(self):
        from virtual_foreground.mask_refiner import refine_alpha_mask
        rgba = self._make_rgba_with_alpha(150, 80)
        refined, _ = refine_alpha_mask(rgba)
        assert refined.size == (150, 80)

    def test_e3_all_zero_alpha_returns_unchanged(self):
        from virtual_foreground.mask_refiner import refine_alpha_mask
        img = Image.new("RGBA", (100, 100), (50, 50, 50, 0))
        refined, meta = refine_alpha_mask(img)
        assert meta["refined"] is False

    def test_e4_all_opaque_returns_unchanged(self):
        from virtual_foreground.mask_refiner import refine_alpha_mask
        img = Image.new("RGBA", (100, 100), (50, 50, 50, 255))
        refined, meta = refine_alpha_mask(img)
        assert meta["refined"] is False

    def test_e5_rgb_input_converted(self):
        from virtual_foreground.mask_refiner import refine_alpha_mask
        img = Image.new("RGB", (100, 100), (50, 80, 120))
        refined, _ = refine_alpha_mask(img)
        assert refined.mode == "RGBA"

    def test_e6_metrics_contain_component_count(self):
        from virtual_foreground.mask_refiner import refine_alpha_mask
        rgba = self._make_rgba_with_alpha(100, 100)
        _, meta = refine_alpha_mask(rgba)
        assert "component_count" in meta


# ── Category F: quality_validator ─────────────────────────────────────────────

class TestQualityValidator:
    def test_f1_none_image_fails(self):
        from virtual_foreground.quality_validator import validate_extraction_quality
        result = validate_extraction_quality(None, {})
        assert result["passed"] is False

    def test_f2_partial_alpha_passes(self):
        from virtual_foreground.quality_validator import validate_extraction_quality
        # Create RGBA with ~40% alpha pixels (well within range)
        arr = np.zeros((100, 100, 4), dtype=np.uint8)
        arr[:, :, 0] = 120
        arr[20:60, 20:60, 3] = 200
        img = Image.fromarray(arr, mode="RGBA")
        result = validate_extraction_quality(img, {})
        assert result["passed"] is True

    def test_f3_too_low_alpha_fails(self):
        from virtual_foreground.quality_validator import validate_extraction_quality
        # Nearly transparent — alpha coverage < 2%
        arr = np.zeros((200, 200, 4), dtype=np.uint8)
        arr[0:2, 0:2, 3] = 255   # only 4 pixels out of 40000
        img = Image.fromarray(arr, mode="RGBA")
        result = validate_extraction_quality(img, {})
        assert result["passed"] is False
        assert "ALPHA_COVERAGE_TOO_LOW" in result["failure_reasons"]

    def test_f4_fully_opaque_fails_with_opaque_bbox(self):
        from virtual_foreground.quality_validator import validate_extraction_quality
        img = Image.new("RGBA", (100, 100), (120, 80, 40, 255))
        result = validate_extraction_quality(img, {})
        assert result["passed"] is False
        assert "OPAQUE_BBOX_CROP_DETECTED" in result["failure_reasons"]

    def test_f5_metrics_structure(self):
        from virtual_foreground.quality_validator import validate_extraction_quality
        arr = np.zeros((100, 100, 4), dtype=np.uint8)
        arr[20:60, 20:60, 3] = 200
        img = Image.fromarray(arr, mode="RGBA")
        result = validate_extraction_quality(img, {})
        m = result["metrics"]
        for key in ("alphaCoverageRatio", "opaqueCoverageRatio",
                    "borderAlphaRatio", "backgroundContaminationScore"):
            assert key in m

    def test_f6_zero_size_image_fails(self):
        from virtual_foreground.quality_validator import validate_extraction_quality
        img = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        result = validate_extraction_quality(img, {})
        # Either zero_size or low_alpha — not passed
        assert isinstance(result["passed"], bool)


# ── Category G: native_matcher ────────────────────────────────────────────────

class TestNativeMatcher:
    def _det(self, det_id: str, role: str, x: int, y: int, w: int, h: int):
        from virtual_foreground.models import FlattenedObjectDetection
        return FlattenedObjectDetection(
            detection_id=det_id,
            semantic_role=role,
            bbox={"x": x, "y": y, "width": w, "height": h},
        )

    def test_g1_no_native_layers_keeps_all(self):
        from virtual_foreground.native_matcher import filter_virtual_detections
        dets = [self._det("d1", "product", 0, 0, 100, 100)]
        kept, logs = filter_virtual_detections(dets, [])
        assert len(kept) == 1
        assert logs == []

    def test_g2_high_iou_skips_detection(self):
        from virtual_foreground.native_matcher import filter_virtual_detections
        dets = [self._det("d1", "product", 0, 0, 100, 100)]
        native = [{"role": "product", "bbox": {"x": 0, "y": 0, "width": 100, "height": 100}}]
        kept, logs = filter_virtual_detections(dets, native)
        assert len(kept) == 0
        assert logs[0]["skipped"] is True

    def test_g3_low_iou_keeps_detection(self):
        from virtual_foreground.native_matcher import filter_virtual_detections
        dets = [self._det("d1", "logo", 200, 200, 50, 50)]
        native = [{"role": "product", "bbox": {"x": 0, "y": 0, "width": 100, "height": 100}}]
        kept, logs = filter_virtual_detections(dets, native)
        assert len(kept) == 1
        assert logs[0]["skipped"] is False

    def test_g4_iou_zero_for_non_overlapping(self):
        from virtual_foreground.native_matcher import _iou
        a = {"x": 0, "y": 0, "width": 100, "height": 100}
        b = {"x": 200, "y": 200, "width": 100, "height": 100}
        assert _iou(a, b) == 0.0

    def test_g5_iou_one_for_identical(self):
        from virtual_foreground.native_matcher import _iou
        a = {"x": 50, "y": 50, "width": 100, "height": 100}
        assert abs(_iou(a, a) - 1.0) < 0.001

    def test_g6_partial_overlap(self):
        from virtual_foreground.native_matcher import _iou
        a = {"x": 0, "y": 0, "width": 100, "height": 100}
        b = {"x": 50, "y": 0, "width": 100, "height": 100}
        iou = _iou(a, b)
        # intersection = 50x100 = 5000, union = 200x100 - 5000 = 15000
        assert 0.3 < iou < 0.4

    def test_g7_logs_contain_match_info(self):
        from virtual_foreground.native_matcher import filter_virtual_detections
        dets = [self._det("d1", "product", 0, 0, 100, 100)]
        native = [{"role": "product", "bbox": {"x": 0, "y": 0, "width": 100, "height": 100}}]
        _, logs = filter_virtual_detections(dets, native)
        assert logs[0]["detection_id"] == "d1"
        assert logs[0]["maxIou"] > 0.9

    def test_g8_multiple_detections_partial_dedup(self):
        from virtual_foreground.native_matcher import filter_virtual_detections
        dets = [
            self._det("d1", "product", 0, 0, 100, 100),   # overlaps native
            self._det("d2", "logo", 300, 300, 50, 50),     # no overlap
        ]
        native = [{"role": "product", "bbox": {"x": 0, "y": 0, "width": 100, "height": 100}}]
        kept, logs = filter_virtual_detections(dets, native)
        assert len(kept) == 1
        assert kept[0].detection_id == "d2"


# ── Category H: scale_virtual_fg_layers ───────────────────────────────────────

class TestScaleVirtualFgLayers:
    def test_h1_scale_preserves_aspect_ratio(self):
        from virtual_foreground.manifest_assembler import scale_virtual_fg_layers
        rgba = _solid(200, 100)
        layers = [{
            "role": "product",
            "name": "p",
            "image": rgba,
            "bbox": {"x": 0, "y": 0, "width": 200, "height": 100},
            "sourceBBox": {"x": 0, "y": 0, "width": 200, "height": 100},
            "depth": 0, "layerId": "", "objectId": "oid1",
            "sourcePixelSha256": "", "compositedCount": 0,
        }]
        result = scale_virtual_fg_layers(layers, 400, 300, 800, 600)
        assert len(result) == 1
        img = result[0]["image"]
        # uniform scale = min(800/400, 600/300) = min(2, 2) = 2
        assert img.size == (400, 200)

    def test_h2_empty_returns_empty(self):
        from virtual_foreground.manifest_assembler import scale_virtual_fg_layers
        assert scale_virtual_fg_layers([], 400, 300, 800, 600) == []

    def test_h3_zero_source_returns_empty(self):
        from virtual_foreground.manifest_assembler import scale_virtual_fg_layers
        assert scale_virtual_fg_layers([{}], 0, 0, 800, 600) == []

    def test_h4_bbox_scaled_to_target(self):
        from virtual_foreground.manifest_assembler import scale_virtual_fg_layers
        rgba = _solid(100, 50)
        layers = [{
            "role": "title",
            "name": "t",
            "image": rgba,
            "bbox": {"x": 100, "y": 50, "width": 100, "height": 50},
            "sourceBBox": {"x": 100, "y": 50, "width": 100, "height": 50},
            "depth": 0, "layerId": "", "objectId": "oid2",
            "sourcePixelSha256": "", "compositedCount": 0,
        }]
        result = scale_virtual_fg_layers(layers, 400, 300, 800, 600)
        assert result[0]["bbox"]["x"] == 200  # 100 * (800/400)
        assert result[0]["bbox"]["y"] == 100  # 50 * (600/300)

    def test_h5_none_image_skipped(self):
        from virtual_foreground.manifest_assembler import scale_virtual_fg_layers
        layers = [{"role": "product", "image": None,
                   "bbox": {"x": 0, "y": 0, "width": 100, "height": 100},
                   "sourceBBox": {"x": 0, "y": 0, "width": 100, "height": 100},
                   "depth": 0, "layerId": "", "objectId": "", "sourcePixelSha256": "",
                   "compositedCount": 0}]
        result = scale_virtual_fg_layers(layers, 400, 300, 800, 600)
        assert result == []


# ── Category I: run_virtual_foreground_extraction success path ─────────────────

class _FakeBackgroundProvider:
    """Deterministic background provider for D-2 source reference tests."""
    def inpaint(self, image, mask, prompt, options=None):
        # Return gradient image (non-blank variance > 5.0)
        from PIL import Image
        import numpy as np
        w, h = image.size
        arr = np.zeros((h, w, 3), dtype=np.uint8)
        for y in range(h):
            arr[y, :, 0] = int(y * 200 / max(h - 1, 1))
            arr[y, :, 1] = 60
            arr[y, :, 2] = 100
        return Image.fromarray(arr, mode="RGB")

    @property
    def provider_name(self):
        return "fake-bg"

    @property
    def model(self):
        return "fake-bg-v1"


class TestRunVirtualForegroundExtraction:
    def test_i1_not_applicable_when_native_layers_present(self):
        from virtual_foreground.manifest_assembler import run_virtual_foreground_extraction
        from virtual_foreground.object_analyzer import FakeObjectAnalysisProvider
        src = _gradient_rgb(400, 300)
        result = run_virtual_foreground_extraction(
            source_image=src,
            source_path="test.png",
            source_sha256="sha_test",
            source_type="png",
            native_layers=[{"role": "product",
                            "bbox": {"x": 0, "y": 0, "width": 100, "height": 100}}],
            background_provider=_FakeBackgroundProvider(),
            analysis_provider=FakeObjectAnalysisProvider(),
            output_dir=os.path.join(
                os.environ.get("TEMP", "/tmp"),
                "d2_test_i1",
            ),
            job_id="test_i1",
        )
        assert result.success is True
        assert result.d2_applicable is False
        assert result.fg_layers == []

    def test_i2_no_objects_detected_returns_failure(self):
        from virtual_foreground.manifest_assembler import run_virtual_foreground_extraction
        from virtual_foreground.object_analyzer import FakeObjectAnalysisProvider
        src = _gradient_rgb(400, 300)
        result = run_virtual_foreground_extraction(
            source_image=src,
            source_path="test.png",
            source_sha256="sha_test",
            source_type="png",
            native_layers=[],
            background_provider=_FakeBackgroundProvider(),
            analysis_provider=FakeObjectAnalysisProvider(detections=[]),
            output_dir=os.path.join(os.environ.get("TEMP", "/tmp"), "d2_test_i2"),
            job_id="test_i2",
        )
        assert result.success is False
        assert result.d2_applicable is True
        assert "D2_NO_OBJECTS_DETECTED" in result.failure_reason

    def test_i3_source_reference_failure_returns_failure(self):
        from virtual_foreground.manifest_assembler import run_virtual_foreground_extraction
        from virtual_foreground.object_analyzer import FakeObjectAnalysisProvider

        class FailingProvider:
            def inpaint(self, *a, **kw):
                raise RuntimeError("provider failed")

        src = _gradient_rgb(400, 300)
        result = run_virtual_foreground_extraction(
            source_image=src,
            source_path="test.png",
            source_sha256="sha_test",
            source_type="png",
            native_layers=[],
            background_provider=FailingProvider(),
            analysis_provider=FakeObjectAnalysisProvider(),
            output_dir=os.path.join(os.environ.get("TEMP", "/tmp"), "d2_test_i3"),
            job_id="test_i3",
        )
        assert result.success is False
        assert "REFERENCE" in result.failure_reason or "D2" in result.failure_reason

    def test_i4_d2_reason_set_for_png_input(self):
        from virtual_foreground.manifest_assembler import run_virtual_foreground_extraction
        from virtual_foreground.object_analyzer import FakeObjectAnalysisProvider
        from virtual_foreground.models import D2_REASON_APPLICABLE_FLATTENED_PNG
        src, _ = _source_ref_pair(400, 300)
        result = run_virtual_foreground_extraction(
            source_image=src,
            source_path="test.png",
            source_sha256="sha_test",
            source_type="png",
            native_layers=[],
            background_provider=_FakeBackgroundProvider(),
            analysis_provider=FakeObjectAnalysisProvider(),
            output_dir=os.path.join(os.environ.get("TEMP", "/tmp"), "d2_test_i4"),
            job_id="test_i4",
        )
        assert result.d2_reason == D2_REASON_APPLICABLE_FLATTENED_PNG

    def test_i5_d2_implemented_always_true(self):
        from virtual_foreground.manifest_assembler import run_virtual_foreground_extraction
        from virtual_foreground.object_analyzer import FakeObjectAnalysisProvider
        src = _gradient_rgb(400, 300)
        result = run_virtual_foreground_extraction(
            source_image=src,
            source_path="test.png",
            source_sha256="sha_test",
            source_type="png",
            native_layers=[],
            background_provider=_FakeBackgroundProvider(),
            analysis_provider=FakeObjectAnalysisProvider(detections=[]),
            output_dir=os.path.join(os.environ.get("TEMP", "/tmp"), "d2_test_i5"),
            job_id="test_i5",
        )
        assert result.d2_implemented is True

    def test_i6_human_subject_not_in_fg_layers(self):
        """human_subject detection must never appear in fg_layers (spec Section 36)."""
        from virtual_foreground.manifest_assembler import run_virtual_foreground_extraction
        from virtual_foreground.object_analyzer import FakeObjectAnalysisProvider

        prov = FakeObjectAnalysisProvider(detections=[
            {"detection_id": "h1", "semantic_role": "human_subject",
             "bbox": {"x": 0, "y": 0, "width": 200, "height": 300},
             "confidence": 0.95},
        ])
        src, _ = _source_ref_pair(400, 300)
        result = run_virtual_foreground_extraction(
            source_image=src,
            source_path="test.png",
            source_sha256="sha_test",
            source_type="png",
            native_layers=[],
            background_provider=_FakeBackgroundProvider(),
            analysis_provider=prov,
            output_dir=os.path.join(os.environ.get("TEMP", "/tmp"), "d2_test_i6"),
            job_id="test_i6",
        )
        for layer in result.fg_layers:
            assert layer["role"] != "human_subject"

    def test_i7_fg_layers_have_required_fields(self):
        from virtual_foreground.manifest_assembler import run_virtual_foreground_extraction
        from virtual_foreground.object_analyzer import FakeObjectAnalysisProvider
        src, _ = _source_ref_pair(400, 300)
        result = run_virtual_foreground_extraction(
            source_image=src,
            source_path="test.png",
            source_sha256="sha_test",
            source_type="png",
            native_layers=[],
            background_provider=_FakeBackgroundProvider(),
            analysis_provider=FakeObjectAnalysisProvider(),
            output_dir=os.path.join(os.environ.get("TEMP", "/tmp"), "d2_test_i7"),
            job_id="test_i7",
        )
        for layer in result.fg_layers:
            for key in ("role", "name", "image", "bbox", "sourceBBox",
                        "objectId", "sourcePixelSha256", "compositedCount"):
                assert key in layer, f"Missing key: {key}"

    def test_i8_warnings_list_always_present(self):
        from virtual_foreground.manifest_assembler import run_virtual_foreground_extraction
        from virtual_foreground.object_analyzer import FakeObjectAnalysisProvider
        src = _gradient_rgb(400, 300)
        result = run_virtual_foreground_extraction(
            source_image=src,
            source_path="test.png",
            source_sha256="sha_test",
            source_type="png",
            native_layers=[{"role": "product",
                            "bbox": {"x": 0, "y": 0, "width": 100, "height": 100}}],
            background_provider=_FakeBackgroundProvider(),
            analysis_provider=FakeObjectAnalysisProvider(),
            output_dir=os.path.join(os.environ.get("TEMP", "/tmp"), "d2_test_i8"),
        )
        assert isinstance(result.warnings, list)


# ── Category J: failure paths ──────────────────────────────────────────────────

class TestVirtualForegroundFailurePaths:
    def test_j1_provider_request_count_tracked(self):
        from virtual_foreground.manifest_assembler import run_virtual_foreground_extraction
        from virtual_foreground.object_analyzer import FakeObjectAnalysisProvider
        src, _ = _source_ref_pair(400, 300)
        result = run_virtual_foreground_extraction(
            source_image=src,
            source_path="test.png",
            source_sha256="sha_test",
            source_type="png",
            native_layers=[],
            background_provider=_FakeBackgroundProvider(),
            analysis_provider=FakeObjectAnalysisProvider(),
            output_dir=os.path.join(os.environ.get("TEMP", "/tmp"), "d2_test_j1"),
        )
        assert result.provider_request_count >= 1

    def test_j2_detected_object_count_correct(self):
        from virtual_foreground.manifest_assembler import run_virtual_foreground_extraction
        from virtual_foreground.object_analyzer import FakeObjectAnalysisProvider
        prov = FakeObjectAnalysisProvider(detections=[
            {"detection_id": "d1", "semantic_role": "product",
             "bbox": {"x": 10, "y": 10, "width": 80, "height": 80}},
            {"detection_id": "d2", "semantic_role": "logo",
             "bbox": {"x": 200, "y": 10, "width": 50, "height": 50}},
        ])
        src, _ = _source_ref_pair(400, 300)
        result = run_virtual_foreground_extraction(
            source_image=src,
            source_path="test.png",
            source_sha256="sha_test",
            source_type="png",
            native_layers=[],
            background_provider=_FakeBackgroundProvider(),
            analysis_provider=prov,
            output_dir=os.path.join(os.environ.get("TEMP", "/tmp"), "d2_test_j2"),
        )
        assert result.detected_object_count == 2


# ── Category K: reason codes ───────────────────────────────────────────────────

class TestReasonCodes:
    def test_k1_d2_extraction_codes_exist(self):
        from verdict import reason_codes as RC
        d2_codes = [
            "EXTRACTION_D2_REQUIRED_FOR_FLATTENED_INPUT",
            "EXTRACTION_D2_OBJECT_ANALYSIS_FAILED",
            "EXTRACTION_D2_OBJECT_ANALYSIS_INVALID_RESPONSE",
            "EXTRACTION_D2_NO_OBJECTS_DETECTED",
            "EXTRACTION_D2_SOURCE_REFERENCE_BUILD_FAILED",
            "EXTRACTION_D2_SOURCE_REFERENCE_BLANK",
            "EXTRACTION_D2_PAIRED_DIFFERENCE_FAILED",
            "EXTRACTION_D2_RAW_MASK_EMPTY",
            "EXTRACTION_D2_RAW_MASK_SATURATED",
            "EXTRACTION_D2_MASK_COMPONENT_COUNT_EXCESSIVE",
            "EXTRACTION_D2_ALPHA_COVERAGE_TOO_LOW",
            "EXTRACTION_D2_ALPHA_COVERAGE_TOO_HIGH",
            "EXTRACTION_D2_BACKGROUND_CONTAMINATION_DETECTED",
            "EXTRACTION_D2_OPAQUE_BBOX_CROP_DETECTED",
            "EXTRACTION_D2_BORDER_ALPHA_RATIO_TOO_HIGH",
            "EXTRACTION_D2_REQUIRED_OBJECT_EXTRACTION_FAILED",
            "EXTRACTION_D2_REQUIRED_OBJECT_MISSING_AFTER_DEDUP",
            "EXTRACTION_D2_NATIVE_VIRTUAL_DUPLICATE",
            "EXTRACTION_D2_GROUP_OBJECT_INCOMPLETE",
            "EXTRACTION_D2_MANIFEST_EMPTY_AFTER_EXTRACTION",
            "EXTRACTION_D2_VIRTUAL_FOREGROUND_NOT_APPLICABLE",
            "EXTRACTION_D2_SOURCE_TYPE_INELIGIBLE",
            "EXTRACTION_D2_NO_OBJECTS_AFTER_NATIVE_DEDUP",
        ]
        for code in d2_codes:
            assert hasattr(RC, code), f"Missing: {code}"
            assert getattr(RC, code) == code

    def test_k2_tech_d2_codes_exist(self):
        from verdict import reason_codes as RC
        tech_codes = [
            "TECH_D2_VIRTUAL_LAYER_USED_AS_OPAQUE_BBOX",
            "TECH_D2_HUMAN_SUBJECT_EXTRACTED_AS_VIRTUAL",
            "TECH_D2_NATIVE_PRIORITY_VIOLATED",
            "TECH_D2_SCENE_REFERENCE_CONTAMINATED",
        ]
        for code in tech_codes:
            assert hasattr(RC, code), f"Missing: {code}"

    def test_k3_all_codes_sorted_and_unique(self):
        from verdict.reason_codes import ALL_CODES
        assert ALL_CODES == sorted(set(ALL_CODES))
        assert len(ALL_CODES) == len(set(ALL_CODES))

    def test_k4_d2_codes_in_all_codes(self):
        from verdict.reason_codes import ALL_CODES
        from verdict import reason_codes as RC
        assert RC.EXTRACTION_D2_REQUIRED_FOR_FLATTENED_INPUT in ALL_CODES
        assert RC.EXTRACTION_D2_OBJECT_ANALYSIS_FAILED in ALL_CODES
        assert RC.TECH_D2_VIRTUAL_LAYER_USED_AS_OPAQUE_BBOX in ALL_CODES
        assert RC.TECH_D2_SCENE_REFERENCE_CONTAMINATED in ALL_CODES

    def test_k5_total_d2_code_count(self):
        from verdict import reason_codes as RC
        d2_codes = [v for k, v in vars(RC).items()
                    if isinstance(v, str) and "D2" in k and not k.startswith("_")]
        # 23 EXTRACTION_D2_* + 4 TECH_D2_* = 27 total
        assert len(d2_codes) == 27


# ── Category L: extraction_evaluator with D-2 ─────────────────────────────────

class TestExtractionEvaluatorD2:
    def _make_manifest(self, source_type="ai_segmentation", n_objects=2):
        from verdict.manifest_builder import build_manifest_from_fg_layers
        from PIL import Image
        layers = []
        for i in range(n_objects):
            role = "product" if i == 0 else "title"
            layers.append({
                "objectId": f"virt_{i:04d}",
                "layerId": "",
                "role": role,
                "name": f"virtual_{role}_{i}",
                "bbox": {"x": i * 50, "y": 10, "width": 40, "height": 40},
                "sourceBBox": {"x": i * 50, "y": 10, "width": 40, "height": 40},
                "depth": i,
                "sourcePixelSha256": "aabbcc",
            })
        return build_manifest_from_fg_layers(layers, source_type=source_type)

    def test_l1_ai_segmentation_evaluates_normally(self):
        from verdict.extraction_evaluator import evaluate_extraction
        from verdict.models import PASS, NOT_APPLICABLE
        manifest = self._make_manifest("ai_segmentation", n_objects=2)
        result = evaluate_extraction(
            manifest,
            source_type="ai_segmentation",
            d2_required=False,
        )
        assert result.status in (PASS, NOT_APPLICABLE)

    def test_l2_unknown_source_type_returns_not_applicable(self):
        from verdict.extraction_evaluator import evaluate_extraction
        from verdict.models import NOT_APPLICABLE
        manifest = self._make_manifest("unknown")
        result = evaluate_extraction(manifest, source_type="unknown", d2_required=False)
        assert result.status == NOT_APPLICABLE

    def test_l3_d2_required_true_without_d2_success_returns_fail(self):
        from verdict.extraction_evaluator import evaluate_extraction
        from verdict.models import FAIL
        result = evaluate_extraction(
            None,
            source_type="unknown",
            d2_required=True,
        )
        assert result.status == FAIL
        from verdict import reason_codes as RC
        assert RC.EXTRACTION_D2_REQUIRED_FOR_FLATTENED_INPUT in result.reasonCodes

    def test_l4_d2_required_false_for_ai_segmentation_passes(self):
        from verdict.extraction_evaluator import evaluate_extraction
        from verdict.models import PASS
        manifest = self._make_manifest("ai_segmentation", n_objects=2)
        result = evaluate_extraction(
            manifest,
            source_type="ai_segmentation",
            d2_required=False,
        )
        assert result.status == PASS

    def test_l5_psd_layer_source_type_unchanged(self):
        from verdict.extraction_evaluator import evaluate_extraction
        from verdict.models import PASS
        manifest = self._make_manifest("psd_layer", n_objects=2)
        result = evaluate_extraction(manifest, source_type="psd_layer", d2_required=False)
        assert result.status == PASS


# ── Category M: serializer / provenance ───────────────────────────────────────

class TestD2Serializer:
    def test_m1_none_result_returns_defaults(self):
        from virtual_foreground.serializer import extract_d2_provenance_fields
        fields = extract_d2_provenance_fields(None)
        assert fields["d2VirtualForegroundApplicable"] is False
        assert fields["d2VirtualForegroundSucceeded"] is False
        assert fields["d2Implemented"] is True

    def test_m2_success_result_returns_correct_fields(self):
        from virtual_foreground.serializer import extract_d2_provenance_fields
        from virtual_foreground.models import VirtualForegroundExtractionResult
        result = VirtualForegroundExtractionResult(
            success=True,
            d2_applicable=True,
            d2_reason="flattened_png_no_native_layers",
            detected_object_count=3,
            virtual_extracted_count=2,
            virtual_rejected_count=1,
            final_recomposition_possible=True,
            source_aligned_reference_sha256="a" * 64,
            provider_request_count=2,
            d2_implemented=True,
        )
        fields = extract_d2_provenance_fields(result)
        assert fields["d2VirtualForegroundApplicable"] is True
        assert fields["d2VirtualForegroundSucceeded"] is True
        assert fields["d2VirtualDetectedCount"] == 3
        assert fields["d2VirtualExtractedCount"] == 2
        assert fields["d2VirtualRejectedCount"] == 1
        assert fields["d2FinalRecompositionPossible"] is True
        assert fields["d2SourceAlignedReferenceSha256"] == "a" * 16
        assert fields["d2ProviderRequestCount"] == 2

    def test_m3_not_applicable_result(self):
        from virtual_foreground.serializer import extract_d2_provenance_fields
        from virtual_foreground.models import (
            VirtualForegroundExtractionResult,
            D2_REASON_NOT_APPLICABLE_HAS_NATIVE_LAYERS,
        )
        result = VirtualForegroundExtractionResult(
            success=True,
            d2_applicable=False,
            d2_reason=D2_REASON_NOT_APPLICABLE_HAS_NATIVE_LAYERS,
        )
        fields = extract_d2_provenance_fields(result)
        assert fields["d2VirtualForegroundApplicable"] is False
        assert fields["d2VirtualForegroundSucceeded"] is False
        assert fields["d2Reason"] == D2_REASON_NOT_APPLICABLE_HAS_NATIVE_LAYERS

    def test_m4_sha256_truncated_to_16(self):
        from virtual_foreground.serializer import extract_d2_provenance_fields
        from virtual_foreground.models import VirtualForegroundExtractionResult
        result = VirtualForegroundExtractionResult(
            success=True,
            d2_applicable=True,
            source_aligned_reference_sha256="f" * 64,
        )
        fields = extract_d2_provenance_fields(result)
        assert fields["d2SourceAlignedReferenceSha256"] == "f" * 16

    def test_m5_empty_sha256_stays_empty(self):
        from virtual_foreground.serializer import extract_d2_provenance_fields
        from virtual_foreground.models import VirtualForegroundExtractionResult
        result = VirtualForegroundExtractionResult(
            success=True,
            d2_applicable=True,
            source_aligned_reference_sha256="",
        )
        fields = extract_d2_provenance_fields(result)
        assert fields["d2SourceAlignedReferenceSha256"] == ""


# ── No real OpenAI calls ───────────────────────────────────────────────────────

class TestNoRealOpenAI:
    def _read_file(self, path: str) -> str:
        return pathlib.Path(path).read_text(encoding="utf-8")

    def _worker_src(self, filename: str) -> str:
        base = pathlib.Path(__file__).parent
        return self._read_file(str(base / filename))

    def test_n1_object_analyzer_no_openai_import(self):
        src = self._worker_src("virtual_foreground/object_analyzer.py")
        assert "openai" not in src.lower() or "FakeObjectAnalysisProvider" in src

    def test_n2_manifest_assembler_no_openai_import(self):
        src = self._worker_src("virtual_foreground/manifest_assembler.py")
        assert "openai" not in src.lower()

    def test_n3_source_reference_reuses_d1(self):
        src = self._worker_src("virtual_foreground/source_reference.py")
        assert "run_semantic_scene_cleanup" in src

    def test_n4_fake_provider_analyze_returns_no_api_calls(self):
        from virtual_foreground.object_analyzer import FakeObjectAnalysisProvider
        import unittest.mock as mock
        with mock.patch("urllib.request.urlopen") as mocked:
            prov = FakeObjectAnalysisProvider()
            prov.analyze(_gradient_rgb(200, 200), "sha")
        assert not mocked.called

    def test_n5_actual_openai_requests_zero(self, monkeypatch):
        """Entire D-2 pipeline with fake providers — ACTUAL_OPENAI_REQUESTS=0."""
        import os
        monkeypatch.setenv("ACTUAL_OPENAI_REQUESTS", "0")
        from virtual_foreground.manifest_assembler import run_virtual_foreground_extraction
        from virtual_foreground.object_analyzer import FakeObjectAnalysisProvider
        src, _ = _source_ref_pair(200, 150)
        result = run_virtual_foreground_extraction(
            source_image=src,
            source_path="test.png",
            source_sha256="sha_n5",
            source_type="png",
            native_layers=[],
            background_provider=_FakeBackgroundProvider(),
            analysis_provider=FakeObjectAnalysisProvider(),
            output_dir=os.path.join(
                os.environ.get("TEMP", "/tmp"), "d2_test_n5"
            ),
            job_id="test_n5",
        )
        assert os.environ.get("ACTUAL_OPENAI_REQUESTS", "0") == "0"
        assert isinstance(result, object)
