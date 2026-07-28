"""Pipeline routing tests.

Verifies:
1. pipelineVersion normalization: absent/None/empty → "clean_v1"
2. Explicit version handling: "clean_v1" accepted only; "legacy" and all others are rejected
3. adapt_request ignores all legacy mode params (resizeMode, smartFitStrength, focalPosition, psdMode)
4. adapt_response always reports pipelineVersion="clean_v1" and fallbackUsed=False
5. All source formats default to clean_v1

These tests cover the routing contract without running Flask (Flask is not available in the
test environment). The routing logic is verified through the bridge adapter functions and
pure-logic reproduction of the app.py normalization.
"""
from __future__ import annotations

import pytest

from clean_pipeline.bridge.request_adapter import adapt_request
from clean_pipeline.bridge.response_adapter import adapt_response
from clean_pipeline.contracts import (
    CleanPipelineResult,
    PipelineStatus,
    StageName,
    StageResult,
)


# ── Routing normalization helpers (reproduce app.py logic) ────────────────────

_VALID_PIPELINE_VERSIONS = frozenset({"clean_v1"})


def _resolve_version(data: dict) -> str:
    """Exact reproduction of app.py: data.get("pipelineVersion") or "clean_v1"."""
    return data.get("pipelineVersion") or "clean_v1"


def _is_valid_version(version: str) -> bool:
    return version in _VALID_PIPELINE_VERSIONS


# ── Tests: version normalization ──────────────────────────────────────────────


def test_absent_pipeline_version_resolves_to_clean_v1():
    assert _resolve_version({}) == "clean_v1"


def test_none_pipeline_version_resolves_to_clean_v1():
    assert _resolve_version({"pipelineVersion": None}) == "clean_v1"


def test_empty_pipeline_version_resolves_to_clean_v1():
    assert _resolve_version({"pipelineVersion": ""}) == "clean_v1"


def test_explicit_clean_v1_stays_clean_v1():
    assert _resolve_version({"pipelineVersion": "clean_v1"}) == "clean_v1"


def test_explicit_legacy_resolves_but_is_rejected():
    """Resolver returns "legacy" as-is (no silent rewrite); validation then rejects it."""
    assert _resolve_version({"pipelineVersion": "legacy"}) == "legacy"
    assert _is_valid_version("legacy") is False


# ── Tests: version validation ─────────────────────────────────────────────────


def test_clean_v1_is_a_valid_version():
    assert _is_valid_version("clean_v1") is True


def test_legacy_is_not_a_valid_version():
    assert _is_valid_version("legacy") is False


@pytest.mark.parametrize("bad_version", [
    "legacy",
    "invalid",
    "stage_e",
    "unified_v2",
    "smart-fit",
    "clean-v1",   # typo: hyphen not underscore
    "",
])
def test_unsupported_versions_are_rejected(bad_version: str):
    assert _is_valid_version(bad_version) is False


# ── Tests: adapt_request ignores legacy mode params ───────────────────────────


def test_adapt_request_ignores_resize_mode(tmp_path):
    """resizeMode, smartFitStrength, focalPosition, psdMode are stripped — clean_v1 ignores them."""
    source = tmp_path / "ad.png"
    source.write_bytes(b"fake")

    data = {
        "psdPath": str(source),
        "jobId": "route_job",
        "specs": [{"width": 300, "height": 250, "slug": "naver_300x250"}],
        # Legacy params that must be ignored
        "resizeMode": "smart-fit",
        "smartFitStrength": "fill",
        "focalPosition": "top-right",
        "psdMode": "artboard-first",
        "objectReflowEnabled": True,
    }

    req = adapt_request(data, "route_job", str(tmp_path / "out"))

    # Only source_path, target_specs, output_directory are forwarded
    assert req.source_path == str(source)
    assert len(req.target_specs) == 1
    assert req.target_specs[0].width == 300
    assert req.target_specs[0].height == 250
    # No resizeMode / smartFitStrength / focalPosition attributes exist on CleanPipelineRequest
    assert not hasattr(req, "resize_mode")
    assert not hasattr(req, "smart_fit_strength")
    assert not hasattr(req, "focal_position")
    assert not hasattr(req, "psd_mode")


