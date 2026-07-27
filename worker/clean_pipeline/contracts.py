"""Fail-closed contracts for clean_pipeline.

Every stage returns StageResult(PASS|FAIL).
FAIL without reasons is a contract violation.
No fallbacks. No best-effort results.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class PipelineStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"


class StageName(str, Enum):
    SOURCE_PREPARATION = "SOURCE_PREPARATION"
    SCENE_ANALYSIS = "SCENE_ANALYSIS"
    OBJECT_EXTRACTION = "OBJECT_EXTRACTION"
    REMOVAL_MASK = "REMOVAL_MASK"
    SCENE_GENERATION = "SCENE_GENERATION"
    SCENE_VALIDATION = "SCENE_VALIDATION"
    LAYOUT = "LAYOUT"
    FINAL_VALIDATION = "FINAL_VALIDATION"


@dataclass
class StageResult:
    stage: StageName
    status: PipelineStatus
    reasons: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status == PipelineStatus.FAIL and not self.reasons:
            raise ValueError(
                f"FAIL StageResult for '{self.stage.value}' must include at least one reason"
            )


@dataclass
class TargetSpec:
    width: int
    height: int
    safe_top: int = 0
    safe_right: int = 0
    safe_bottom: int = 0
    safe_left: int = 0


@dataclass
class CleanPipelineRequest:
    job_id: str
    source_path: str
    target_specs: list[TargetSpec]
    output_directory: str


@dataclass
class CleanPipelineResult:
    job_id: str
    status: PipelineStatus
    stage_results: list[StageResult] = field(default_factory=list)
    output_paths: list[str] = field(default_factory=list)
    failure_code: str = ""
    failure_message: str = ""
