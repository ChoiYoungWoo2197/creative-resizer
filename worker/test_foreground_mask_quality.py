"""Stage 4 tests: Foreground mask quality checker.

Verifies check_mask_contamination and filter_clean_fg_layers:
  - Layers with confidence=0 / no evidence / no maskRef / recompose=False are rejected
  - Clean layers pass through unchanged
  - Contamination log is emitted only when rejections occur
  - filter returns (clean_layers, rejected_pairs) tuple

Zero actual AI/OpenAI requests.
"""
from __future__ import annotations

import io
import contextlib
import pytest


def _capture_filter(layers, job_id="j", spec_id="s"):
    from foreground.mask_quality import filter_clean_fg_layers
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        clean, rejected = filter_clean_fg_layers(layers, job_id=job_id, spec_id=spec_id)
    return clean, rejected, buf.getvalue()


def _full_layer(**overrides):
    base = {
        "objectId": "obj1",
        "role": "product",
        "confidence": 0.85,
        "semanticEvidence": ["gdino_box"],
        "maskRef": "sha256abc",
        "recompose": True,
    }
    base.update(overrides)
    return base


# ── check_mask_contamination ──────────────────────────────────────────────────

class TestCheckMaskContamination:
    def test_clean_layer_passes(self):
        from foreground.mask_quality import check_mask_contamination
        result = check_mask_contamination(_full_layer())
        assert result.isClean is True
        assert result.reasonCodes == []

    def test_confidence_zero_rejected(self):
        from foreground.mask_quality import check_mask_contamination
        result = check_mask_contamination(_full_layer(confidence=0.0))
        assert result.isClean is False
        assert "CONFIDENCE_ZERO" in result.reasonCodes

    def test_negative_confidence_rejected(self):
        from foreground.mask_quality import check_mask_contamination
        result = check_mask_contamination(_full_layer(confidence=-0.1))
        assert "CONFIDENCE_ZERO" in result.reasonCodes

    def test_no_evidence_rejected(self):
        from foreground.mask_quality import check_mask_contamination
        result = check_mask_contamination(_full_layer(semanticEvidence=[]))
        assert "NO_SEMANTIC_EVIDENCE" in result.reasonCodes

    def test_no_mask_ref_rejected(self):
        from foreground.mask_quality import check_mask_contamination
        result = check_mask_contamination(_full_layer(maskRef=""))
        assert "NO_MASK_REF" in result.reasonCodes

    def test_recompose_false_rejected(self):
        from foreground.mask_quality import check_mask_contamination
        result = check_mask_contamination(_full_layer(recompose=False))
        assert "RECOMPOSE_FALSE" in result.reasonCodes

    def test_all_contamination_flags(self):
        from foreground.mask_quality import check_mask_contamination
        result = check_mask_contamination({
            "objectId": "x",
            "role": "cta",
            "confidence": 0.0,
            "semanticEvidence": [],
            "maskRef": "",
            "recompose": False,
        })
        assert "CONFIDENCE_ZERO" in result.reasonCodes
        assert "NO_SEMANTIC_EVIDENCE" in result.reasonCodes
        assert "NO_MASK_REF" in result.reasonCodes
        assert "RECOMPOSE_FALSE" in result.reasonCodes

    def test_result_fields(self):
        from foreground.mask_quality import check_mask_contamination
        layer = _full_layer(objectId="pid", role="product")
        result = check_mask_contamination(layer)
        assert result.objectId == "pid"
        assert result.role == "product"
        assert result.confidence == pytest.approx(0.85)
        assert result.hasEvidence is True
        assert result.hasMaskRef is True
        assert result.willRecompose is True

    def test_non_dict_rejected(self):
        from foreground.mask_quality import check_mask_contamination
        result = check_mask_contamination("not_a_dict")
        assert result.isClean is False
        assert "NOT_A_DICT" in result.reasonCodes

    def test_semanticRole_field_also_accepted(self):
        from foreground.mask_quality import check_mask_contamination
        layer = _full_layer()
        layer.pop("role", None)
        layer["semanticRole"] = "title"
        result = check_mask_contamination(layer)
        assert result.role == "title"

    def test_mask_sha256_accepted_as_mask_ref(self):
        from foreground.mask_quality import check_mask_contamination
        layer = _full_layer()
        del layer["maskRef"]
        layer["mask_sha256"] = "abc123"
        result = check_mask_contamination(layer)
        assert result.isClean is True


