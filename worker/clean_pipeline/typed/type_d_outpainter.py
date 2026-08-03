"""TYPE D — P5 대체: contain-scale + AI border outpaint.

수치/프롬프트 튜닝 → type_d_config.py / type_d_prompt.py
로직 변경만 이 파일에서.

Steps:
  1. contain-scale → 캔버스 중앙 배치
  2. 기본 마스크 생성 (255=빈영역, 0=원본)
  3. [Rule 5] 균일 색상 영역 → AI 없이 단색 직접 채움
  4. [Rule 1] 마스크를 원본 안쪽으로 확장 (경계 재생성)
  5. [Rule 4] 좌우 색상 차이 크면 L/R 별도 API 호출, 아니면 통합 1회
  6. [Rule 3] alpha blend: 경계를 그라데이션으로 원본에 합성
  7. scene_plate 저장

Fail codes:
  CANONICAL_LOAD_FAILED, API_ERROR, CLEANUP_SIZE_MISMATCH
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from clean_pipeline.contracts import PipelineStatus, StageName, StageResult
from clean_pipeline.pipeline_logger import PipelineLogger
from clean_pipeline.scene import openai_cleanup
from clean_pipeline.scene.models import ScenePlateResult
from clean_pipeline.typed.type_d_config import (
    BLEND_FEATHER_PX,
    DARK_BG_STD_THRESHOLD,
    FORCE_SIDE_EXTEND_PX,
    MASK_INWARD_EXPAND_PX,
    MIN_EMPTY_RATIO,
    SEPARATE_LR_BRIGHTNESS_DIFF,
    SEQUENTIAL_LR_CALLS,
    SOLID_FILL_SAMPLE_WIDTH,
)
from clean_pipeline.typed.type_d_prompt import (
    OUTPAINT_PROMPT,
    OUTPAINT_PROMPT_LEFT,
    OUTPAINT_PROMPT_RIGHT,
    OUTPAINT_PROMPT_SAFEZONE,
)

STAGE = StageName.SCENE_GENERATION
_OUTPUT_SUBDIR = Path("clean_v1") / "05_scene"


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

    has_safe_zone = (safe_left + safe_top + safe_right + safe_bottom) > 0
    logger.stage_start(
        STAGE.value,
        f"TYPE D: target={target_width}×{target_height} "
        f"safe_zone={'yes' if has_safe_zone else 'no'} "
        f"expand={MASK_INWARD_EXPAND_PX}px feather={BLEND_FEATHER_PX}px",
        metrics={"mode": "TYPE_D", "targetWidth": target_width, "targetHeight": target_height,
                 "hasSafeZone": has_safe_zone},
    )

    try:
        source = Image.open(canonical_path).convert("RGB")
    except Exception as exc:
        return _fail(logger, "CANONICAL_LOAD_FAILED", f"Cannot open canonical: {exc}")

    src_w, src_h = source.width, source.height

    # ── Step 1: Scale 및 배치 계산 ───────────────────────────────────────────
    if has_safe_zone:
        # 세이프존 기준 축소 → 세이프존 중심에 배치 (외곽 전체가 outpaint 대상)
        safe_w = target_width  - safe_left - safe_right
        safe_h = target_height - safe_top  - safe_bottom
        scale = min(safe_w / src_w, safe_h / src_h)
        render_w = max(1, round(src_w * scale))
        render_h = max(1, round(src_h * scale))
        safe_cx = safe_left + safe_w // 2
        safe_cy = safe_top  + safe_h // 2
        place_x = safe_cx - render_w // 2
        place_y = safe_cy - render_h // 2
    else:
        # 캔버스 기준 축소 → 캔버스 중앙 배치 (기존 동작)
        scale = min(target_width / src_w, target_height / src_h)
        render_w = max(1, round(src_w * scale))
        render_h = max(1, round(src_h * scale))

        # FORCE_SIDE_EXTEND_PX: 좌우 확장량이 부족하면 추가 축소
        if FORCE_SIDE_EXTEND_PX > 0:
            natural_extend = (target_width - render_w) // 2
            if natural_extend < FORCE_SIDE_EXTEND_PX:
                max_render_w = target_width - 2 * FORCE_SIDE_EXTEND_PX
                scale = max_render_w / src_w
                render_w = max(1, round(src_w * scale))
                render_h = max(1, round(src_h * scale))
                logger.artifact_written(
                    STAGE.value, "(memory) force_extend",
                    f"FORCE_SIDE_EXTEND_PX={FORCE_SIDE_EXTEND_PX}px: "
                    f"자동 확장({natural_extend}px) < 강제 확장 → scale {scale*100:.1f}% "
                    f"render={render_w}×{render_h}",
                )

        place_x = (target_width - render_w) // 2
        place_y = (target_height - render_h) // 2

    extend_l = place_x
    extend_r = target_width  - (place_x + render_w)
    extend_t = place_y
    extend_b = target_height - (place_y + render_h)

    logger.artifact_written(
        STAGE.value, "(memory) type_d_scale",
        f"축소: {src_w}×{src_h} → {render_w}×{render_h} ({scale*100:.1f}%) "
        f"| 확장: L={extend_l} R={extend_r} T={extend_t} B={extend_b}px",
    )

    canvas = Image.new("RGB", (target_width, target_height), (255, 255, 255))
    canvas.paste(source.resize((render_w, render_h), Image.LANCZOS), (place_x, place_y))

    canvas_path = stage_dir / "type_d_canvas.png"
    canvas.save(str(canvas_path))
    logger.artifact_written(STAGE.value, str(canvas_path),
                            f"canvas: {render_w}×{render_h} at ({place_x},{place_y})")

    canvas_arr = np.array(canvas)

    # ── Step 2: 기본 마스크 (255=빈영역, 0=원본) ────────────────────────────
    x1 = max(0, place_x)
    y1 = max(0, place_y)
    x2 = min(target_width,  place_x + render_w)
    y2 = min(target_height, place_y + render_h)

    base_mask = np.full((target_height, target_width), 255, dtype=np.uint8)
    base_mask[y1:y2, x1:x2] = 0

    total_px    = target_width * target_height
    empty_px    = int((base_mask > 128).sum())
    empty_ratio = empty_px / total_px

    logger.artifact_written(
        STAGE.value, "(memory) type_d_empty_ratio",
        f"outpaint: {empty_px}px ({empty_ratio:.1%}) "
        f"{'→ API 호출' if empty_ratio >= MIN_EMPTY_RATIO else '→ 스킵'}",
    )

    if empty_ratio < MIN_EMPTY_RATIO:
        scene_plate = canvas
        api_calls = 0
        logger.artifact_written(STAGE.value, "(memory) outpaint_skip", "empty < 1% → 스킵")
    else:
        # ── Step 3: [Rule 5] 균일 색상 → 단색 직접 채움 ─────────────────────
        work_arr, work_mask = _solid_fill_uniform(canvas_arr, base_mask, x1, y1, x2, y2)
        filled_count = int((base_mask > 128).sum()) - int((work_mask > 128).sum())
        if filled_count > 0:
            logger.artifact_written(STAGE.value, "(memory) solid_fill",
                                    f"Rule5: {filled_count}px 단색 직접 채움 (std<{DARK_BG_STD_THRESHOLD})")

        # ── Step 4: [Rule 1] 마스크 원본 안쪽으로 확장 ──────────────────────
        # 세이프존 모드에서는 비활성: 원본 경계 침범 시 얼굴/제품 복제 아티팩트 발생
        if MASK_INWARD_EXPAND_PX > 0 and not has_safe_zone and int((work_mask > 128).sum()) > 0:
            expanded = Image.fromarray(work_mask).filter(
                ImageFilter.MaxFilter(2 * MASK_INWARD_EXPAND_PX + 1)
            )
            work_mask = np.array(expanded)
            logger.artifact_written(STAGE.value, "(memory) mask_expand",
                                    f"Rule1: 마스크 {MASK_INWARD_EXPAND_PX}px 안쪽 확장")

        outpaint_mask_path = stage_dir / "outpaint_mask.png"
        Image.fromarray(work_mask).save(str(outpaint_mask_path))
        logger.artifact_written(STAGE.value, str(outpaint_mask_path), "outpaint mask (확장 후)")

        work_canvas = Image.fromarray(work_arr)

        # ── Step 5: [Rule 4] 순차/별도/통합 처리 분기 ───────────────────────
        api_calls, outpainted = _run_outpaint(
            work_canvas, work_mask, target_width, target_height,
            x1, y1, x2, y2, api_key, stage_dir, logger,
            has_safe_zone=has_safe_zone,
        )
        if outpainted is None:
            # _run_outpaint already logged; need to return fail
            return _fail(logger, "API_ERROR", "outpaint API failed")

        # ── Step 6: [Rule 3] Alpha blend — 경계 그라데이션 합성 ─────────────
        scene_plate = _gradient_blend(outpainted, canvas, base_mask, BLEND_FEATHER_PX)
        if BLEND_FEATHER_PX > 0:
            logger.artifact_written(STAGE.value, "(memory) alpha_blend",
                                    f"Rule3: feather={BLEND_FEATHER_PX}px 그라데이션 합성")

    # ── Save scene plate ─────────────────────────────────────────────────────
    scene_plate_path = stage_dir / "scene_plate.png"
    scene_plate.save(str(scene_plate_path))
    logger.artifact_written(STAGE.value, str(scene_plate_path), "TYPE D: scene plate (final)")

    black_mask_path = stage_dir / "restore_mask_dummy.png"
    Image.new("L", (target_width, target_height), 0).save(str(black_mask_path))

    result_obj = ScenePlateResult(
        job_id=job_id,
        target_width=target_width,
        target_height=target_height,
        source_projection_path=str(canvas_path),
        projected_removal_mask_path=str(outpaint_mask_path) if empty_ratio >= MIN_EMPTY_RATIO else str(canvas_path),
        projected_restore_mask_path=str(black_mask_path),
        ai_cleanup_path=str(scene_plate_path),
        scene_plate_path=str(scene_plate_path),
        scene_json_path="",
        api_call_count=api_calls,
        original_projection_path=str(canvas_path),
        source_cleanup_ai_path=str(scene_plate_path),
        clean_source_plate_path=str(canvas_path),
        clean_source_projection_path=str(canvas_path),
        outpaint_mask_path=str(outpaint_mask_path) if empty_ratio >= MIN_EMPTY_RATIO else "",
        target_outpaint_ai_path="",
        source_cleanup_api_call_count=api_calls,
        outpaint_api_call_count=0,
        total_api_call_count=api_calls,
    )

    scene_json_path = stage_dir / "scene_generation.json"
    scene_json_path.write_text(result_obj.to_json(), encoding="utf-8")
    result_obj.scene_json_path = str(scene_json_path)

    logger.stage_pass(
        STAGE.value,
        f"TYPE D PASS | {src_w}×{src_h}→{render_w}×{render_h} ({scale*100:.1f}%) "
        f"확장 L{extend_l}/R{extend_r}/T{extend_t}/B{extend_b}px | API {api_calls}회",
        metrics={
            "mode": "TYPE_D",
            "scalePercent": round(scale * 100, 1),
            "extendLeft": extend_l, "extendRight": extend_r,
            "extendTop": extend_t, "extendBottom": extend_b,
            "emptyRatio": round(empty_ratio, 4),
            "totalApiCallCount": api_calls,
        },
    )

    return StageResult(
        stage=STAGE,
        status=PipelineStatus.PASS,
        metrics={"targetWidth": target_width, "targetHeight": target_height,
                 "totalApiCallCount": api_calls},
        artifacts={"canvas": str(canvas_path), "scene_plate": str(scene_plate_path)},
    ), result_obj


# ── Helpers ───────────────────────────────────────────────────────────────────

def _solid_fill_uniform(
    canvas_arr: np.ndarray,
    mask_arr: np.ndarray,
    x1: int, y1: int, x2: int, y2: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Rule 5: 확장 영역이 균일 색상이면 AI 없이 단색으로 채움."""
    result = canvas_arr.copy()
    remaining = mask_arr.copy()
    h, w = mask_arr.shape

    regions = []
    if x1 > 0:   regions.append(("left",   0,  x1,  0,  h,  x1,  min(x1 + SOLID_FILL_SAMPLE_WIDTH, x2)))
    if x2 < w:   regions.append(("right",  x2, w,   0,  h,  max(x2 - SOLID_FILL_SAMPLE_WIDTH, x1), x2))
    if y1 > 0:   regions.append(("top",    0,  w,   0,  y1, 0,    w))
    if y2 < h:   regions.append(("bottom", 0,  w,   y2, h,  0,    w))

    for name, fx1, fx2, fy1, fy2, sx1, sx2 in regions:
        sample = canvas_arr[fy1 if name not in ("top","bottom") else y1:
                            fy2 if name not in ("top","bottom") else y2,
                            sx1:sx2]
        if sample.size == 0:
            continue
        std = float(sample.reshape(-1, 3).std(axis=0).max())
        if std < DARK_BG_STD_THRESHOLD:
            color = np.median(sample.reshape(-1, 3), axis=0).astype(np.uint8)
            result[fy1:fy2, fx1:fx2] = color
            remaining[fy1:fy2, fx1:fx2] = 0

    return result, remaining


