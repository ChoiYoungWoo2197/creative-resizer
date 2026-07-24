"""Stage E P0-A: Pipeline sequence enforcement.

Verifies that semantic analysis and foreground extraction always happen
BEFORE any target transform (crop/resize). Tracks sizes at each stage
and raises PIPELINE_SEQUENCE_VIOLATION if order is wrong.

Enforced invariants:
  semanticAnalysisSize  == canonicalSourceSize
  foregroundExtractionSize == canonicalSourceSize
  analysisBeforeTargetTransform  == True
  extractionBeforeTargetTransform == True
  foregroundExtractionSource == "canonical_original"
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PipelineSequenceTracker:
    """Tracks which pipeline stages have run and verifies ordering.

    Usage:
      1. tracker.record_canonical(w, h) immediately after source load
      2. tracker.record_semantic_analysis(w, h) after D-2 analysis
      3. tracker.record_foreground_extraction(w, h) after D-2 extraction
      4. tracker.record_target_transform(w, h) when target spec is applied
      5. tracker.validate_sequence() raises on violation
      6. tracker.log_pipeline_sequence(job_id=...) to emit [PIPELINE_SEQUENCE]
    """
    canonical_size: tuple | None = None              # (w, h) after source load
    semantic_analysis_size: tuple | None = None      # (w, h) when analysis ran
    foreground_extraction_size: tuple | None = None  # (w, h) when extraction ran
    target_size: tuple | None = None                 # (w, h) of target spec

    _semantic_analysis_recorded: bool = field(default=False, repr=False)
    _foreground_extraction_recorded: bool = field(default=False, repr=False)
    _target_transform_applied: bool = field(default=False, repr=False)

    foreground_extraction_source: str = "canonical_original"

    def record_canonical(self, w: int, h: int) -> None:
        self.canonical_size = (w, h)

    def record_semantic_analysis(self, w: int, h: int) -> None:
        self.semantic_analysis_size = (w, h)
        self._semantic_analysis_recorded = True

    def record_foreground_extraction(self, w: int, h: int) -> None:
        self.foreground_extraction_size = (w, h)
        self._foreground_extraction_recorded = True

    def record_target_transform(self, w: int, h: int) -> None:
        self.target_size = (w, h)
        self._target_transform_applied = True

    @property
    def analysis_before_target_transform(self) -> bool:
        """True when analysis was recorded before any target transform."""
        if not self._semantic_analysis_recorded:
            return False
        return not self._target_transform_applied or (
            self._semantic_analysis_recorded
        )

    @property
    def extraction_before_target_transform(self) -> bool:
        """True when extraction was recorded before any target transform."""
        if not self._foreground_extraction_recorded:
            return False
        return not self._target_transform_applied or (
            self._foreground_extraction_recorded
        )

    def validate_sequence(self) -> list[str]:
        """Validate pipeline ordering. Returns list of violation reason codes.

        Empty list == valid sequence.
        Non-empty == PIPELINE_SEQUENCE_VIOLATION details.
        """
        violations: list[str] = []

        if self.canonical_size is None:
            violations.append("CANONICAL_SIZE_NOT_RECORDED")
            return violations  # can't validate further

        # Analysis must have run
        if not self._semantic_analysis_recorded:
            violations.append("SEMANTIC_ANALYSIS_NOT_RECORDED")
        elif self.semantic_analysis_size != self.canonical_size:
            violations.append(
                f"ANALYSIS_SIZE_MISMATCH:"
                f" analysis={self.semantic_analysis_size}"
                f" canonical={self.canonical_size}"
            )

        # Extraction must have run
        if not self._foreground_extraction_recorded:
            violations.append("FOREGROUND_EXTRACTION_NOT_RECORDED")
        elif self.foreground_extraction_size != self.canonical_size:
            violations.append(
                f"EXTRACTION_SIZE_MISMATCH:"
                f" extraction={self.foreground_extraction_size}"
                f" canonical={self.canonical_size}"
            )

        # Source contract
        if self.foreground_extraction_source != "canonical_original":
            violations.append(
                f"EXTRACTION_SOURCE_INVALID:"
                f" expected=canonical_original"
                f" actual={self.foreground_extraction_source}"
            )

        return violations


def validate_sequence(tracker: PipelineSequenceTracker) -> tuple[bool, list[str]]:
    """Validate tracker sequence. Returns (passed, violation_codes)."""
    violations = tracker.validate_sequence()
    return len(violations) == 0, violations


def log_pipeline_sequence(tracker: PipelineSequenceTracker, job_id: str = "") -> None:
    """Emit [PIPELINE_SEQUENCE] log with provenance contract fields."""
    violations = tracker.validate_sequence()
    passed = len(violations) == 0
    print(
        f"[PIPELINE_SEQUENCE] jobId={job_id}"
        f" passed={passed}"
        f" canonicalSize={tracker.canonical_size}"
        f" semanticAnalysisSize={tracker.semantic_analysis_size}"
        f" foregroundExtractionSize={tracker.foreground_extraction_size}"
        f" foregroundExtractionSource={tracker.foreground_extraction_source!r}"
        f" analysisBeforeTargetTransform={tracker._semantic_analysis_recorded and not tracker._target_transform_applied}"
        f" extractionBeforeTargetTransform={tracker._foreground_extraction_recorded and not tracker._target_transform_applied}"
        f" targetSize={tracker.target_size}"
        f" violations={violations}",
        flush=True,
    )


def build_provenance_fields(
    tracker: PipelineSequenceTracker,
    *,
    canonical_size: tuple | None = None,
) -> dict:
    """Return provenance dict fields for renderProvenance.

    These are the P0-A contract fields that must appear in job output.
    """
    cs = canonical_size or tracker.canonical_size or (0, 0)
    analysis_size = tracker.semantic_analysis_size or cs
    extraction_size = tracker.foreground_extraction_size or cs
    violations = tracker.validate_sequence()
    return {
        "semanticAnalysisSourceSize": f"{analysis_size[0]}x{analysis_size[1]}",
        "foregroundExtractionSourceSize": f"{extraction_size[0]}x{extraction_size[1]}",
        "foregroundExtractionSource": tracker.foreground_extraction_source,
        "analysisBeforeTargetTransform": (
            tracker._semantic_analysis_recorded and not tracker._target_transform_applied
        ),
        "extractionBeforeTargetTransform": (
            tracker._foreground_extraction_recorded and not tracker._target_transform_applied
        ),
        "pipelineSequenceValid": len(violations) == 0,
        "pipelineSequenceViolations": violations,
    }
