"""Fail-closed pipeline orchestrator.

Executes all 8 stages in strict order:
  P1  SOURCE_PREPARATION
  P2  SCENE_ANALYSIS
  P3  OBJECT_EXTRACTION
  P4  REMOVAL_MASK
  P5  SCENE_GENERATION     (OpenAI DALL-E 2 inpaint, 1 call)
  P6  SCENE_VALIDATION     (OpenAI GPT-4o, 1 call)
  P7  LAYOUT
  P8  FINAL_VALIDATION     (composite + render_validator)

Rules:
  - Any FAIL → stop immediately, do NOT create result.png, do NOT proceed
  - Never call the legacy pipeline as fallback
  - api_key: explicit parameter > OPENAI_API_KEY env > BACKGROUND_AI_API_KEY env
"""
from __future__ import annotations

import os
from pathlib import Path

from worker.clean_pipeline.contracts import (
    CleanPipelineRequest,
    CleanPipelineResult,
    PipelineStatus,
    StageName,
    StageResult,
    TargetSpec,
)
from worker.clean_pipeline.pipeline_logger import PipelineLogger


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
        from worker.clean_pipeline.source.canonical_source import prepare

        sr, canonical = prepare(request.source_path, request.output_directory, job_id, logger)
        stage_results.append(sr)
        if sr.status == PipelineStatus.FAIL:
            return _fail(job_id, sr, stage_results, logger)

        # ── P2: SCENE_ANALYSIS ────────────────────────────────────────────────
        from worker.clean_pipeline.analysis.openai_analyzer import analyze

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

        # ── P3: OBJECT_EXTRACTION ─────────────────────────────────────────────
        from worker.clean_pipeline.extraction.object_extractor import extract

        sr, extraction = extract(
            canonical_path=canonical.canonical_path,
            manifest=manifest,
            output_dir=request.output_directory,
            job_id=job_id,
            logger=logger,
        )
        stage_results.append(sr)
        if sr.status == PipelineStatus.FAIL:
            return _fail(job_id, sr, stage_results, logger)

        # ── P4: REMOVAL_MASK ──────────────────────────────────────────────────
        from worker.clean_pipeline.removal.removal_mask_builder import build as build_removal

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

        # Per-spec stages — first spec only for MVP
        spec: TargetSpec = request.target_specs[0]
        output_paths: list[str] = []

        # ── P5: SCENE_GENERATION ──────────────────────────────────────────────
        from worker.clean_pipeline.scene.scene_plate_generator import generate as generate_scene

        sr, scene_plate = generate_scene(
            canonical_path=canonical.canonical_path,
            removal_result=removal,
            target_width=spec.width,
            target_height=spec.height,
            api_key=resolved_key,
            output_dir=request.output_directory,
            job_id=job_id,
            logger=logger,
        )
        stage_results.append(sr)
        if sr.status == PipelineStatus.FAIL:
            return _fail(job_id, sr, stage_results, logger)

        # ── P6: SCENE_VALIDATION ─────────────────────────────────────────────
        from worker.clean_pipeline.validation.scene_validator import validate as validate_scene

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
        from worker.clean_pipeline.layout.layout_validator import run as run_layout

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
        )
        stage_results.append(sr)
        if sr.status == PipelineStatus.FAIL:
            return _fail(job_id, sr, stage_results, logger)

        # ── P8: FINAL_VALIDATION (composite + validate) ───────────────────────
        from worker.clean_pipeline.render import compositor, render_validator

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
