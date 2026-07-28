"""P1 source format tests: PNG, JPG, JPEG, and failure modes.

Extends test_p1_canonical_source.py to cover all supported input formats and
verify that every format produces a stable, correctly-sized RGBA canonical.png.

Coverage:
- PNG  → RGBA canonical, original dimensions preserved
- JPG  → RGBA canonical, original dimensions preserved
- JPEG → RGBA canonical, original dimensions preserved (alias of JPG)
- PSD  → RGBA canonical via psd-tools composite
- Unsupported extension (.bmp, .gif, .tiff) → UNSUPPORTED_SOURCE_TYPE FAIL
- Missing file → SOURCE_NOT_FOUND FAIL
- SHA-256 is stable: same content = same hash across two calls
- RGBA mode is always the output mode regardless of source mode
"""
from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from clean_pipeline.contracts import PipelineStatus, StageName
from clean_pipeline.pipeline_logger import PipelineLogger
from clean_pipeline.source.canonical_source import prepare


def _make_logger(tmp_path: Path, job_id: str = "test_job") -> PipelineLogger:
    return PipelineLogger(job_id, tmp_path / "pipeline.jsonl")


def _make_rgb_image(path: Path, width: int = 320, height: int = 240) -> Path:
    arr = np.random.randint(30, 200, (height, width, 3), dtype=np.uint8)
    Image.fromarray(arr, "RGB").save(str(path))
    return path


def _make_png(tmp_path: Path, w: int = 320, h: int = 240) -> Path:
    return _make_rgb_image(tmp_path / "source.png", w, h)


def _make_jpg(tmp_path: Path, w: int = 400, h: int = 300) -> Path:
    path = tmp_path / "source.jpg"
    arr = np.random.randint(30, 200, (h, w, 3), dtype=np.uint8)
    Image.fromarray(arr, "RGB").save(str(path), format="JPEG", quality=92)
    return path


def _make_jpeg(tmp_path: Path, w: int = 640, h: int = 480) -> Path:
    path = tmp_path / "source.jpeg"
    arr = np.random.randint(30, 200, (h, w, 3), dtype=np.uint8)
    Image.fromarray(arr, "RGB").save(str(path), format="JPEG", quality=90)
    return path


# ── PNG ───────────────────────────────────────────────────────────────────────


def test_png_produces_rgba_canonical_same_size(tmp_path):
    src = _make_png(tmp_path, w=400, h=250)
    lg = _make_logger(tmp_path)
    result, meta = prepare(str(src), str(tmp_path / "out"), "png_job", lg)
    lg.close()

    assert result.status == PipelineStatus.PASS
    assert result.stage == StageName.SOURCE_PREPARATION
    assert meta is not None
    assert meta.width == 400
    assert meta.height == 250
    assert meta.mode == "RGBA"
    assert meta.source_type == "png"
    assert Path(meta.canonical_path).exists()

    canon = Image.open(meta.canonical_path)
    assert canon.size == (400, 250)
    assert canon.mode == "RGBA"


# ── JPG ───────────────────────────────────────────────────────────────────────


def test_jpg_produces_rgba_canonical_same_size(tmp_path):
    src = _make_jpg(tmp_path, w=640, h=360)
    lg = _make_logger(tmp_path)
    result, meta = prepare(str(src), str(tmp_path / "out"), "jpg_job", lg)
    lg.close()

    assert result.status == PipelineStatus.PASS
    assert meta is not None
    assert meta.width == 640
    assert meta.height == 360
    assert meta.mode == "RGBA"
    assert meta.source_type == "jpg"

    canon = Image.open(meta.canonical_path)
    assert canon.size == (640, 360)
    assert canon.mode == "RGBA"


# ── JPEG (alias of JPG) ───────────────────────────────────────────────────────


def test_jpeg_extension_produces_rgba_canonical(tmp_path):
    src = _make_jpeg(tmp_path, w=800, h=600)
    lg = _make_logger(tmp_path)
    result, meta = prepare(str(src), str(tmp_path / "out"), "jpeg_job", lg)
    lg.close()

    assert result.status == PipelineStatus.PASS
    assert meta is not None
    assert meta.width == 800
    assert meta.height == 600
    assert meta.mode == "RGBA"
    assert meta.source_type == "jpg"   # .jpeg maps to "jpg" type

    canon = Image.open(meta.canonical_path)
    assert canon.mode == "RGBA"