def test_adapt_request_with_png_source(tmp_path):
    source = tmp_path / "banner.png"
    source.write_bytes(b"fake-png")
    req = adapt_request({"psdPath": str(source), "specs": [{"width": 160, "height": 90}]},
                        "png_job", str(tmp_path / "out"))
    assert req.source_path == str(source)
    assert req.target_specs[0].width == 160


def test_adapt_request_with_jpg_source(tmp_path):
    source = tmp_path / "banner.jpg"
    source.write_bytes(b"fake-jpg")
    req = adapt_request({"psdPath": str(source), "specs": [{"width": 640, "height": 360}]},
                        "jpg_job", str(tmp_path / "out"))
    assert req.source_path == str(source)


def test_adapt_request_with_jpeg_source(tmp_path):
    source = tmp_path / "banner.jpeg"
    source.write_bytes(b"fake-jpeg")
    req = adapt_request({"psdPath": str(source), "specs": [{"width": 720, "height": 480}]},
                        "jpeg_job", str(tmp_path / "out"))
    assert req.source_path == str(source)


def test_adapt_request_with_psd_source(tmp_path):
    source = tmp_path / "design.psd"
    source.write_bytes(b"fake-psd")
    req = adapt_request({"psdPath": str(source), "specs": [{"width": 1200, "height": 628}]},
                        "psd_job", str(tmp_path / "out"))
    assert req.source_path == str(source)


# ── Tests: adapt_response always reports clean_v1 identity ───────────────────


_SPECS = [{"width": 300, "height": 250, "media": "naver", "slug": "naver_300x250", "name": "Test"}]


def test_adapt_response_pass_reports_clean_pipeline_identity(tmp_path):
    """PASS result always carries pipelineVersion=clean_v1 and fallbackUsed=False."""
    fake_file = tmp_path / "result.png"
    fake_file.write_bytes(b"png")

    result = CleanPipelineResult(
        job_id="j1", status=PipelineStatus.PASS, output_paths=[str(fake_file)]
    )
    items, missing = adapt_response(result, _SPECS)

    assert len(items) == 1
    assert items[0]["pipelineVersion"] == "clean_v1"
    assert items[0]["fallbackUsed"] is False
    assert items[0]["renderSource"] == "clean_pipeline"
    assert items[0]["valid"] is True
    assert missing == []


def test_adapt_response_fail_reports_clean_pipeline_identity():
    """FAIL result also carries pipelineVersion=clean_v1 and fallbackUsed=False."""
    fail_sr = StageResult(
        stage=StageName.SCENE_GENERATION,
        status=PipelineStatus.FAIL,
        reasons=["API_ERROR"],
    )
    result = CleanPipelineResult(
        job_id="j2",
        status=PipelineStatus.FAIL,
        stage_results=[fail_sr],
        failure_code="API_ERROR",
        failure_message="API error",
    )
    items, missing = adapt_response(result, _SPECS)

    assert len(items) == 1
    assert items[0]["pipelineVersion"] == "clean_v1"
    assert items[0]["fallbackUsed"] is False
    assert items[0]["renderSource"] == "clean_pipeline"
    assert items[0]["valid"] is False


def test_adapt_response_pass_includes_spec_dimensions(tmp_path):
    """PASS item must carry width, height, slug, media from the spec."""
    fake_file = tmp_path / "result.png"
    fake_file.write_bytes(b"png")

    result = CleanPipelineResult(
        job_id="j3", status=PipelineStatus.PASS, output_paths=[str(fake_file)]
    )
    items, _ = adapt_response(result, _SPECS)

    assert items[0]["width"] == 300
    assert items[0]["height"] == 250
    assert items[0]["slug"] == "naver_300x250"
    assert items[0]["media"] == "naver"
    assert items[0]["name"] == "Test"
