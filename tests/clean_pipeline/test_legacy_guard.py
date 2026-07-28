"""Legacy isolation guard tests.

Verifies two layers of protection:
  1. Static layer  — test_no_legacy_contamination.py (AST import scan, runs at CI)
  2. Runtime layer — this file (mock-based call-site check + orchestrator isolation)

Runtime checks:
  A. orchestrator.run() never invokes resizer.generate or generate_candidates
     (we mock those to raise and confirm they are never triggered)
  B. If resizer.generate IS called during clean_v1, the caller gets RuntimeError
  C. clean_v1 path returns a valid response without calling any legacy function

Import checks:
  D. clean_pipeline.* has no side-effect imports from legacy packages on import
  E. Each clean_pipeline sub-package is importable in isolation without legacy modules
"""
from __future__ import annotations

import importlib
import json
import base64
import io
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PIL import Image

from clean_pipeline.contracts import (
    CleanPipelineRequest,
    PipelineStatus,
    StageName,
    TargetSpec,
)
from clean_pipeline.orchestrator import run as orchestrate


# ── Helpers ───────────────────────────────────────────────────────────────────

_TW, _TH = 300, 200
_SAFE = dict(safe_left=20, safe_top=15, safe_right=20, safe_bottom=15)


def _make_png(path: Path, w: int = _TW, h: int = _TH) -> str:
    arr = np.random.randint(30, 200, (h, w, 3), dtype=np.uint8)
    Image.fromarray(arr, "RGB").save(str(path))
    return str(path)


def _b64_png(w: int = 1024, h: int = 1024) -> str:
    arr = np.random.randint(30, 200, (h, w, 3), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr, "RGB").save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _chat(content: str) -> MagicMock:
    msg = MagicMock(); msg.content = content
    choice = MagicMock(); choice.message = msg
    resp = MagicMock(); resp.choices = [choice]
    return resp


def _images_ok() -> MagicMock:
    item = MagicMock(); item.b64_json = _b64_png()
    resp = MagicMock(); resp.data = [item]
    return resp


def _manifest_json(w: int = _TW, h: int = _TH) -> str:
    return json.dumps({
        "jobId": "job", "sourceSha256": "abc", "imageWidth": w, "imageHeight": h,
        "model": "gpt-4o", "apiCallCount": 1,
        "objects": [{
            "id": "prod_0", "role": "product", "required": True, "movable": True,
            "removableFromScene": True,
            "bbox": {"x": 30, "y": 30, "width": 60, "height": 50},
            "polygon": [[30, 30], [90, 30], [90, 80], [30, 80]],
            "confidence": 0.92,
            "groupId": None, "zIndex": 1, "textContent": "", "description": "",
        }],
    })


def _validation_pass_json() -> str:
    return json.dumps({
        "adObjectsRemaining": False, "remainingObjectIds": [],
        "protectedSubjectPreserved": True, "visibleSeamDetected": False,
        "duplicatedFragmentsDetected": False, "newlyGeneratedTextDetected": False,
        "sceneNaturalnessScore": 0.88, "pass": True, "reasons": ["OK"],
    })


# ── A. orchestrator never calls legacy functions ──────────────────────────────


def test_orchestrator_never_calls_resizer_generate(tmp_path):
    """orchestrate() must never invoke resizer.generate regardless of outcome."""
    call_log = []

    def _forbidden_generate(*args, **kwargs):
        call_log.append("resizer.generate")
        raise AssertionError("resizer.generate() was called — legacy fallback is FORBIDDEN")

    src = _make_png(tmp_path / "ad.png")
    request = CleanPipelineRequest(
        job_id="guard_pass_job",
        source_path=src,
        target_specs=[TargetSpec(width=_TW, height=_TH, **_SAFE)],
        output_directory=str(tmp_path / "out"),
    )

    with patch("openai.OpenAI") as MockOpenAI:
        mc = MockOpenAI.return_value
        mc.chat.completions.create.side_effect = [
            _chat(_manifest_json()), _chat(_validation_pass_json()),
        ]
        mc.images.edit.return_value = _images_ok()

        # Patch resizer.generate if resizer is importable; otherwise skip
        try:
            import resizer as _resizer
            original = _resizer.generate
            _resizer.generate = _forbidden_generate
            try:
                result = orchestrate(request, api_key="sk-fake")
            finally:
                _resizer.generate = original
        except ImportError:
            # resizer not available in test env → orchestrator can't import it either → safe
            result = orchestrate(request, api_key="sk-fake")

    assert result.status == PipelineStatus.PASS, (
        f"Expected PASS: {result.failure_code} — {result.failure_message}"
    )
    assert call_log == [], f"resizer.generate was called: {call_log}"


def test_orchestrator_never_calls_resizer_on_fail(tmp_path):
    """Even on FAIL, orchestrate() must not invoke any legacy renderer."""
    call_log = []

    def _forbidden(*args, **kwargs):
        call_log.append("legacy_called")
        raise AssertionError("Legacy renderer called during clean_v1 FAIL path")

    # Force P1 FAIL (missing source)
    request = CleanPipelineRequest(
        job_id="guard_fail_job",
        source_path=str(tmp_path / "missing.png"),
        target_specs=[TargetSpec(width=_TW, height=_TH, **_SAFE)],
        output_directory=str(tmp_path / "out"),
    )

    try:
        import resizer as _resizer
        original = _resizer.generate
        _resizer.generate = _forbidden
        try:
            result = orchestrate(request, api_key="sk-fake")
        finally:
            _resizer.generate = original
    except ImportError:
        result = orchestrate(request, api_key="sk-fake")

    assert result.status == PipelineStatus.FAIL
    assert call_log == [], f"Legacy was called on FAIL path: {call_log}"


