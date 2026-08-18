"""Fail-closed pipeline orchestrator.

TYPE A flow (P1→pass):
  P1  SOURCE_PREPARATION
  pass  원본과 규격 동일 → 캐노니컬 그대로 출력

TYPE B flow (P1→P2→P3→P4→P5→P6→P7→P8):
  P1  SOURCE_PREPARATION
  P2  SCENE_ANALYSIS       (GPT-4o element detection)
  P3  OBJECT_EXTRACTION    (RGBA crop per element)
  P4  REMOVAL_MASK         (inpaint mask build)
  P5  SCENE_GENERATION     (gpt-image-1 ad removal + outpaint)
  P6  SCENE_VALIDATION     (GPT-4o naturalness check)
  P7  LAYOUT               (safe-zone constraint solver)
  P8  FINAL_VALIDATION     (composite + render_validator)

TYPE D flow (P1→P5D→P8):
  P1  SOURCE_PREPARATION
  P5D SCENE_GENERATION_D   (contain-scale + AI border outpaint only)
  P8  FINAL_VALIDATION     (render_validator size check)

Routing: pipeline_type_selector.select() after P1 (scale + ar_ratio criteria).
Override: CLEAN_PIPELINE_TYPE=A, B, or D forces the type for all specs.

Rules:
  - Any FAIL → stop immediately, do NOT create result.png, do NOT proceed
  - Never call the legacy pipeline as fallback
  - api_key: explicit parameter > OPENAI_API_KEY env > BACKGROUND_AI_API_KEY env
"""
from __future__ import annotations

import os
from pathlib import Path

from clean_pipeline.contracts import (
    CleanPipelineRequest,
    CleanPipelineResult,
    PipelineStatus,
    StageName,
    StageResult,
    TargetSpec,
)
from clean_pipeline.pipeline_logger import PipelineLogger


