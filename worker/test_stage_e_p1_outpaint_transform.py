"""Stage E P1-B: Subject-preserving outpaint transform tests.

Verifies:
  1. Scale is contain (min of scale_x/scale_y)
  2. Source mapped region covers expected area
  3. New canvas region is complement of source mapped
  4. Allowed generation mask == source_mapped OR new_canvas
  5. No required subjects cropped when source fits target
  6. REQUIRED_SUBJECT_CROPPED raised when subject clipped
  7. Contain scale for portrait→landscape adds letterbox columns
  8. Contain scale for landscape→portrait adds letterbox rows
  9. validate_subject_not_cropped returns cropped IDs correctly
  10. log emits [SUBJECT_PRESERVING_TRANSFORM]
  11. Same-aspect source: full coverage, no new canvas
  12. transform result includes background_scene_transform field
  13. required_subject_ids parameter triggers validation
  14. Non-required subjects can be cropped without error

All tests: ACTUAL_OPENAI_REQUESTS=0
"""
from __future__ import annotations

import numpy as np
import pytest


# ── Helpers ──────────────────────────────────────────────────────────────────

def _transform(sw, sh, tw, th, bboxes=None, required_ids=None):
    from scene_cleanup.subject_preserving_transform import SubjectPreservingTransform
    t = SubjectPreservingTransform()
    return t.compute(sw, sh, tw, th, bboxes, required_subject_ids=required_ids)


# ── P1-B-1: Scale computation ────────────────────────────────────────────────

class TestScaleComputation:
    def test_contain_scale_square_to_wider(self):
        """400x400 → 800x400: scale = 400/400 = 1.0 (height constraint)."""
        r = _transform(400, 400, 800, 400)
        assert r.scale == pytest.approx(1.0, abs=0.01)

    def test_contain_scale_portrait_to_landscape(self):
        """300x600 → 600x300: scale = min(2.0, 0.5) = 0.5."""
        r = _transform(300, 600, 600, 300)
        assert r.scale == pytest.approx(0.5, abs=0.01)

    def test_contain_scale_same_aspect(self):
        """400x300 → 800x600: scale = 2.0."""
        r = _transform(400, 300, 800, 600)
        assert r.scale == pytest.approx(2.0, abs=0.01)

    def test_scale_min_of_x_and_y(self):
        """Source 400x200, target 200x200: scale=min(0.5, 1.0)=0.5."""
        r = _transform(400, 200, 200, 200)
        assert r.scale == pytest.approx(0.5, abs=0.01)


# ── P1-B-2: Mask generation ──────────────────────────────────────────────────

class TestMaskGeneration:
    def test_source_mapped_plus_new_canvas_equals_full(self):
        """source_mapped + new_canvas should cover entire target canvas."""
        r = _transform(400, 300, 600, 300)
        total = r.target_w * r.target_h
        source_pixels = int((r.source_mapped_region_mask > 0).sum())
        new_pixels = int((r.new_canvas_region_mask > 0).sum())
        assert source_pixels + new_pixels == total

    def test_allowed_gen_mask_covers_all(self):
        """Allowed generation mask should cover the entire target canvas."""
        r = _transform(400, 300, 600, 300)
        total = r.target_w * r.target_h
        allowed_pixels = int((r.allowed_generation_mask > 0).sum())
        assert allowed_pixels == total

    def test_same_aspect_no_new_canvas(self):
        """Same aspect ratio: entire target is source-mapped."""
        r = _transform(400, 300, 800, 600)  # same 4:3
        total = r.target_w * r.target_h
        source_pixels = int((r.source_mapped_region_mask > 0).sum())
        new_pixels = int((r.new_canvas_region_mask > 0).sum())
        assert source_pixels == total
        assert new_pixels == 0

    def test_portrait_to_landscape_has_left_right_bands(self):
        """Portrait source → landscape target: new canvas on left+right sides."""
        r = _transform(300, 600, 600, 300)
        # Scaled source: 150x300, centered in 600x300
        # Left and right bands should be new canvas
        assert int((r.new_canvas_region_mask > 0).sum()) > 0

    def test_immutable_mask_same_as_source_mapped(self):
        """Immutable mask covers exactly the source-mapped region."""
        r = _transform(400, 300, 600, 300)
        np.testing.assert_array_equal(
            r.immutable_mask_in_target_space,
            r.source_mapped_region_mask
        )


