"""Retry + best-pick policy for P5 scene generation.

SKELETON ONLY — not yet wired into the pipeline.

When activated, P5 will attempt scene generation up to MAX_ATTEMPTS times
and return the result with the highest naturalness score (best-pick).

Cost impact: MAX_ATTEMPTS × current API cost per job.
Activate only after P5/P6 quality is stable enough to justify the spend.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ── Constants ─────────────────────────────────────────────────────────────────

MAX_ATTEMPTS = 3

# If the first attempt achieves this naturalness or higher, skip retries.
EARLY_STOP_NATURALNESS = 0.85

# ── Data model ────────────────────────────────────────────────────────────────


@dataclass
class AttemptResult:
    attempt: int                        # 1-based
    scene_plate_path: str
    naturalness_score: float            # from P6 AI validation
    passed: bool                        # True = P6 PASS
    fail_codes: list[str] = field(default_factory=list)


# ── Selection logic ───────────────────────────────────────────────────────────


def select_best(results: list[AttemptResult]) -> AttemptResult:
    """Return the best result from multiple attempts.

    Priority:
      1. PASS results sorted by naturalness (highest first)
      2. If all FAIL, return the one with the highest naturalness (least bad)
    """
    if not results:
        raise ValueError("results must not be empty")

    passing = [r for r in results if r.passed]
    candidates = passing if passing else results
    return max(candidates, key=lambda r: r.naturalness_score)


def should_retry(result: AttemptResult, attempt: int) -> bool:
    """Return True if another attempt is worthwhile."""
    if attempt >= MAX_ATTEMPTS:
        return False
    if result.passed and result.naturalness_score >= EARLY_STOP_NATURALNESS:
        return False
    return True


# ── TODO: wire into orchestrator ──────────────────────────────────────────────
#
# from clean_pipeline.scene.scene_plate_generator import generate
# from clean_pipeline.validation.scene_validator import validate as validate_scene
#
# def generate_with_retry(
#     canonical_path: str,
#     removal_result: RemovalMaskResult,
#     manifest: SceneManifest,
#     target_width: int,
#     target_height: int,
#     api_key: str,
#     output_dir: str,
#     job_id: str,
#     logger: PipelineLogger,
# ) -> tuple[StageResult, ScenePlateResult | None]:
#
#     attempts: list[AttemptResult] = []
#     best_plate: ScenePlateResult | None = None
#
#     for attempt in range(1, MAX_ATTEMPTS + 1):
#         attempt_job_id = f"{job_id}_attempt{attempt}"
#         result, plate = generate(
#             canonical_path=canonical_path,
#             removal_result=removal_result,
#             target_width=target_width,
#             target_height=target_height,
#             api_key=api_key,
#             output_dir=output_dir,
#             job_id=attempt_job_id,
#             logger=logger,
#         )
#         if plate is None:
#             continue
#
#         _result, validation = validate_scene(
#             scene_plate_result=plate,
#             canonical_path=canonical_path,
#             manifest=manifest,
#             api_key=api_key,
#             output_dir=output_dir,
#             job_id=attempt_job_id,
#             logger=logger,
#         )
#         naturalness = validation.ai.scene_naturalness_score if validation and validation.ai else 0.0
#         ar = AttemptResult(
#             attempt=attempt,
#             scene_plate_path=plate.scene_plate_path,
#             naturalness_score=naturalness,
#             passed=_result.status == PipelineStatus.PASS,
#             fail_codes=validation.fail_codes if validation else [],
#         )
#         attempts.append(ar)
#         if not should_retry(ar, attempt):
#             break
#
#     if not attempts:
#         return _fail(logger, "ALL_ATTEMPTS_FAILED", "All generation attempts returned no plate")
#
#     best = select_best(attempts)
#     # Return the plate corresponding to best.attempt
#     ...
