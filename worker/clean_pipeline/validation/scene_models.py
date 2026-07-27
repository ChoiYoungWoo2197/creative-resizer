"""Validation stage output models."""
from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class DeterministicValidationResult:
    decode_success: bool
    target_size_ok: bool
    not_solid_color: bool
    restore_pixels_ok: bool
    passed: bool
    fail_code: str = ""
    reason: str = ""


@dataclass
class AIValidationResult:
    ad_objects_remaining: bool = False
    remaining_object_ids: list[str] = field(default_factory=list)
    protected_subject_preserved: bool = True
    visible_seam_detected: bool = False
    duplicated_fragments_detected: bool = False
    newly_generated_text_detected: bool = False
    scene_naturalness_score: float = 1.0
    passed: bool = True          # AI self-reported; not authoritative — see scene_validator
    reasons: list[str] = field(default_factory=list)
    api_call_count: int = 1


@dataclass
class SceneValidationResult:
    job_id: str
    passed: bool
    fail_codes: list[str] = field(default_factory=list)
    deterministic: DeterministicValidationResult = None   # type: ignore[assignment]
    ai: AIValidationResult = None                         # type: ignore[assignment]
    validation_json_path: str = ""

    def to_dict(self) -> dict:
        det = self.deterministic
        ai = self.ai
        return {
            "jobId": self.job_id,
            "passed": self.passed,
            "failCodes": self.fail_codes,
            "deterministic": {
                "decodeSuccess": det.decode_success,
                "targetSizeOk": det.target_size_ok,
                "notSolidColor": det.not_solid_color,
                "restorePixelsOk": det.restore_pixels_ok,
                "passed": det.passed,
                "failCode": det.fail_code,
                "reason": det.reason,
            } if det else None,
            "ai": {
                "adObjectsRemaining": ai.ad_objects_remaining,
                "remainingObjectIds": ai.remaining_object_ids,
                "protectedSubjectPreserved": ai.protected_subject_preserved,
                "visibleSeamDetected": ai.visible_seam_detected,
                "duplicatedFragmentsDetected": ai.duplicated_fragments_detected,
                "newlyGeneratedTextDetected": ai.newly_generated_text_detected,
                "sceneNaturalnessScore": ai.scene_naturalness_score,
                "apiCallCount": ai.api_call_count,
                "reasons": ai.reasons,
            } if ai else None,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