def run(request: CleanPipelineRequest, api_key: str = "") -> CleanPipelineResult:
    resolved_key = (
        api_key
        or os.environ.get("OPENAI_API_KEY", "")
        or os.environ.get("BACKGROUND_AI_API_KEY", "")
    )

    job_id = request.job_id
    logger = PipelineLogger(
        job_id,
        Path(request.output_directory) / job_id / "clean_v1" / "pipeline.jsonl",
    )

    stage_results: list[StageResult] = []

    with logger:
        logger.job_start(
            f"source={request.source_path} specs={len(request.target_specs)}",
            metrics={"specCount": len(request.target_specs), "sourcePath": request.source_path},
        )

        # ── P1: SOURCE_PREPARATION ────────────────────────────────────────────
        from clean_pipeline.source.canonical_source import prepare

        sr, canonical = prepare(request.source_path, request.output_directory, job_id, logger)
        stage_results.append(sr)
        if sr.status == PipelineStatus.FAIL:
            return _fail(job_id, sr, stage_results, logger)

        # Per-spec stages — first spec only for MVP
        spec: TargetSpec = request.target_specs[0]
        output_paths: list[str] = []

        # ── Pipeline type routing (after P1, before any analysis) ─────────────
        from clean_pipeline import pipeline_type_selector
        _VALID = ("A", "B", "D", "E", "F", "G")
        if request.pipeline_type in _VALID:
            pipeline_type = request.pipeline_type
            logger.artifact_written(
                "ORCHESTRATOR", "(memory) pipeline_type",
                f"TYPE {pipeline_type} (request override)",
            )
        else:
            pipeline_type = pipeline_type_selector.select(
                canonical.width, canonical.height, spec.width, spec.height,
                safe_left=spec.safe_left, safe_top=spec.safe_top,
                safe_right=spec.safe_right, safe_bottom=spec.safe_bottom,
            )
            logger.artifact_written(
                "ORCHESTRATOR", "(memory) pipeline_type",
                f"src={canonical.width}×{canonical.height} "
                f"spec={spec.width}×{spec.height} → TYPE {pipeline_type}",
            )

        # ─────────────────────────────────────────────────────────────────────
        # TYPE A: 원본 = 목표 규격 → 캐노니컬 그대로 출력
        # ─────────────────────────────────────────────────────────────────────
        if pipeline_type == "A":
            import shutil
            from pathlib import Path as _Path
            from clean_pipeline.render import render_validator

            result_dir = _Path(request.output_directory) / job_id / "clean_v1" / "08_final"
            result_dir.mkdir(parents=True, exist_ok=True)
            result_path = str(result_dir / "result.png")
            shutil.copy2(canonical.canonical_path, result_path)

            logger.artifact_written("ORCHESTRATOR", result_path, "TYPE A: canonical copied as result")

            is_valid, val_code, val_reason = render_validator.validate(
                result_path, spec.width, spec.height
            )
            final_sr = StageResult(
                stage=StageName.FINAL_VALIDATION,
                status=PipelineStatus.PASS if is_valid else PipelineStatus.FAIL,
                reasons=[] if is_valid else [f"[{val_code}] {val_reason}"],
                artifacts={"result": result_path},
            )
            stage_results.append(final_sr)
            if not is_valid:
                logger.stage_fail(StageName.FINAL_VALIDATION.value, val_code, val_reason)
                return _fail(job_id, final_sr, stage_results, logger)

            output_paths.append(result_path)
            logger.job_pass(
                f"TYPE A job complete — result={result_path}",
                metrics={"outputCount": len(output_paths)},
            )
            return CleanPipelineResult(
                job_id=job_id,
                status=PipelineStatus.PASS,
                stage_results=stage_results,
                output_paths=output_paths,
            )

        # ─────────────────────────────────────────────────────────────────────
        # TYPE G: P1 → P2(트랙별 GPT-4o + birefnet) — P1~P2까지만
        # ─────────────────────────────────────────────────────────────────────
        if pipeline_type == "G":
            from clean_pipeline.typed.type_g_pipeline import run as run_type_g

            return run_type_g(
                request=request,
                spec=spec,
                canonical=canonical,
                stage_results=stage_results,
                logger=logger,
                api_key=resolved_key,
            )

        # ─────────────────────────────────────────────────────────────────────
        # TYPE F: P1 → P2(birefnet) → P3(object-removal) → P4(smart-resize) → P5(composite)
        # ─────────────────────────────────────────────────────────────────────
        if pipeline_type == "F":
            from clean_pipeline.typed.type_f_pipeline import run as run_type_f

            return run_type_f(
                request=request,
                spec=spec,
                canonical=canonical,
                stage_results=stage_results,
                logger=logger,
            )

        # ─────────────────────────────────────────────────────────────────────
        # TYPE E: P1 → P5.5(smart_resize) → P7(safe_zone) → P8
        # ─────────────────────────────────────────────────────────────────────
        if pipeline_type == "E":
            from clean_pipeline.typed.type_e_pipeline import run as run_type_e

            return run_type_e(
                request=request,
                spec=spec,
                canonical=canonical,
                stage_results=stage_results,
                logger=logger,
            )

        # ─────────────────────────────────────────────────────────────────────
        # TYPE D: P1 → P5D → P8
        # ─────────────────────────────────────────────────────────────────────
        if pipeline_type == "D":
            from clean_pipeline.typed.type_d_outpainter import generate as generate_type_d

            sr, scene_plate = generate_type_d(
                canonical_path=canonical.canonical_path,
                target_width=spec.width,
                target_height=spec.height,
                safe_left=spec.safe_left,
                safe_top=spec.safe_top,
                safe_right=spec.safe_right,
                safe_bottom=spec.safe_bottom,
                api_key=resolved_key,
                output_dir=request.output_directory,
                job_id=job_id,
                logger=logger,
            )
            stage_results.append(sr)
            if sr.status == PipelineStatus.FAIL:
                return _fail(job_id, sr, stage_results, logger)

            # P8: size validation only (no layout compositing for TYPE D)
            from clean_pipeline.render import render_validator

            is_valid, val_code, val_reason = render_validator.validate(
                scene_plate.scene_plate_path, spec.width, spec.height
            )
            final_sr = StageResult(
                stage=StageName.FINAL_VALIDATION,
                status=PipelineStatus.PASS if is_valid else PipelineStatus.FAIL,
                reasons=[] if is_valid else [f"[{val_code}] {val_reason}"],
                artifacts={"result": scene_plate.scene_plate_path},
            )
            stage_results.append(final_sr)
            if not is_valid:
                logger.stage_fail(StageName.FINAL_VALIDATION.value, val_code, val_reason)
                return _fail(job_id, final_sr, stage_results, logger)

            logger.stage_pass(
                StageName.FINAL_VALIDATION.value,
                f"TYPE D result validated: {scene_plate.scene_plate_path}",
                metrics={"resultPath": scene_plate.scene_plate_path},
            )
            output_paths.append(scene_plate.scene_plate_path)
            logger.job_pass(
                f"TYPE D job complete — result={scene_plate.scene_plate_path}",
                metrics={"outputCount": len(output_paths)},
            )
            return CleanPipelineResult(
                job_id=job_id,
                status=PipelineStatus.PASS,
                stage_results=stage_results,
                output_paths=output_paths,
            )

        # ─────────────────────────────────────────────────────────────────────
        # TYPE B: P1 → P2 → P3 → P4 → P5 → P6 → P7 → P8
        # ─────────────────────────────────────────────────────────────────────

        # ── P2: SCENE_ANALYSIS ────────────────────────────────────────────────
        # TYPE C: GPT-4o + 계층 텍스트 그룹화 + person_zone + decorative_panel
        # TYPE B: GPT-4o + Structured Outputs + 정규화 좌표(0~1000)
        # TYPE A: o3 + 자유형 JSON + 실제 픽셀 좌표
        from clean_pipeline.typec_config import PIPELINE_TYPE
        from clean_pipeline.scene.scene_plate_generator import _TYPE_B_NO_MASK
        if PIPELINE_TYPE == "CLAUDE":
            from clean_pipeline.analysis.claude_analyzer import analyze
        elif PIPELINE_TYPE == "C":
            from clean_pipeline.analysis.gpt4o_analyzer_typec import analyze
        elif _TYPE_B_NO_MASK:
            from clean_pipeline.analysis.gpt4o_analyzer import analyze
        else:
            from clean_pipeline.analysis.openai_analyzer import analyze

        sr, manifest = analyze(
            canonical_path=canonical.canonical_path,
            image_width=canonical.width,
            image_height=canonical.height,
            source_sha256=canonical.sha256,
            api_key=resolved_key,
            output_dir=request.output_directory,
            job_id=job_id,
            logger=logger,
        )
        stage_results.append(sr)
        if sr.status == PipelineStatus.FAIL:
            return _fail(job_id, sr, stage_results, logger)

        # ── P2.5: INPAINTING_CLEANUP ──────────────────────────────────────────
        # P2에서 생성한 inpaint_mask로 fal-ai/lama 인페인팅 → clean_canonical 생성.
        # FAL_KEY 없거나 실패 시 canonical_path 그대로 pass-through.
        inpaint_mask_path = sr.artifacts.get("inpaint_mask", "")
        from clean_pipeline.inpainting.lama_cleaner import clean as lama_clean

        sr, clean_canonical_path = lama_clean(
            canonical_path=canonical.canonical_path,
            inpaint_mask_path=inpaint_mask_path,
            image_width=canonical.width,
            image_height=canonical.height,
            output_dir=request.output_directory,
            job_id=job_id,
            logger=logger,
        )
        stage_results.append(sr)
        if sr.status == PipelineStatus.FAIL:
            return _fail(job_id, sr, stage_results, logger)

        # ── P3: OBJECT_EXTRACTION ─────────────────────────────────────────────
        # canonical_path: P1 원본 (title_group/logo RGBA 추출용)
        # clean_canonical_path: P2.5 lama 결과 (product SAM2/RGBA 추출용)
        if PIPELINE_TYPE == "C":
            from clean_pipeline.extraction.object_extractor_typec import extract
        else:
            from clean_pipeline.extraction.object_extractor import extract

        sr, extraction = extract(
            canonical_path=clean_canonical_path,
            manifest=manifest,
            output_dir=request.output_directory,
            job_id=job_id,
            logger=logger,
            original_canonical_path=canonical.canonical_path,
        )
        stage_results.append(sr)
        if sr.status == PipelineStatus.FAIL:
            return _fail(job_id, sr, stage_results, logger)

        # ── P4: REMOVAL_MASK ──────────────────────────────────────────────────
        from clean_pipeline.removal.removal_mask_builder import build as build_removal

        sr, removal = build_removal(
            extraction=extraction,
            image_width=canonical.width,
            image_height=canonical.height,
            output_dir=request.output_directory,
            job_id=job_id,
            logger=logger,
        )
        stage_results.append(sr)
        if sr.status == PipelineStatus.FAIL:
            return _fail(job_id, sr, stage_results, logger)

        # ── P5: SCENE_GENERATION ──────────────────────────────────────────────
        # clean_canonical_path: AI inpaint 입력 (환각 텍스트 방지)
        from clean_pipeline.scene.scene_plate_generator import generate as generate_scene

        sr, scene_plate = generate_scene(
            canonical_path=canonical.canonical_path,
            removal_result=removal,
            target_width=spec.width,
            target_height=spec.height,
            api_key=resolved_key,
            output_dir=request.output_directory,
            job_id=job_id,
            logger=logger,
            clean_canonical_path=clean_canonical_path,
        )
        stage_results.append(sr)
        if sr.status == PipelineStatus.FAIL:
            return _fail(job_id, sr, stage_results, logger)

        # ── P6: SCENE_VALIDATION ─────────────────────────────────────────────
        from clean_pipeline.validation.scene_validator import validate as validate_scene

        sr, _ = validate_scene(
            scene_plate_result=scene_plate,
            canonical_path=canonical.canonical_path,
            manifest=manifest,
            api_key=resolved_key,
            output_dir=request.output_directory,
            job_id=job_id,
            logger=logger,
        )
        stage_results.append(sr)
        if sr.status == PipelineStatus.FAIL:
            return _fail(job_id, sr, stage_results, logger)

        # ── P7: LAYOUT ────────────────────────────────────────────────────────
        from clean_pipeline.layout.layout_validator import run as run_layout

        sr, layout_result = run_layout(
            extraction=extraction,
            scene_plate_result=scene_plate,
            manifest=manifest,
            safe_left=spec.safe_left,
            safe_top=spec.safe_top,
            safe_right=spec.safe_right,
            safe_bottom=spec.safe_bottom,
            output_dir=request.output_directory,
            job_id=job_id,
            logger=logger,
            src_width=canonical.width,
            src_height=canonical.height,
        )
        stage_results.append(sr)
        if sr.status == PipelineStatus.FAIL:
            return _fail(job_id, sr, stage_results, logger)

        # ── P8: FINAL_VALIDATION (composite + validate) ───────────────────────
        from clean_pipeline.render import compositor, render_validator

        logger.stage_start(
            StageName.FINAL_VALIDATION.value,
            f"placing {len(layout_result.placed)} objects onto scene_plate",
            metrics={"placedCount": len(layout_result.placed)},
        )

        result_path, comp_code, comp_reason = compositor.composite(
            scene_plate_path=scene_plate.scene_plate_path,
            placed=layout_result.placed,
            manifest=manifest,
            target_width=spec.width,
            target_height=spec.height,
            output_dir=request.output_directory,
            job_id=job_id,
            logger=logger,
        )

        if result_path is None:
            logger.stage_fail(StageName.FINAL_VALIDATION.value, comp_code, comp_reason)
            fail_sr = StageResult(
                stage=StageName.FINAL_VALIDATION,
                status=PipelineStatus.FAIL,
                reasons=[f"[{comp_code}] {comp_reason}"],
            )
            stage_results.append(fail_sr)
            return _fail(job_id, fail_sr, stage_results, logger)

        is_valid, val_code, val_reason = render_validator.validate(
            result_path, spec.width, spec.height
        )

        final_sr = StageResult(
            stage=StageName.FINAL_VALIDATION,
            status=PipelineStatus.PASS if is_valid else PipelineStatus.FAIL,
            reasons=[] if is_valid else [f"[{val_code}] {val_reason}"],
            artifacts={"result": result_path},
        )
        stage_results.append(final_sr)

        if not is_valid:
            logger.stage_fail(StageName.FINAL_VALIDATION.value, val_code, val_reason)
            return _fail(job_id, final_sr, stage_results, logger)

        logger.stage_pass(
            StageName.FINAL_VALIDATION.value,
            f"result.png written: {result_path}",
            metrics={"resultPath": result_path},
        )

        output_paths.append(result_path)
        logger.job_pass(
            f"job complete — result={result_path}",
            metrics={"outputCount": len(output_paths)},
        )

        return CleanPipelineResult(
            job_id=job_id,
            status=PipelineStatus.PASS,
            stage_results=stage_results,
            output_paths=output_paths,
        )


# ── Helper ────────────────────────────────────────────────────────────────────


def _fail(
    job_id: str,
    failed_sr: StageResult,
    stage_results: list[StageResult],
    logger: PipelineLogger,
) -> CleanPipelineResult:
    code = failed_sr.reasons[0] if failed_sr.reasons else "UNKNOWN"
    logger.job_fail(code, f"Stage {failed_sr.stage.value} failed")
    return CleanPipelineResult(
        job_id=job_id,
        status=PipelineStatus.FAIL,
        stage_results=stage_results,
        failure_code=code,
        failure_message=f"Stage {failed_sr.stage.value} failed",
    )
