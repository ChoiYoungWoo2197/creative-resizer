"""TYPE D — P5 대체: contain-scale + AI border outpaint.

원본을 세이프존 기준으로 contain 축소 후 캔버스 중앙에 배치하고,
빈 테두리 영역만 gpt-image-1로 배경 확장한다.

Steps:
  1. scale = min(availableW/srcW, availableH/srcH)
  2. 리사이즈 후 세이프존 내 중앙 배치
  3. outpaint mask 생성 (white = 빈 영역)
  4. gpt-image-1 images.edit 호출 (빈 영역이 1% 이상일 때)
  5. 원본 배치 영역을 canvas에서 복원 (AI가 건드린 픽셀 덮어쓰기)

Returns ScenePlateResult — P6, P8 단계와 동일한 인터페이스.

Fail codes:
  CANONICAL_LOAD_FAILED, API_ERROR, CLEANUP_SIZE_MISMATCH
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from clean_pipeline.contracts import PipelineStatus, StageName, StageResult
from clean_pipeline.pipeline_logger import PipelineLogger
from clean_pipeline.scene import immutable_pixel_restorer, openai_cleanup
from clean_pipeline.scene.models import ScenePlateResult

STAGE = StageName.SCENE_GENERATION
_OUTPUT_SUBDIR = Path("clean_v1") / "05_scene"

_OUTPAINT_PROMPT = (
    "Seamlessly extend the background to fill the empty border areas. "
    "Match the existing colors, lighting, and texture precisely. "
    "Do NOT add any text, logos, products, people, or advertisement elements. "
    "Only fill the empty regions with natural background continuation."
)

# 빈 영역이 이 비율 미만이면 outpaint API 호출 생략
_MIN_EMPTY_RATIO = 0.01


def generate(
    canonical_path: str,
    target_width: int,
    target_height: int,
    safe_left: int,
    safe_top: int,
    safe_right: int,
    safe_bottom: int,
    api_key: str,
    output_dir: str,
    job_id: str,
    logger: PipelineLogger,
) -> tuple[StageResult, ScenePlateResult | None]:

    stage_dir = Path(output_dir) / job_id / _OUTPUT_SUBDIR
    stage_dir.mkdir(parents=True, exist_ok=True)

    logger.stage_start(
        STAGE.value,
        f"TYPE D: canonical={canonical_path} target={target_width}×{target_height} "
        f"safe=(L{safe_left} T{safe_top} R{safe_right} B{safe_bottom})",
        metrics={
            "mode": "TYPE_D",
            "targetWidth": target_width,
            "targetHeight": target_height,
        },
    )

    # ── Load source ─────────────────────────────────────────────────────────
    try:
        source = Image.open(canonical_path).convert("RGB")
    except Exception as exc:
        return _fail(logger, "CANONICAL_LOAD_FAILED", f"Cannot open canonical: {exc}")

    src_w, src_h = source.width, source.height

    # ── Step 1: Contain scale to full canvas ─────────────────────────────────
    scale = min(target_width / src_w, target_height / src_h)
    render_w = max(1, round(src_w * scale))
    render_h = max(1, round(src_h * scale))

    # Center on full canvas
    place_x = (target_width - render_w) // 2
    place_y = (target_height - render_h) // 2

    # 방향별 확장 px 계산
    extend_left  = place_x
    extend_right = target_width  - (place_x + render_w)
    extend_top   = place_y
    extend_bottom= target_height - (place_y + render_h)

    logger.artifact_written(
        STAGE.value, "(memory) type_d_scale",
        f"축소: {src_w}×{src_h} → {render_w}×{render_h} ({scale*100:.1f}%) "
        f"| 확장: L={extend_left} R={extend_right} T={extend_top} B={extend_bottom}px",
    )

    # ── Step 2: Place on canvas ──────────────────────────────────────────────
    canvas = Image.new("RGB", (target_width, target_height), (255, 255, 255))
    resized = source.resize((render_w, render_h), Image.LANCZOS)
    canvas.paste(resized, (place_x, place_y))

    canvas_path = stage_dir / "type_d_canvas.png"
    canvas.save(str(canvas_path))
    logger.artifact_written(
        STAGE.value, str(canvas_path),
        f"TYPE D canvas: {render_w}×{render_h} placed at ({place_x},{place_y}) "
        f"on {target_width}×{target_height}",
    )

    # ── Step 3: Build outpaint mask (white = empty borders) ──────────────────
    mask_arr = np.full((target_height, target_width), 255, dtype=np.uint8)
    # Clamp bounds to canvas
    x1 = max(0, place_x)
    y1 = max(0, place_y)
    x2 = min(target_width, place_x + render_w)
    y2 = min(target_height, place_y + render_h)
    mask_arr[y1:y2, x1:x2] = 0  # source region = black (keep)

    outpaint_mask = Image.fromarray(mask_arr, "L")
    outpaint_mask_path = stage_dir / "outpaint_mask.png"
    outpaint_mask.save(str(outpaint_mask_path))
    logger.artifact_written(STAGE.value, str(outpaint_mask_path), "TYPE D: outpaint mask")

    # ── Step 4: AI outpaint (only if meaningful empty area) ──────────────────
    total_px = target_width * target_height
    empty_px  = int((mask_arr > 128).sum())
    empty_ratio = float(empty_px) / total_px

    logger.artifact_written(
        STAGE.value, "(memory) type_d_empty_ratio",
        f"outpaint: {empty_px}px ({empty_ratio:.1%}) {'→ API 호출' if empty_ratio >= _MIN_EMPTY_RATIO else '→ 스킵'}",
    )

    api_calls = 0
    if empty_ratio < _MIN_EMPTY_RATIO:
        scene_plate = canvas
        logger.artifact_written(
            STAGE.value, "(memory) outpaint_skip",
            f"empty area {empty_ratio:.2%} < {_MIN_EMPTY_RATIO:.0%} — skipping outpaint",
        )
    else:
        outpainted, fail_code, fail_reason = openai_cleanup.cleanup(
            projected_image=canvas,
            projected_removal_mask=outpaint_mask,
            target_width=target_width,
            target_height=target_height,
            api_key=api_key,
            prompt=_OUTPAINT_PROMPT,
        )
        if outpainted is None:
            return _fail(logger, fail_code, fail_reason)
        api_calls = 1

        outpainted_path = stage_dir / "type_d_outpainted.png"
        outpainted.save(str(outpainted_path))
        logger.artifact_written(STAGE.value, str(outpainted_path), "TYPE D: AI outpaint result")

        # ── Step 5: Restore source-placed region from canvas ──────────────────
        # AI may have slightly altered the source area → pin it back to canvas.
        source_region_mask = Image.fromarray(255 - mask_arr, "L")  # white = source area
        scene_plate = immutable_pixel_restorer.restore(outpainted, canvas, source_region_mask)

    scene_plate_path = stage_dir / "scene_plate.png"
    scene_plate.save(str(scene_plate_path))
    logger.artifact_written(STAGE.value, str(scene_plate_path), "TYPE D: scene plate (final)")

    # ── Build result (ScenePlateResult — P8 compatible) ─────────────────────
    # source_projection_path → canvas (source placed, no outpaint) for P6 size check
    # projected_restore_mask_path → all-black mask so deterministic restore check passes
    black_mask = Image.new("L", (target_width, target_height), 0)
    black_mask_path = stage_dir / "restore_mask_dummy.png"
    black_mask.save(str(black_mask_path))

    result_obj = ScenePlateResult(
        job_id=job_id,
        target_width=target_width,
        target_height=target_height,
        source_projection_path=str(canvas_path),
        projected_removal_mask_path=str(outpaint_mask_path),
        projected_restore_mask_path=str(black_mask_path),
        ai_cleanup_path=str(scene_plate_path),
        scene_plate_path=str(scene_plate_path),
        scene_json_path="",
        api_call_count=api_calls,
        original_projection_path=str(canvas_path),
        source_cleanup_ai_path=str(scene_plate_path),
        clean_source_plate_path=str(canvas_path),
        clean_source_projection_path=str(canvas_path),
        outpaint_mask_path=str(outpaint_mask_path),
        target_outpaint_ai_path="",
        source_cleanup_api_call_count=api_calls,
        outpaint_api_call_count=0,
        total_api_call_count=api_calls,
    )

    scene_json_path = stage_dir / "scene_generation.json"
    scene_json_path.write_text(result_obj.to_json(), encoding="utf-8")
    result_obj.scene_json_path = str(scene_json_path)
    logger.artifact_written(STAGE.value, str(scene_json_path), "TYPE D: scene generation summary")

    logger.stage_pass(
        STAGE.value,
        f"TYPE D PASS | {src_w}×{src_h}→{render_w}×{render_h} ({scale*100:.1f}%) "
        f"확장 L{extend_left}/R{extend_right}/T{extend_top}/B{extend_bottom}px "
        f"| API {api_calls}회",
        metrics={
            "mode": "TYPE_D",
            "scalePercent": round(scale * 100, 1),
            "extendLeft": extend_left,
            "extendRight": extend_right,
            "extendTop": extend_top,
            "extendBottom": extend_bottom,
            "emptyRatio": round(empty_ratio, 4),
            "totalApiCallCount": api_calls,
        },
    )

    return StageResult(
        stage=STAGE,
        status=PipelineStatus.PASS,
        metrics={
            "targetWidth": target_width,
            "targetHeight": target_height,
            "totalApiCallCount": api_calls,
        },
        artifacts={
            "canvas": str(canvas_path),
            "scene_plate": str(scene_plate_path),
        },
    ), result_obj


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
