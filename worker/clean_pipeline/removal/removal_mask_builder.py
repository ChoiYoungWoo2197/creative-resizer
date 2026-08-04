"""REMOVAL_MASK stage: union ad-object masks → removalMask + restoreMask.

Processing:
  1. Load each extraction object's mask (cropped L-mode PNG)
  2. Paste back at source_bbox position to reconstruct full-size mask
  3. max-union all full-size masks (numpy maximum)
  4. 3px dilation via MaxFilter(size=7)
  5. Save removal_mask.png
  6. restore_mask = 255 - removal_mask
  7. Save restore_mask.png

Rules:
  - Include: movable=True AND removableFromScene=True (already satisfied by ExtractionResult.objects)
  - Exclude: protected_subject masks (those are in ExtractionResult.protected, never included here)
  - Do NOT use bbox rectangles as masks
  - Source masks come from polygon rasterization in P3, never recomputed here
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from clean_pipeline.contracts import PipelineStatus, StageName, StageResult
from clean_pipeline.extraction.models import ExtractionResult
from clean_pipeline.pipeline_logger import PipelineLogger
from clean_pipeline.removal.models import RemovalMaskResult

STAGE = StageName.REMOVAL_MASK
_DILATION_PX = 3
_LEFT_GAP_PX = 80   # title_group 등 좌측 경계의 어두운 배경 패널 커버를 위한 확장 폭
_OUTPUT_SUBDIR = Path("clean_v1") / "04_removal"


def build(
    extraction: ExtractionResult,
    image_width: int,
    image_height: int,
    output_dir: str,
    job_id: str,
    logger: PipelineLogger,
) -> tuple[StageResult, RemovalMaskResult | None]:

    stage_dir = Path(output_dir) / job_id / _OUTPUT_SUBDIR
    stage_dir.mkdir(parents=True, exist_ok=True)

    logger.stage_start(
        STAGE.value,
        f"extractedObjects={len(extraction.objects)} imageSize={image_width}x{image_height}",
        metrics={"objectCount": len(extraction.objects)},
    )

    # Candidates: all objects in ExtractionResult (movable+removable, no protected)
    candidates = extraction.objects
    if not candidates:
        return _fail(
            logger, "NO_REMOVABLE_OBJECTS",
            "ExtractionResult has no removable objects — nothing to union",
        )

    # Accumulate full-size masks via max-union
    union_arr = np.zeros((image_height, image_width), dtype=np.uint8)
    loaded_count = 0

    for obj in candidates:
        mask_path = Path(obj.mask_path)
        if not mask_path.exists():
            return _fail(
                logger, "MASK_FILE_MISSING",
                f"Mask file not found for object '{obj.id}': {mask_path}",
            )

        cropped = Image.open(str(mask_path)).convert("L")
        cw, ch = cropped.width, cropped.height

        # Sanity: bounding rect must fit inside the original image
        bx, by = obj.source_bbox.x, obj.source_bbox.y
        if bx + cw > image_width or by + ch > image_height:
            return _fail(
                logger, "SIZE_MISMATCH",
                f"Object '{obj.id}' mask ({cw}×{ch}) at bbox ({bx},{by}) "
                f"exceeds image {image_width}×{image_height}",
            )

        # Reconstruct full-size mask at source position
        full = np.zeros((image_height, image_width), dtype=np.uint8)
        full[by: by + ch, bx: bx + cw] = np.array(cropped, dtype=np.uint8)

        np.maximum(union_arr, full, out=union_arr)
        loaded_count += 1

    # 좌측 갭 확장: removable 객체 마스크의 leftmost 경계에서 _LEFT_GAP_PX 만큼 좌측 연장.
    # title_group bbox 좌측에 존재하는 어두운 배경 패널 구간을 removal_mask에 포함시킴.
    cols_with_mask = np.where(union_arr.max(axis=0) > 0)[0]
    if len(cols_with_mask) > 0:
        leftmost_col = int(cols_with_mask[0])
        gap_start = max(0, leftmost_col - _LEFT_GAP_PX)
        if gap_start < leftmost_col:
            rows_with_mask = union_arr[:, leftmost_col:].max(axis=1) > 0
            union_arr[rows_with_mask, gap_start:leftmost_col] = 255

    # Guard: union must be non-empty before dilation
    if loaded_count > 0 and union_arr.max() == 0:
        return _fail(
            logger, "REMOVAL_MASK_EMPTY",
            f"Union of {loaded_count} object mask(s) is all-zero "
            "— all polygon masks were empty",
        )

    # 3px dilation via MaxFilter(size=7) on PIL image
    union_img = Image.fromarray(union_arr, "L")
    removal_img = union_img.filter(ImageFilter.MaxFilter(size=2 * _DILATION_PX + 1))

    # Guard: after dilation must still be non-empty
    removal_arr = np.array(removal_img, dtype=np.uint8)
    if removal_arr.max() == 0:
        return _fail(
            logger, "REMOVAL_MASK_EMPTY",
            "Removal mask is empty after dilation",
        )

    # restore_mask = complement
    restore_arr = (255 - removal_arr.astype(np.int16)).astype(np.uint8)
    restore_img = Image.fromarray(restore_arr, "L")

    # Save
    removal_path = stage_dir / "removal_mask.png"
    restore_path = stage_dir / "restore_mask.png"
    removal_img.save(str(removal_path))
    restore_img.save(str(restore_path))

    logger.artifact_written(STAGE.value, str(removal_path), "removal mask (white = remove)")
    logger.artifact_written(STAGE.value, str(restore_path), "restore mask (complement)")

    result_obj = RemovalMaskResult(
        job_id=job_id,
        image_width=image_width,
        image_height=image_height,
        source_object_count=loaded_count,
        dilation_px=_DILATION_PX,
        removal_mask_path=str(removal_path),
        restore_mask_path=str(restore_path),
        removal_json_path="",
    )

    removal_json_path = stage_dir / "removal.json"
    removal_json_path.write_text(result_obj.to_json(), encoding="utf-8")
    result_obj.removal_json_path = str(removal_json_path)
    logger.artifact_written(STAGE.value, str(removal_json_path), "removal summary")

    non_zero_pct = round(float((removal_arr > 0).mean()) * 100, 2)
    logger.stage_pass(
        STAGE.value,
        f"union of {loaded_count} masks dilated {_DILATION_PX}px — "
        f"removal covers {non_zero_pct}% of image",
        metrics={
            "sourceObjectCount": loaded_count,
            "dilationPx": _DILATION_PX,
            "removalCoveragePct": non_zero_pct,
        },
    )

    return StageResult(
        stage=STAGE,
        status=PipelineStatus.PASS,
        metrics={
            "sourceObjectCount": loaded_count,
            "dilationPx": _DILATION_PX,
            "removalCoveragePct": non_zero_pct,
        },
        artifacts={
            "removal_mask": str(removal_path),
            "restore_mask": str(restore_path),
            "removal_json": str(removal_json_path),
        },
    ), result_obj


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
        reasons=[f"[{code}] {message}"],
    ), None
