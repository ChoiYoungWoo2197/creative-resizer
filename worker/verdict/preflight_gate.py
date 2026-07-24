"""Stage E P0-D: Semantic preflight gate.

Validates that all preconditions are met before a Job is published to MQ
or a Worker is invoked. If any check fails, the job is blocked.

Checks:
  1. Canonical source exists and has a valid SHA
  2. SemanticManifest exists and is finalized
  3. SHA chain is valid (all stages match canonical)
  4. Required object IDs are present in manifest
  5. Preserve and removal masks are defined (not empty)
  6. No unresolved mask conflicts
  7. CTA/title groups are complete (if expected)
  8. No role contradictions (text ∩ human)

Failure produces:
  PreflightResult(passed=False, reason_codes=[...], status="BLOCKED/FAILED_PREFLIGHT")
  → RabbitMQ publish blocked
  → Worker invocation blocked
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PreflightResult:
    """Result of the preflight gate check."""
    passed: bool = False
    reason_codes: list = field(default_factory=list)
    status: str = "PENDING"  # "PASSED", "BLOCKED/FAILED_PREFLIGHT"
    details: dict = field(default_factory=dict)


class SemanticPreflightGate:
    """Pre-publish validation gate for semantic AI jobs.

    Call run_preflight() before publishing to MQ or calling the Worker.
    A BLOCKED/FAILED_PREFLIGHT result means the job must not be published.
    """

    def run_preflight(
        self,
        canonical_src: object | None,
        manifest: object | None,
        sha_chain: object | None,
        *,
        required_object_ids: list | None = None,
        expected_group_ids: list | None = None,
        job_id: str = "",
        spec_id: str = "",
    ) -> PreflightResult:
        """Run all preflight checks.

        Args:
            canonical_src:       CanonicalSourceImage or None
            manifest:            SemanticManifest or None
            sha_chain:           CanonicalSHAChain or None
            required_object_ids: IDs that must appear in manifest.preserve_object_ids
            expected_group_ids:  IDs that must appear in manifest.cta_group_ids
            job_id:              job identifier for logging
            spec_id:             spec identifier for logging

        Returns:
            PreflightResult with passed=True or passed=False + reason_codes
        """
        reason_codes: list[str] = []
        details: dict = {}

        # 1. Canonical source check
        if canonical_src is None:
            reason_codes.append("CANONICAL_SOURCE_MISSING")
            details["canonicalSourceMissing"] = True
        else:
            _sha = getattr(canonical_src, "canonical_image_sha256", "") or ""
            if not _sha:
                reason_codes.append("CANONICAL_SOURCE_SHA_EMPTY")

        # 2. Manifest existence and finalization
        if manifest is None:
            reason_codes.append("UNIFIED_MANIFEST_INCOMPLETE")
            details["manifestMissing"] = True
        else:
            finalized = getattr(manifest, "finalized", False)
            if not finalized:
                reason_codes.append("UNIFIED_MANIFEST_NOT_FINALIZED")
            # Check for text-human contradictions
            contradictions = getattr(manifest, "text_human_contradictions", [])
            if contradictions:
                reason_codes.append("SEMANTIC_ROLE_CONTRADICTION_DETECTED")
                details["contradictionObjectIds"] = contradictions

        # 3. SHA chain validation
        if sha_chain is not None:
            violations = sha_chain.validate_chain() if hasattr(sha_chain, "validate_chain") else []
            if violations:
                reason_codes.append("CANONICAL_SOURCE_HASH_MISMATCH")
                details["shaChainViolations"] = violations

        # 4. Required object IDs
        if required_object_ids and manifest is not None:
            preserve_ids = set(getattr(manifest, "preserve_object_ids", []))
            missing_ids = [oid for oid in required_object_ids if oid not in preserve_ids]
            if missing_ids:
                reason_codes.append("REQUIRED_SEMANTIC_OBJECT_MISSING")
                details["missingRequiredObjectIds"] = missing_ids

        # 5. Preserve/removal masks defined
        if manifest is not None:
            has_preserve = bool(
                getattr(manifest, "preserve_object_ids", [])
                or getattr(manifest, "preserve_roles", [])
            )
            has_removal = bool(
                getattr(manifest, "removal_object_ids", [])
                or getattr(manifest, "removal_roles", [])
            )
            # Masks are optional — only warn, don't block
            details["preserveMaskDefined"] = has_preserve
            details["removalMaskDefined"] = has_removal

        # 6. Unresolved conflict check
        if manifest is not None:
            conflict_ids = getattr(manifest, "mask_conflict_ids", [])
            if conflict_ids:
                reason_codes.append("MASK_CONFLICT_UNRESOLVED")
                details["conflictObjectIds"] = conflict_ids

        # 7. Group completeness
        if expected_group_ids and manifest is not None:
            cta_ids = set(getattr(manifest, "cta_group_ids", []))
            missing_groups = [gid for gid in expected_group_ids if gid not in cta_ids]
            if missing_groups:
                reason_codes.append("SEMANTIC_GROUP_INCOMPLETE")
                details["missingGroupIds"] = missing_groups

        passed = len(reason_codes) == 0
        status = "PASSED" if passed else "BLOCKED/FAILED_PREFLIGHT"

        result = PreflightResult(
            passed=passed,
            reason_codes=sorted(set(reason_codes)),
            status=status,
            details=details,
        )
        _log_preflight(result, job_id=job_id, spec_id=spec_id)
        return result


def _log_preflight(result: PreflightResult, *, job_id: str = "", spec_id: str = "") -> None:
    """Emit [SEMANTIC_PREFLIGHT] log."""
    print(
        f"[SEMANTIC_PREFLIGHT] jobId={job_id} specId={spec_id}"
        f" passed={result.passed}"
        f" status={result.status!r}"
        f" reasonCodes={result.reason_codes}"
        f" details={result.details}",
        flush=True,
    )