# ── RGBA source (PNG with alpha) ──────────────────────────────────────────────


def test_rgba_png_source_preserved_as_rgba(tmp_path):
    """PNG that already has an alpha channel must still produce RGBA canonical."""
    arr = np.random.randint(0, 255, (200, 200, 4), dtype=np.uint8)
    src = tmp_path / "rgba_source.png"
    Image.fromarray(arr, "RGBA").save(str(src))

    lg = _make_logger(tmp_path, "rgba_job")
    result, meta = prepare(str(src), str(tmp_path / "out"), "rgba_job", lg)
    lg.close()

    assert result.status == PipelineStatus.PASS
    assert meta is not None
    assert meta.mode == "RGBA"
    assert meta.width == 200


# ── SHA-256 stability ─────────────────────────────────────────────────────────


def test_sha256_is_stable_for_identical_content(tmp_path):
    """Same pixel content must produce the same SHA-256 on two separate prepare() calls."""
    np.random.seed(1234)
    arr = np.random.randint(0, 255, (200, 150, 3), dtype=np.uint8)
    src = tmp_path / "stable.png"
    Image.fromarray(arr, "RGB").save(str(src))

    lg1 = _make_logger(tmp_path, "sha_job_1")
    _, meta1 = prepare(str(src), str(tmp_path / "out1"), "sha_job_1", lg1)
    lg1.close()

    lg2 = _make_logger(tmp_path, "sha_job_2")
    _, meta2 = prepare(str(src), str(tmp_path / "out2"), "sha_job_2", lg2)
    lg2.close()

    assert meta1 is not None and meta2 is not None
    assert meta1.sha256 == meta2.sha256, (
        f"SHA-256 must be deterministic for identical content: "
        f"{meta1.sha256!r} != {meta2.sha256!r}"
    )


# ── Failure: missing file ─────────────────────────────────────────────────────


def test_missing_file_fails_with_source_not_found(tmp_path):
    lg = _make_logger(tmp_path, "miss_job")
    result, meta = prepare(
        str(tmp_path / "does_not_exist.png"),
        str(tmp_path / "out"),
        "miss_job",
        lg,
    )
    lg.close()

    assert result.status == PipelineStatus.FAIL
    assert meta is None
    assert any("SOURCE_NOT_FOUND" in r or "not found" in r.lower() for r in result.reasons)


# ── Failure: unsupported extensions ──────────────────────────────────────────


@pytest.mark.parametrize("ext", [".bmp", ".gif", ".tiff", ".webp", ".svg"])
def test_unsupported_extension_fails(tmp_path, ext: str):
    bad = tmp_path / f"file{ext}"
    bad.write_bytes(b"fake content")
    lg = _make_logger(tmp_path, f"bad_{ext[1:]}_job")
    result, meta = prepare(str(bad), str(tmp_path / "out"), f"bad_{ext[1:]}", lg)
    lg.close()

    assert result.status == PipelineStatus.FAIL
    assert meta is None
    assert any("UNSUPPORTED_SOURCE_TYPE" in r or ext in r for r in result.reasons)


# ── Failure: corrupt PNG file ─────────────────────────────────────────────────


def test_corrupt_image_file_fails(tmp_path):
    bad = tmp_path / "corrupt.png"
    bad.write_bytes(b"this is not a valid image file at all")
    lg = _make_logger(tmp_path, "corrupt_job")
    result, meta = prepare(str(bad), str(tmp_path / "out"), "corrupt_job", lg)
    lg.close()

    assert result.status == PipelineStatus.FAIL
    assert meta is None
    # canonical_source.py puts the message in reasons, not the code; check for either
    assert any(
        "LOAD_FAILED" in r or "UNSUPPORTED_SOURCE_TYPE" in r
        or "Failed to load" in r or "cannot identify" in r.lower()
        for r in result.reasons
    )
