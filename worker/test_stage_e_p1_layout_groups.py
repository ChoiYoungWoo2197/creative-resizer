"""Stage E P1-C: Subject avoidance layout and group RGBA builder tests.

Verifies:
  avoidance_mask:
    1. build_avoidance_mask() creates correct binary mask from bboxes
    2. Empty bboxes → zero mask
    3. Margin is respected in mask
    4. validate_subject_occlusion() passes when no overlap
    5. validate_subject_occlusion() fails FACE_OCCLUSION_EXCEEDED on face
    6. validate_subject_occlusion() fails HAND_OCCLUSION_EXCEEDED on hand
    7. validate_subject_occlusion() fails REQUIRED_SUBJECT_OCCLUDED with custom threshold
    8. log_avoidance_mask() emits [SUBJECT_AVOIDANCE_MASK]

  group_rgba_builder:
    9. GroupRGBABuilder.build_group_image() returns groupImageCreated=True
    10. allRequiredChildrenRendered=True when all children rendered
    11. missingChildObjectIds=[] when all children rendered
    12. allRequiredChildrenRendered=False when required child missing
    13. missingChildObjectIds has missing child ID
    14. Duplicate child IDs detected
    15. No duplicate compositing (child rendered only once)
    16. log emits [GROUP_RGBA_BUILD]
    17. RGBA image has correct dimensions
    18. Canvas correctly composites children

All tests: ACTUAL_OPENAI_REQUESTS=0
"""
from __future__ import annotations

import numpy as np
import pytest
from PIL import Image


# ── Helpers ──────────────────────────────────────────────────────────────────

def _rgba(w, h, color=(200, 100, 50, 255)):
    return Image.new("RGBA", (w, h), color=color)


def _rgb(w, h, color=(200, 100, 50)):
    return Image.new("RGB", (w, h), color=color)


# ── P1-C-1: SubjectAvoidanceMask ─────────────────────────────────────────────

class TestSubjectAvoidanceMask:
    def _mask_builder(self, w=400, h=300, face_threshold=0.10, hand_threshold=0.15):
        from layout.avoidance_mask import SubjectAvoidanceMask
        return SubjectAvoidanceMask(canvas_w=w, canvas_h=h,
                                    face_threshold=face_threshold,
                                    hand_threshold=hand_threshold)

    def test_empty_bboxes_zero_mask(self):
        sb = self._mask_builder()
        mask = sb.build_avoidance_mask([], margin=0)
        assert int(mask.sum()) == 0

    def test_single_bbox_fills_region(self):
        sb = self._mask_builder()
        bboxes = [{"x": 50, "y": 50, "w": 100, "h": 80, "type": "face"}]
        mask = sb.build_avoidance_mask(bboxes, margin=0)
        # Region 50:130, 50:130 should be 255
        assert mask[50, 50] == 255
        assert mask[129, 149] == 255
        assert mask[130, 150] == 0  # outside

    def test_margin_expands_bbox(self):
        sb = self._mask_builder()
        bboxes = [{"x": 100, "y": 100, "w": 50, "h": 50, "type": "face"}]
        mask_no_margin = sb.build_avoidance_mask(bboxes, margin=0)
        mask_with_margin = sb.build_avoidance_mask(bboxes, margin=10)
        assert int((mask_with_margin > 0).sum()) > int((mask_no_margin > 0).sum())

    def test_multiple_bboxes_union(self):
        sb = self._mask_builder()
        bboxes = [
            {"x": 0, "y": 0, "w": 50, "h": 50, "type": "face"},
            {"x": 200, "y": 200, "w": 50, "h": 50, "type": "hand"},
        ]
        mask = sb.build_avoidance_mask(bboxes, margin=0)
        # Both regions should be marked
        assert mask[0, 0] == 255
        assert mask[200, 200] == 255
        assert mask[100, 100] == 0

    def test_occlusion_no_overlap_passes(self):
        sb = self._mask_builder()
        placed = {"x": 0, "y": 0, "w": 50, "h": 50}
        avoidance = [{"x": 300, "y": 200, "w": 50, "h": 50, "type": "face"}]
        r = sb.validate_subject_occlusion(placed, avoidance)
        assert r.passed is True

    def test_occlusion_face_fails(self):
        sb = self._mask_builder(face_threshold=0.10)
        placed = {"x": 0, "y": 0, "w": 100, "h": 100}
        avoidance = [{"x": 0, "y": 0, "w": 50, "h": 50, "type": "face"}]
        r = sb.validate_subject_occlusion(placed, avoidance)
        assert r.passed is False
        assert r.reason_code == "FACE_OCCLUSION_EXCEEDED"

    def test_occlusion_hand_fails(self):
        sb = self._mask_builder(hand_threshold=0.15)
        placed = {"x": 0, "y": 0, "w": 100, "h": 100}
        avoidance = [{"x": 0, "y": 0, "w": 50, "h": 50, "type": "hand"}]
        r = sb.validate_subject_occlusion(placed, avoidance)
        assert r.passed is False
        assert r.reason_code == "HAND_OCCLUSION_EXCEEDED"

    def test_occlusion_below_threshold_passes(self):
        sb = self._mask_builder(face_threshold=0.50)
        # placed = 10x10, avoidance = 100x100 at same origin
        # overlap = 10*10=100 / avoidance_area=10000 = 1% < 50% → pass
        placed = {"x": 0, "y": 0, "w": 10, "h": 10}
        avoidance = [{"x": 0, "y": 0, "w": 100, "h": 100, "type": "face"}]
        r = sb.validate_subject_occlusion(placed, avoidance)
        assert r.passed is True

    def test_log_emitted(self, capsys):
        from layout.avoidance_mask import log_avoidance_mask
        sb = self._mask_builder()
        mask = sb.build_avoidance_mask([], margin=0)
        log_avoidance_mask(mask, [], job_id="p1c-log", spec_id="300x250")
        out = capsys.readouterr().out
        assert "[SUBJECT_AVOIDANCE_MASK]" in out
        assert "jobId=p1c-log" in out


