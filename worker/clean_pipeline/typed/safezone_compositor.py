"""TYPE F P5: safe zone 기반 전경 합성.

scene_plate(배경) 위에 product_cutout(RGBA 전경)을 safe zone 우측 영역에 배치.
PIL alpha_composite 사용.

Safe Zone 계산:
  effective_width  = target_w - safe_left - safe_right
  effective_height = target_h - safe_top  - safe_bottom
  product는 effective_height 초과하지 않도록 비율 유지 thumbnail 후
  safe zone 우측(x = safe_left + effective_width - product_w)에 배치.

Outputs under output/{jobId}/clean_v1/08_final/:
  result.png
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from clean_pipeline.contracts import PipelineStatus, StageName, StageResult
from clean_pipeline.pipeline_logger import PipelineLogger

STAGE = StageName.COMPOSITE_SAFEZONE
_OUTPUT_SUBDIR = Path("clean_v1") / "08_final"


def composite(
    scene_plate_path: str,
    cutout_path: str,
    target_width: int,
    target_height: int,
    safe_left: int,
    safe_top: int,
    safe_right: int,
    safe_bottom: int,
    output_dir: str,
    job_id: str,
    logger: PipelineLogger,
) -> tuple[StageResult, str | None]:
    """scene_plate 위에 product_cutout을 safe zone 우측 배치 후 result.png 저장.

    Returns (StageResult, result_path | None)
    """
    stage_dir = Path(output_dir) / job_id / _OUTPUT_SUBDIR
    stage_dir.mkdir(parents=True, exist_ok=True)
    result_path = str(stage_dir / "result.png")

    has_safe_zone = (safe_left + safe_top + safe_right + safe_bottom) > 0
    eff_w = target_width - safe_left - safe_right
    eff_h = target_height - safe_top - safe_bottom

    logger.stage_start(
        STAGE.value,
        f"composite safe_zone={'yes' if has_safe_zone else 'none'} "
        f"eff={eff_w}×{eff_h} target={target_width}×{target_height}",
        metrics={
            "safeLeft": safe_left, "safeTop": safe_top,
            "safeRight": safe_right, "safeBottom": safe_bottom,
            "effectiveWidth": eff_w, "effectiveHeight": eff_h,
        },
    )

    try:
        scene = Image.open(scene_plate_path).convert("RGBA")
        if scene.size != (target_width, target_height):
            scene = scene.resize((target_width, target_height), Image.LANCZOS)
    except Exception as exc:
        return _fail(logger, "SCENE_LOAD_FAILED", f"scene_plate 로드 실패: {exc}")

    try:
        cutout = Image.open(cutout_path).convert("RGBA")
    except Exception as exc:
        return _fail(logger, "CUTOUT_LOAD_FAILED", f"product_cutout 로드 실패: {exc}")

    # safe zone 내 proportional 축소 (eff_h 초과 금지)
    cutout.thumbnail((eff_w, eff_h), Image.LANCZOS)
    p_w, p_h = cutout.size

    # 우측 safe zone 기준 배치
    paste_x = safe_left + eff_w - p_w
    paste_y = safe_top + (eff_h - p_h) // 2

    # alpha_composite: scene RGBA 위에 cutout 합성
    overlay = Image.new("RGBA", (target_width, target_height), (0, 0, 0, 0))
    overlay.paste(cutout, (paste_x, paste_y))
    result = Image.alpha_composite(scene, overlay).convert("RGB")

    result.save(result_path)
    logger.artifact_written(STAGE.value, result_path, "safe zone composite result.png 저장")
    logger.stage_pass(
        STAGE.value,
        f"PASS — product ({p_w}×{p_h}) at ({paste_x},{paste_y})",
        metrics={
            "productWidth": p_w, "productHeight": p_h,
            "pasteX": paste_x, "pasteY": paste_y,
        },
    )

    return StageResult(
        stage=STAGE,
        status=PipelineStatus.PASS,
        metrics={"productWidth": p_w, "productHeight": p_h, "pasteX": paste_x, "pasteY": paste_y},
        artifacts={"result": result_path},
    ), result_path


# ── Helper ────────────────────────────────────────────────────────────────────


def _fail(logger: PipelineLogger, code: str, message: str) -> tuple[StageResult, None]:
    logger.stage_fail(STAGE.value, code, message)
    return StageResult(
        stage=STAGE,
        status=PipelineStatus.FAIL,
        reasons=[f"[{code}] {message}"],
    ), None