def _run_outpaint(
    canvas: Image.Image,
    mask_arr: np.ndarray,
    target_w: int, target_h: int,
    x1: int, y1: int, x2: int, y2: int,
    api_key: str,
    stage_dir: Path,
    logger: PipelineLogger,
    has_safe_zone: bool = False,
) -> tuple[int, Image.Image | None]:
    """좌우 순차/별도/통합 처리 분기.

    우선순위:
      0. has_safe_zone=True → 4방향 통합 1회 (OUTPAINT_PROMPT_SAFEZONE)
      1. SEQUENTIAL_LR_CALLS=true → 좌측 먼저 처리 후 결과를 우측 입력으로 사용
      2. SEPARATE_LR_BRIGHTNESS_DIFF > 0 → 밝기 차이 기반 별도 병렬 처리
      3. 기본 → 통합 1회 처리
    """
    # 세이프존 모드: 4방향 확장이므로 sequential L/R 대신 통합 단일 호출
    if has_safe_zone:
        logger.artifact_written(
            STAGE.value, "(memory) safezone_unified",
            "safe zone 모드: 4방향 통합 단일 호출 (OUTPAINT_PROMPT_SAFEZONE)",
        )
        outpaint_mask = Image.fromarray(mask_arr)
        result, fail_code, fail_reason = openai_cleanup.cleanup(
            projected_image=canvas,
            projected_removal_mask=outpaint_mask,
            target_width=target_w,
            target_height=target_h,
            api_key=api_key,
            prompt=OUTPAINT_PROMPT_SAFEZONE,
        )
        if result is None:
            logger.artifact_written(STAGE.value, "(memory) api_error", f"{fail_code}: {fail_reason}")
            return 1, None
        out_path = stage_dir / "type_d_outpainted.png"
        result.save(str(out_path))
        logger.artifact_written(STAGE.value, str(out_path), "TYPE D: AI outpaint result (safezone)")
        return 1, result

    # 순차 L/R 처리 (좌측 완료 결과 → 우측 입력, 컨텍스트 간섭 차단)
    if SEQUENTIAL_LR_CALLS and x1 > 0 and x2 < target_w:
        logger.artifact_written(
            STAGE.value, "(memory) sequential_lr",
            f"sequential L/R: 좌측 먼저 처리 후 결과를 우측 입력으로 사용 "
            f"(L={x1}px R={target_w - x2}px)",
        )
        return _run_separate_lr(
            canvas, mask_arr, target_w, target_h, api_key, stage_dir, logger,
            prompt_left=OUTPAINT_PROMPT_LEFT,
            prompt_right=OUTPAINT_PROMPT_RIGHT,
            sequential=True,
        )

    if SEPARATE_LR_BRIGHTNESS_DIFF > 0 and x1 > 0 and x2 < target_w:
        canvas_arr = np.array(canvas)
        left_sample  = canvas_arr[:, x1:min(x1 + SOLID_FILL_SAMPLE_WIDTH, x2), :]
        right_sample = canvas_arr[:, max(x2 - SOLID_FILL_SAMPLE_WIDTH, x1):x2, :]
        left_bright  = float(left_sample.mean())
        right_bright = float(right_sample.mean())
        diff = abs(left_bright - right_bright)

        if diff >= SEPARATE_LR_BRIGHTNESS_DIFF:
            logger.artifact_written(
                STAGE.value, "(memory) separate_lr",
                f"Rule4: L/R 밝기 차이 {diff:.1f} >= {SEPARATE_LR_BRIGHTNESS_DIFF} → 별도 처리",
            )
            return _run_separate_lr(canvas, mask_arr, target_w, target_h, api_key, stage_dir, logger)

    # 통합 처리 (기본)
    outpaint_mask = Image.fromarray(mask_arr)
    result, fail_code, fail_reason = openai_cleanup.cleanup(
        projected_image=canvas,
        projected_removal_mask=outpaint_mask,
        target_width=target_w,
        target_height=target_h,
        api_key=api_key,
        prompt=OUTPAINT_PROMPT,
    )
    if result is None:
        logger.artifact_written(STAGE.value, "(memory) api_error", f"{fail_code}: {fail_reason}")
        return 1, None

    out_path = stage_dir / "type_d_outpainted.png"
    result.save(str(out_path))
    logger.artifact_written(STAGE.value, str(out_path), "TYPE D: AI outpaint result")
    return 1, result