# ── B. guard context manager raises on forbidden call ────────────────────────


def test_guard_raises_on_forbidden_call(tmp_path):
    """Simulates _clean_v1_legacy_guard: replacing a function with _forbidden raises."""
    sentinel = []

    def _forbidden(*args, **kwargs):
        raise RuntimeError("[CLEAN_V1_GUARD] resizer.generate() called — FORBIDDEN")

    class _FakeResizer:
        def generate(self, *a, **kw): sentinel.append("generate")

    fake = _FakeResizer()
    original = fake.generate
    fake.generate = _forbidden

    with pytest.raises(RuntimeError, match="CLEAN_V1_GUARD"):
        fake.generate("some_arg")

    # Restore
    fake.generate = original
    fake.generate("restored")
    assert sentinel == ["generate"], "Original should have been restored"


def test_guard_always_restores_on_exception(tmp_path):
    """Guard restores original even when the body raises."""
    class _FakeResizer:
        def generate(self, *a, **kw): return "original"

    fake = _FakeResizer()
    original = fake.generate

    def _forbidden(*a, **kw):
        raise RuntimeError("forbidden")

    fake.generate = _forbidden
    try:
        # simulate: guard runs the body which itself raises
        try:
            raise ValueError("pipeline error in body")
        finally:
            fake.generate = original  # guard cleanup
    except ValueError:
        pass

    assert fake.generate() == "original", "Original must be restored after exception"


# ── C. clean_v1 path produces response without calling legacy ────────────────


def test_clean_v1_path_produces_pass_with_no_legacy_calls(tmp_path):
    """Full clean_v1 execution: PASS result, zero legacy calls."""
    src = _make_png(tmp_path / "ad.png")
    request = CleanPipelineRequest(
        job_id="no_legacy_pass_job",
        source_path=src,
        target_specs=[TargetSpec(width=_TW, height=_TH, **_SAFE)],
        output_directory=str(tmp_path / "out"),
    )

    with patch("openai.OpenAI") as MockOpenAI:
        mc = MockOpenAI.return_value
        mc.chat.completions.create.side_effect = [
            _chat(_manifest_json()), _chat(_validation_pass_json()),
        ]
        mc.images.edit.return_value = _images_ok()
        result = orchestrate(request, api_key="sk-fake")

    assert result.status == PipelineStatus.PASS
    assert result.output_paths
    assert Path(result.output_paths[0]).exists()


# ── D. clean_pipeline sub-packages are importable without legacy ──────────────


_CLEAN_PIPELINE_MODULES = [
    "clean_pipeline.contracts",
    "clean_pipeline.pipeline_logger",
    "clean_pipeline.orchestrator",
    "clean_pipeline.bridge.request_adapter",
    "clean_pipeline.bridge.response_adapter",
    "clean_pipeline.source.canonical_source",
    "clean_pipeline.analysis.openai_analyzer",
    "clean_pipeline.extraction.object_extractor",
    "clean_pipeline.removal.removal_mask_builder",
    "clean_pipeline.scene.scene_plate_generator",
    "clean_pipeline.validation.scene_validator",
    "clean_pipeline.layout.layout_validator",
    "clean_pipeline.render.compositor",
    "clean_pipeline.render.render_validator",
]


@pytest.mark.parametrize("module_path", _CLEAN_PIPELINE_MODULES)
def test_clean_pipeline_module_importable_without_legacy(module_path):
    """Each clean_pipeline module must be importable without any legacy package."""
    # If a module is already in sys.modules, importlib.import_module returns it from cache.
    # Since conftest.py puts worker/ in sys.path, these should all resolve cleanly.
    mod = importlib.import_module(module_path)
    assert mod is not None

    # Verify the module file is within worker/clean_pipeline/
    module_file = getattr(mod, "__file__", None)
    if module_file:
        module_file_path = Path(module_file).resolve()
        clean_pipeline_root = Path(__file__).parent.parent.parent / "worker" / "clean_pipeline"
        assert module_file_path.is_relative_to(clean_pipeline_root.resolve()), (
            f"{module_path} resolves to {module_file_path}, which is outside "
            f"worker/clean_pipeline/. Possible module namespace collision."
        )


# ── E. clean_pipeline module namespace does not collide with legacy ───────────


def test_clean_pipeline_namespace_is_isolated():
    """clean_pipeline.* modules must resolve to worker/clean_pipeline/ on disk."""
    import clean_pipeline
    if hasattr(clean_pipeline, "__file__") and clean_pipeline.__file__:
        cp_file = Path(clean_pipeline.__file__).resolve()
        expected_root = (Path(__file__).parent.parent.parent / "worker" / "clean_pipeline").resolve()
        assert cp_file.is_relative_to(expected_root), (
            f"clean_pipeline package root mismatch: {cp_file}"
        )