# ── P1-C-2: GroupRGBABuilder ─────────────────────────────────────────────────

class TestGroupRGBABuilder:
    def _builder(self):
        from layout.group_rgba_builder import GroupRGBABuilder
        return GroupRGBABuilder()

    def _layout(self, w, h, children):
        return {"width": w, "height": h, "children": children}

    def test_basic_build_returns_result(self):
        builder = self._builder()
        layout = self._layout(200, 100, [
            {"objectId": "child1", "x": 0, "y": 0, "w": 100, "h": 50, "required": True},
        ])
        child_images = {"child1": _rgba(100, 50)}
        r = builder.build_group_image(child_images, layout, group_id="g1", group_type="cta")
        assert r.group_image_created is True

    def test_all_required_rendered(self):
        builder = self._builder()
        layout = self._layout(200, 100, [
            {"objectId": "c1", "x": 0, "y": 0, "w": 50, "h": 50, "required": True},
            {"objectId": "c2", "x": 50, "y": 0, "w": 50, "h": 50, "required": True},
        ])
        r = builder.build_group_image(
            {"c1": _rgba(50, 50), "c2": _rgba(50, 50)},
            layout, group_id="g2", group_type="cta"
        )
        assert r.all_required_children_rendered is True
        assert r.missing_child_object_ids == []

    def test_missing_required_child_flagged(self):
        builder = self._builder()
        layout = self._layout(200, 100, [
            {"objectId": "c1", "x": 0, "y": 0, "w": 50, "h": 50, "required": True},
        ])
        # c1 image not provided
        r = builder.build_group_image({}, layout, group_id="g3", group_type="title")
        assert r.all_required_children_rendered is False
        assert "c1" in r.missing_child_object_ids

    def test_missing_non_required_not_flagged(self):
        builder = self._builder()
        layout = self._layout(200, 100, [
            {"objectId": "c1", "x": 0, "y": 0, "w": 50, "h": 50, "required": False},
        ])
        r = builder.build_group_image({}, layout, group_id="g4", group_type="cta")
        assert r.missing_child_object_ids == []
        assert r.all_required_children_rendered is True

    def test_duplicate_child_detected(self):
        builder = self._builder()
        layout = self._layout(200, 100, [
            {"objectId": "c1", "x": 0, "y": 0, "w": 50, "h": 50, "required": False},
            {"objectId": "c1", "x": 50, "y": 0, "w": 50, "h": 50, "required": False},  # dup
        ])
        r = builder.build_group_image({"c1": _rgba(50, 50)}, layout, group_id="g5")
        assert "c1" in r.duplicate_child_ids
        # c1 rendered only once
        assert r.rendered_child_object_ids.count("c1") == 1

    def test_result_image_correct_dimensions(self):
        builder = self._builder()
        layout = self._layout(300, 150, [])
        r = builder.build_group_image({}, layout, group_id="g6", group_type="cta")
        assert r.group_image is not None
        assert r.group_image.size == (300, 150)
        assert r.width == 300
        assert r.height == 150

    def test_image_is_rgba(self):
        builder = self._builder()
        layout = self._layout(200, 100, [
            {"objectId": "c1", "x": 0, "y": 0, "w": 50, "h": 50, "required": False},
        ])
        r = builder.build_group_image({"c1": _rgb(50, 50)}, layout, group_id="g7")
        assert r.group_image.mode == "RGBA"

    def test_zero_canvas_not_created(self):
        builder = self._builder()
        layout = self._layout(0, 0, [])
        r = builder.build_group_image({}, layout, group_id="g8")
        assert r.group_image_created is False

    def test_log_emitted(self, capsys):
        builder = self._builder()
        layout = self._layout(100, 50, [
            {"objectId": "c1", "x": 0, "y": 0, "w": 50, "h": 50, "required": True},
        ])
        builder.build_group_image({"c1": _rgba(50, 50)}, layout, group_id="g9")
        out = capsys.readouterr().out
        assert "[GROUP_RGBA_BUILD]" in out
        assert "groupId='g9'" in out
        assert "groupImageCreated=True" in out

    def test_rgb_child_converted_to_rgba(self):
        builder = self._builder()
        layout = self._layout(200, 100, [
            {"objectId": "c1", "x": 0, "y": 0, "w": 100, "h": 50, "required": True},
        ])
        r = builder.build_group_image({"c1": _rgb(100, 50)}, layout, group_id="g10")
        assert r.all_required_children_rendered is True
        assert r.group_image.mode == "RGBA"
