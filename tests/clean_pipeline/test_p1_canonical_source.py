"""P1 tests: SOURCE_PREPARATION stage."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from worker.clean_pipeline.contracts import PipelineStatus, StageName
from worker.clean_pipeline.pipeline_logger import PipelineLogger
from worker.clean_pipeline.source.canonical_source import prepare


def _make_logger(tmp_path: Path) -> PipelineLogger:
    return PipelineLogger("test_job", tmp_path / "pipeline.jsonl")


def _make_png(path: Path, width: int = 320, height: int = 240) -> None:
    arr = np.random.randint(30, 220, (height, width, 3), dtype=np.uint8)
    Image.fromarray(arr, "RGB").save(str(path))


# ── Normal: PNG → canonical.png same size, mode RGBA ─────────────────────────


def test_png_canonical_size_and_mode(tmp_path):
    src = tmp_path / "ad.png"
    _make_png(src, width=400, height=250)

    lg = _make_logger(tmp_path)
    result, meta = prepare(str(src), str(tmp_path / "output"), "job01", lg)
    lg.close()

    assert result.status == PipelineStatus.PASS
    assert result.stage == StageName.SOURCE_PREPARATION
    assert meta is not None
    assert meta.width == 400
    assert meta.height == 250
    assert meta.mode == "RGBA"

    canonical = Image.open(meta.canonical_path)
    assert canonical.size == (400, 250)
    assert canonical.mode == "RGBA"


# ── Failure: unsupported extension → UNSUPPORTED_SOURCE_TYPE ─────────────────


def test_unsupported_extension_fails(tmp_path):
    bad = tmp_path / "file.bmp"
    bad.write_bytes(b"BM fake content")

    lg = _make_logger(tmp_path)
    result, meta = prepare(str(bad), str(tmp_path / "output"), "job02", lg)
    lg.close()

    assert result.status == PipelineStatus.FAIL
    assert meta is None
    assert any("UNSUPPORTED_SOURCE_TYPE" in r or ".bmp" in r for r in result.reasons)