# ── filter_clean_fg_layers ────────────────────────────────────────────────────

class TestFilterCleanFgLayers:
    def test_empty_returns_empty(self):
        clean, rejected, _ = _capture_filter([])
        assert clean == []
        assert rejected == []

    def test_all_clean_all_returned(self):
        layers = [_full_layer(objectId="obj1"), _full_layer(objectId="obj2")]
        clean, rejected, _ = _capture_filter(layers)
        assert len(clean) == 2
        assert len(rejected) == 0

    def test_all_contaminated_all_rejected(self):
        layers = [
            _full_layer(objectId="c1", confidence=0.0),
            _full_layer(objectId="c2", semanticEvidence=[]),
        ]
        clean, rejected, _ = _capture_filter(layers)
        assert len(clean) == 0
        assert len(rejected) == 2

    def test_mixed_correctly_separated(self):
        layers = [
            _full_layer(objectId="good"),
            _full_layer(objectId="bad", confidence=0.0, semanticEvidence=[], maskRef=""),
        ]
        clean, rejected, _ = _capture_filter(layers)
        assert len(clean) == 1
        assert len(rejected) == 1
        assert clean[0]["objectId"] == "good"

    def test_rejected_pair_contains_result(self):
        from foreground.mask_quality import MaskQualityResult
        layers = [_full_layer(objectId="x", recompose=False)]
        _, rejected, _ = _capture_filter(layers)
        assert len(rejected) == 1
        layer, result = rejected[0]
        assert isinstance(result, MaskQualityResult)
        assert result.objectId == "x"
        assert "RECOMPOSE_FALSE" in result.reasonCodes

    def test_clean_layer_unchanged(self):
        layer = _full_layer(objectId="keep")
        clean, _, _ = _capture_filter([layer])
        assert clean[0] is layer

    def test_contamination_filter_log_emitted_on_rejection(self):
        layers = [_full_layer(objectId="c1", confidence=0.0)]
        _, _, out = _capture_filter(layers)
        assert "MASK_CONTAMINATION_FILTER" in out

    def test_contamination_filter_log_not_emitted_when_clean(self):
        layers = [_full_layer()]
        _, _, out = _capture_filter(layers)
        assert "MASK_CONTAMINATION_FILTER" not in out

    def test_reject_log_emitted_per_layer(self):
        layers = [
            _full_layer(objectId="r1", confidence=0.0),
            _full_layer(objectId="r2", confidence=0.0),
        ]
        _, _, out = _capture_filter(layers)
        assert out.count("MASK_CONTAMINATION_REJECT") == 2

    def test_log_contains_reason_codes(self):
        layers = [_full_layer(confidence=0.0, semanticEvidence=[])]
        _, _, out = _capture_filter(layers)
        assert "CONFIDENCE_ZERO" in out
        assert "NO_SEMANTIC_EVIDENCE" in out

    def test_log_contains_object_id(self):
        layers = [_full_layer(objectId="targetobj", confidence=0.0)]
        _, _, out = _capture_filter(layers)
        assert "targetobj" in out

    def test_clean_count_in_log(self):
        layers = [_full_layer(), _full_layer(objectId="bad", confidence=0.0)]
        _, _, out = _capture_filter(layers)
        assert "cleanCount=1" in out
        assert "rejectedCount=1" in out


# ── Integration: diagnostic logger receives contaminated layers ────────────────

class TestDiagnosticLoggerIntegration:
    def test_semantic_object_reject_fields_after_contamination(self):
        """After Stage 4 filter, contaminated layer is passed to log_semantic_object_reject."""
        import io, contextlib
        from foreground.mask_quality import filter_clean_fg_layers
        from verdict.diagnostic_logger import log_semantic_object_reject

        layer = _full_layer(objectId="rej1", confidence=0.0, semanticEvidence=[], maskRef="")
        _, rejected, _ = _capture_filter([layer])
        assert rejected

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            for rej_layer, rej_qr in rejected:
                log_semantic_object_reject(
                    rej_layer,
                    reason_codes=rej_qr.reasonCodes,
                    mask_metrics={},
                    fail_closed=True,
                    job_id="j", spec_id="s",
                )
        out = buf.getvalue()
        assert "[SEMANTIC_OBJECT_REJECT]" in out
        assert "rej1" in out
        assert "CONFIDENCE_ZERO" in out