def _run_separate_lr(
    canvas: Image.Image,
    mask_arr: np.ndarray,
    target_w: int, target_h: int,
    api_key: str,
    stage_dir: Path,
    logger: PipelineLogger,
    prompt_left: str = OUTPAINT_PROMPT,
    prompt_right: str = OUTPAINT_PROMPT,
    sequential: bool = False,
) -> tuple[int, Image.Image | None]:
    """좌/우 마스크를 각각 별도 API 호출 후 병합.

    sequential=True: 좌측 결과를 우측 입력으로 사용 (컨텍스트 간섭 차단)
    sequential=False: 두 호출 모두 원본 canvas 사용 후 픽셀 병합
    """
    canvas_arr = np.array(canvas)
    mid_x = target_w // 2
    api_calls = 0
    merged = canvas_arr.copy()
    current_input = canvas  # sequential 모드: 이전 결과를 다음 입력으로 사용

    for side, col_start, col_end, prompt in [
        ("left",  0,     mid_x,    prompt_left),
        ("right", mid_x, target_w, prompt_right),
    ]:
        side_mask = np.zeros_like(mask_arr)
        side_mask[:, col_start:col_end] = mask_arr[:, col_start:col_end]

        if not (side_mask > 128).any():
            continue

        result, fail_code, fail_reason = openai_cleanup.cleanup(
            projected_image=current_input,
            projected_removal_mask=Image.fromarray(side_mask),
            target_width=target_w,
            target_height=target_h,
            api_key=api_key,
            prompt=prompt,
        )
        api_calls += 1
        if result is None:
            logger.artifact_written(STAGE.value, "(memory) api_error",
                                    f"{side}: {fail_code}: {fail_reason}")
            return api_calls, None

        result_arr = np.array(result)
        fill_region = side_mask > 128
        merged[fill_region] = result_arr[fill_region]

        if sequential:
            # 다음 호출(우측)은 이번 결과(좌측 채워진 이미지)를 입력으로 사용
            current_input = Image.fromarray(merged)

        out_path = stage_dir / f"type_d_outpainted_{side}.png"
        result.save(str(out_path))
        logger.artifact_written(STAGE.value, str(out_path), f"TYPE D: AI outpaint ({side})")

    return api_calls, Image.fromarray(merged)


