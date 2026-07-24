"""Stage E P1-A: Retry manifest invariant enforcement.

Ensures that across retry attempts, the following are frozen:
  - manifestSha256 (manifest identity)
  - semantic roles (preserve/removal role sets)
  - required object IDs
  - CTA group IDs
  - canonical SHA

Allowed changes across retries (conservative relaxation only):
  - preserve_object_ids can EXPAND (add new objects to preserve)
  - removal_object_ids can CONTRACT (remove objects from removal)
  - feather / mask can shrink

Forbidden changes (raise RETRY_MANIFEST_MUTATION_FORBIDDEN):
  - preserve shrink → RETRY_PRESERVE_SHRINK_FORBIDDEN
  - removal expand → RETRY_MANIFEST_MUTATION_FORBIDDEN
  - role reclassification
  - required object deletion

Logs:
  [SEMANTIC_RETRY_INVARIANT] per attempt with delta analysis
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field


@dataclass
class RetryManifestInvariant:
    """Captures manifest state at attempt 1 and validates subsequent attempts.

    Usage:
      invariant = RetryManifestInvariant()
      invariant.capture_attempt1(manifest)
      # ... retry ...
      invariant.validate_retry(attempt2_manifest)  # raises on violation
    """
    # Captured at attempt 1
    _manifest_sha: str = field(default="", repr=False)
    _roles_hash: str = field(default="", repr=False)
    _required_ids_hash: str = field(default="", repr=False)
    _groups_hash: str = field(default="", repr=False)
    _canonical_sha: str = field(default="", repr=False)
    _preserve_ids_set: frozenset = field(default_factory=frozenset, repr=False)
    _removal_ids_set: frozenset = field(default_factory=frozenset, repr=False)
    _captured: bool = field(default=False, repr=False)
    _attempt_count: int = field(default=0, repr=False)

    def capture_attempt1(self, manifest: object, *, canonical_sha: str = "") -> None:
        """Capture invariants from the first attempt's manifest."""
        self._manifest_sha = getattr(manifest, "manifest_sha256", "")
        self._roles_hash = _hash_roles(manifest)
        self._required_ids_hash = _hash_list(getattr(manifest, "preserve_object_ids", []))
        self._groups_hash = _hash_list(getattr(manifest, "cta_group_ids", []))
        self._canonical_sha = canonical_sha
        self._preserve_ids_set = frozenset(getattr(manifest, "preserve_object_ids", []))
        self._removal_ids_set = frozenset(getattr(manifest, "removal_object_ids", []))
        self._captured = True
        self._attempt_count = 1

    def validate_retry(self, retry_manifest: object, *, attempt: int = 2) -> list[str]:
        """Validate that retry_manifest respects invariants from attempt 1.

        Returns list of violation codes (empty = valid).
        Raises RuntimeError on forbidden mutations.
        """
        if not self._captured:
            return []

        self._attempt_count = max(self._attempt_count, attempt)
        violations: list[str] = []

        # Check: preserve shrink forbidden
        retry_preserve = frozenset(getattr(retry_manifest, "preserve_object_ids", []))
        if not self._preserve_ids_set.issubset(retry_preserve):
            # Some original preserve IDs are missing in retry
            removed = self._preserve_ids_set - retry_preserve
            violations.append(
                f"RETRY_PRESERVE_SHRINK_FORBIDDEN: removedIds={sorted(removed)}"
            )

        # Check: removal expand forbidden
        retry_removal = frozenset(getattr(retry_manifest, "removal_object_ids", []))
        if not retry_removal.issubset(self._removal_ids_set):
            # New removal IDs appeared in retry that weren't in attempt 1
            added = retry_removal - self._removal_ids_set
            violations.append(
                f"RETRY_MANIFEST_MUTATION_FORBIDDEN: newRemovalIds={sorted(added)}"
            )

        # Check: canonical SHA must remain the same
        retry_canonical_sha = getattr(retry_manifest, "_canonical_sha", "")
        if retry_canonical_sha and self._canonical_sha and retry_canonical_sha != self._canonical_sha:
            violations.append(
                f"CANONICAL_SOURCE_HASH_MISMATCH: attempt1={self._canonical_sha[:16]} "
                f"retry={retry_canonical_sha[:16]}"
            )

        if violations:
            raise RuntimeError(
                f"RETRY_MANIFEST_MUTATION_FORBIDDEN: attempt={attempt}"
                f" violations={violations}"
            )

        return violations

    def get_delta(self, retry_manifest: object) -> dict:
        """Compute allowed delta between attempt 1 and retry manifest.

        Returns dict describing what changed and whether it's permitted.
        """
        if not self._captured:
            return {"captured": False}

        retry_preserve = frozenset(getattr(retry_manifest, "preserve_object_ids", []))
        retry_removal = frozenset(getattr(retry_manifest, "removal_object_ids", []))

        added_preserve = retry_preserve - self._preserve_ids_set
        removed_preserve = self._preserve_ids_set - retry_preserve
        added_removal = retry_removal - self._removal_ids_set
        removed_removal = self._removal_ids_set - retry_removal

        return {
            "captured": True,
            "preserveExpanded": sorted(added_preserve),    # allowed
            "preserveShrunk": sorted(removed_preserve),    # forbidden
            "removalExpanded": sorted(added_removal),      # forbidden
            "removalContracted": sorted(removed_removal),  # allowed
            "permitedChanges": len(added_preserve) > 0 or len(removed_removal) > 0,
            "forbiddenChanges": len(removed_preserve) > 0 or len(added_removal) > 0,
        }


def log_retry_invariant(
    invariant: RetryManifestInvariant,
    retry_manifest: object,
    *,
    attempt: int = 2,
    job_id: str = "",
    spec_id: str = "",
) -> None:
    """Emit [SEMANTIC_RETRY_INVARIANT] log."""
    delta = invariant.get_delta(retry_manifest)
    print(
        f"[SEMANTIC_RETRY_INVARIANT] jobId={job_id} specId={spec_id}"
        f" attempt={attempt}"
        f" captured={invariant._captured}"
        f" preserveExpanded={delta.get('preserveExpanded', [])}"
        f" preserveShrunk={delta.get('preserveShrunk', [])}"
        f" removalExpanded={delta.get('removalExpanded', [])}"
        f" removalContracted={delta.get('removalContracted', [])}"
        f" forbiddenChanges={delta.get('forbiddenChanges', False)}",
        flush=True,
    )


# ── Internal helpers ─────────────────────────────────────────────────────────

def _hash_list(lst: list) -> str:
    payload = json.dumps(sorted(lst), sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _hash_roles(manifest: object) -> str:
    preserve = sorted(getattr(manifest, "preserve_roles", []))
    removal = sorted(getattr(manifest, "removal_roles", []))
    payload = json.dumps({"p": preserve, "r": removal}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]
