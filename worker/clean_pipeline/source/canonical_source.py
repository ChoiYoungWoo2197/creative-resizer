"""SOURCE_PREPARATION stage: load source image → single RGBA canonical.png.

Rules:
- PNG/JPG: apply EXIF orientation, convert to RGBA, preserve original dimensions.
- PSD: composite all layers into one image, convert to RGBA.
- No crop. No resize. No layer analysis. No artboard separation.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image, ImageOps

from clean_pipeline.contracts import PipelineStatus, StageName, StageResult
from clean_pipeline.pipeline_logger import PipelineLogger
from clean_pipeline.source.models import CanonicalSource

STAGE = StageName.SOURCE_PREPARATION

_SUPPORTED_RASTER = {".png", ".jpg", ".jpeg"}
_SUPPORTED_PSD = {".psd"}
_SUPPORTED = _SUPPORTED_RASTER | _SUPPORTED_PSD

_OUTPUT_SUBDIR = Path("clean_v1") / "01_source"


def prepare(
    source_path: str,
    output_dir: str,
    job_id: str,
    logger: PipelineLogger,
) -> tuple[StageResult, CanonicalSource | None]:

    stage_dir = Path(output_dir) / job_id / _OUTPUT_SUBDIR
    stage_dir.mkdir(parents=True, exist_ok=True)

    logger.stage_start(STAGE.value, f"source={source_path}")

    src = Path(source_path)

    if not src.exists():
        return _fail(logger, "SOURCE_NOT_FOUND", f"File not found: {source_path}")

    ext = src.suffix.lower()
    if ext not in _SUPPORTED:
        return _fail(
            logger,
            "UNSUPPORTED_SOURCE_TYPE",
            f"Extension {ext!r} is not supported. Supported: {sorted(_SUPPORTED)}",
        )

    try:
        if ext in _SUPPORTED_PSD:
            img = _load_psd(str(src))
            source_type = "psd"
        else:
            img = _load_raster(str(src))
            source_type = "jpg" if ext in {".jpg", ".jpeg"} else "png"
    except Exception as exc:
        return _fail(logger, "LOAD_FAILED", f"Failed to load source: {exc}")

    # Save canonical
    canonical_path = stage_dir / "canonical.png"
    img.save(str(canonical_path), "PNG")

    sha = hashlib.sha256(canonical_path.read_bytes()).hexdigest()[:16]

    meta = CanonicalSource(
        source_type=source_type,
        width=img.width,
        height=img.height,
        mode=img.mode,
        sha256=sha,
        canonical_path=str(canonical_path),
    )

    json_path = stage_dir / "source.json"
    json_path.write_text(meta.to_json(), encoding="utf-8")

    logger.artifact_written(STAGE.value, str(canonical_path), f"{img.width}x{img.height} {img.mode}")
    logger.artifact_written(STAGE.value, str(json_path), "source metadata")
    logger.stage_pass(
        STAGE.value,
        f"canonical {img.width}x{img.height} sha={sha}",
        metrics={"width": img.width, "height": img.height, "mode": img.mode, "sha256": sha},
    )

    return StageResult(
        stage=STAGE,
        status=PipelineStatus.PASS,
        metrics={"width": img.width, "height": img.height, "mode": img.mode, "sha256": sha},
        artifacts={"canonical": str(canonical_path), "source_json": str(json_path)},
    ), meta


# ── Loaders ───────────────────────────────────────────────────────────────────


def _load_raster(path: str) -> Image.Image:
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)
    return img.convert("RGBA")


def _load_psd(path: str) -> Image.Image:
    from psd_tools import PSDImage
    psd = PSDImage.open(path)
    img = psd.composite()
    if img is None:
        raise RuntimeError("PSD composite() returned None — file may be empty or corrupt")
    return img.convert("RGBA")


# ── Helpers ───────────────────────────────────────────────────────────────────


def _fail(
    logger: PipelineLogger,
    code: str,
    message: str,
) -> tuple[StageResult, None]:
    logger.stage_fail(STAGE.value, code, message)
    return StageResult(
        stage=STAGE,
        status=PipelineStatus.FAIL,
        reasons=[message],
    ), None