def _gradient_blend(
    ai_img: Image.Image,
    original_img: Image.Image,
    base_mask_arr: np.ndarray,
    feather_px: int,
) -> Image.Image:
    """Rule 3: base_mask_arr 경계를 Gaussian blur로 페더링 후 alpha blend."""
    mask_img = Image.fromarray(base_mask_arr, "L")
    if feather_px > 0:
        blurred = mask_img.filter(ImageFilter.GaussianBlur(radius=feather_px))
        alpha = np.array(blurred).astype(np.float32) / 255.0
        # outpaint 영역(흰 캔버스와 blend되면 회색 생김)은 항상 AI 100%
        alpha = np.where(base_mask_arr > 128, 1.0, alpha)
    else:
        alpha = (base_mask_arr > 128).astype(np.float32)

    ai_arr   = np.array(ai_img.convert("RGB")).astype(np.float32)
    orig_arr = np.array(original_img.convert("RGB")).astype(np.float32)

    a = alpha[..., np.newaxis]
    result = ai_arr * a + orig_arr * (1.0 - a)
    return Image.fromarray(np.clip(result, 0, 255).astype(np.uint8), "RGB")


def _fail(logger: PipelineLogger, code: str, message: str) -> tuple[StageResult, None]:
    logger.stage_fail(STAGE.value, code, message)
    return StageResult(
        stage=STAGE,
        status=PipelineStatus.FAIL,
        reasons=[f"[{code}] {message}"],
    ), None