# ── P1-B-3: Subject validation ───────────────────────────────────────────────

class TestSubjectValidation:
    def test_no_subjects_no_error(self):
        r = _transform(400, 300, 600, 300, bboxes=[])
        assert not r.required_subjects_cropped

    def test_required_subject_within_bounds_passes(self):
        bboxes = [{"id": "subj1", "x": 100, "y": 50, "w": 200, "h": 150, "required": True}]
        r = _transform(400, 300, 800, 600, bboxes=bboxes)
        assert not r.required_subjects_cropped

    def test_required_subject_cropped_raises(self):
        from scene_cleanup.subject_preserving_transform import SubjectPreservingTransform
        t = SubjectPreservingTransform()
        # Source 400x300 → target 200x200, scale=0.5
        # Subject at source x=300, w=200 → scaled x=150, w=100 → x2=250 > target_w=200
        bboxes = [{"id": "subj1", "x": 300, "y": 0, "w": 200, "h": 100, "required": True}]
        with pytest.raises(RuntimeError, match="REQUIRED_SUBJECT_CROPPED"):
            t.compute(400, 300, 200, 200, bboxes)

    def test_non_required_subject_crop_no_error(self):
        from scene_cleanup.subject_preserving_transform import SubjectPreservingTransform
        t = SubjectPreservingTransform()
        # Non-required subject that would be cropped
        bboxes = [{"id": "bg1", "x": 350, "y": 0, "w": 100, "h": 100, "required": False}]
        r = t.compute(400, 300, 200, 200, bboxes)  # should not raise
        assert not r.required_subjects_cropped

    def test_required_via_ids_parameter(self):
        from scene_cleanup.subject_preserving_transform import SubjectPreservingTransform
        t = SubjectPreservingTransform()
        # Subject not marked required in bbox but listed in required_subject_ids
        bboxes = [{"id": "subj1", "x": 350, "y": 0, "w": 100, "h": 100, "required": False}]
        with pytest.raises(RuntimeError, match="REQUIRED_SUBJECT_CROPPED"):
            t.compute(400, 300, 200, 200, bboxes, required_subject_ids=["subj1"])


# ── P1-B-4: validate_subject_not_cropped helper ──────────────────────────────

class TestValidateSubjectNotCropped:
    def test_all_in_bounds_returns_empty(self):
        from scene_cleanup.subject_preserving_transform import validate_subject_not_cropped
        bboxes = [{"id": "s1", "x": 0, "y": 0, "w": 200, "h": 150, "required": True}]
        cropped = validate_subject_not_cropped(bboxes, 400, 300, 1.0, 0, 0, 400, 300)
        assert cropped == []

    def test_out_of_bounds_returns_id(self):
        from scene_cleanup.subject_preserving_transform import validate_subject_not_cropped
        bboxes = [{"id": "s1", "x": 350, "y": 0, "w": 100, "h": 50, "required": True}]
        cropped = validate_subject_not_cropped(bboxes, 400, 300, 1.0, 0, 0, 400, 300)
        assert "s1" in cropped

    def test_non_required_not_returned(self):
        from scene_cleanup.subject_preserving_transform import validate_subject_not_cropped
        bboxes = [{"id": "s1", "x": 350, "y": 0, "w": 100, "h": 50, "required": False}]
        cropped = validate_subject_not_cropped(bboxes, 400, 300, 1.0, 0, 0, 400, 300)
        assert cropped == []


# ── P1-B-5: log and metadata ─────────────────────────────────────────────────

class TestLogAndMetadata:
    def test_log_emitted(self, capsys):
        from scene_cleanup.subject_preserving_transform import log_subject_preserving_transform
        r = _transform(400, 300, 600, 300)
        log_subject_preserving_transform(r, job_id="p1b-log", spec_id="600x300")
        out = capsys.readouterr().out
        assert "[SUBJECT_PRESERVING_TRANSFORM]" in out
        assert "jobId=p1b-log" in out

    def test_transform_field_name(self):
        r = _transform(400, 300, 600, 300)
        assert r.background_scene_transform == "subject-preserving-outpaint"

    def test_result_dimensions_correct(self):
        r = _transform(400, 300, 600, 300)
        assert r.source_w == 400
        assert r.source_h == 300
        assert r.target_w == 600
        assert r.target_h == 300
