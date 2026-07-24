"""Stage E P0-D: Semantic cache key validation.

Defines required fields for a semantic cache key and rejects stale/incompatible
cached analysis from legacy pipeline versions.

Incompatible cache key versions (always rejected):
  - psd-object-map-v1
  - psd-object-map-v2
  - source-faithful-repair-*
  - legacy-object-map-*

Compatible versions (full-image semantic pipeline):
  pipelinePolicy:    full-image-semantic-v1
  manifestVersion:   unified-semantic-v1
  analysisVersion:   full-image-semantic-v1
  maskPolicyVersion: default-immutable-v1

Logs:
  [SEMANTIC_CACHE_REJECT] with reason, cachedVersion, requiredVersion, SHAs
"""
from __future__ import annotations

from dataclasses import dataclass, field


# ── Version constants ─────────────────────────────────────────────────────────

REQUIRED_PIPELINE_POLICY = "full-image-semantic-v1"
REQUIRED_MANIFEST_VERSION = "unified-semantic-v1"
REQUIRED_ANALYSIS_VERSION = "full-image-semantic-v1"
REQUIRED_MASK_POLICY_VERSION = "default-immutable-v1"

INCOMPATIBLE_PREFIXES = (
    "psd-object-map-v1",
    "psd-object-map-v2",
    "source-faithful-repair",
    "legacy-object-map",
)

# Required fields in a SemanticCacheKey
REQUIRED_CACHE_KEY_FIELDS = (
    "canonicalImageSha256",
    "pipelinePolicy",
    "manifestVersion",
    "analysisVersion",
    "analysisPromptVersion",
    "maskPolicyVersion",
    "sourceNormalizationVersion",
    "semanticRoleSchemaVersion",
    "model",
    "modelConfigHash",
)


@dataclass
class SemanticCacheKey:
    """Cache key for semantic analysis results.

    All 10 fields are required. Missing any field makes this key invalid.
    Version mismatches trigger cache rejection.
    """
    canonicalImageSha256: str = ""
    pipelinePolicy: str = ""
    manifestVersion: str = ""
    analysisVersion: str = ""
    analysisPromptVersion: str = ""
    maskPolicyVersion: str = ""
    sourceNormalizationVersion: str = ""
    semanticRoleSchemaVersion: str = ""
    model: str = ""
    modelConfigHash: str = ""

    def is_complete(self) -> bool:
        """True when all required fields are populated."""
        return all(getattr(self, f, "") for f in REQUIRED_CACHE_KEY_FIELDS)

    def missing_fields(self) -> list[str]:
        """Return list of required fields that are empty."""
        return [f for f in REQUIRED_CACHE_KEY_FIELDS if not getattr(self, f, "")]

    def is_legacy(self) -> tuple[bool, str]:
        """True when any version field matches a known legacy/incompatible prefix.

        Returns (is_legacy, matching_prefix).
        """
        for f in ("pipelinePolicy", "analysisVersion", "manifestVersion", "maskPolicyVersion"):
            val = getattr(self, f, "")
            for prefix in INCOMPATIBLE_PREFIXES:
                if val.startswith(prefix):
                    return True, f"{f}={val!r} matches incompatible prefix {prefix!r}"
        return False, ""


def validate_cache_hit(
    cached_key: SemanticCacheKey,
    current_key: SemanticCacheKey,
) -> tuple[bool, str]:
    """Validate whether a cached analysis can be reused.

    Args:
        cached_key: the SemanticCacheKey stored with the cached analysis
        current_key: the SemanticCacheKey for the current job request

    Returns:
        (valid: bool, reject_reason: str)
        valid=True means the cache hit is accepted.
        valid=False + reason means it must be rejected.
    """
    # 1. SHA must match
    if cached_key.canonicalImageSha256 != current_key.canonicalImageSha256:
        return False, (
            f"SHA_MISMATCH:"
            f" cached={cached_key.canonicalImageSha256[:16]!r}"
            f" current={current_key.canonicalImageSha256[:16]!r}"
        )

    # 2. Reject if cached key is legacy
    is_leg, leg_reason = cached_key.is_legacy()
    if is_leg:
        return False, f"LEGACY_VERSION_REJECTED: {leg_reason}"

    # 3. Reject if current key requires a different version than cached
    version_checks = [
        ("pipelinePolicy", REQUIRED_PIPELINE_POLICY),
        ("manifestVersion", REQUIRED_MANIFEST_VERSION),
        ("analysisVersion", REQUIRED_ANALYSIS_VERSION),
        ("maskPolicyVersion", REQUIRED_MASK_POLICY_VERSION),
    ]
    for field_name, required_val in version_checks:
        cached_val = getattr(cached_key, field_name, "")
        current_val = getattr(current_key, field_name, "")
        if cached_val and current_val and cached_val != current_val:
            return False, (
                f"VERSION_MISMATCH: field={field_name}"
                f" cached={cached_val!r}"
                f" required={current_val!r}"
            )

    # 4. Cached key must be complete (all fields populated)
    missing = cached_key.missing_fields()
    if missing:
        return False, f"INCOMPLETE_CACHE_KEY: missing={missing}"

    return True, ""


def log_cache_reject(
    reason: str,
    *,
    cached_version: str = "",
    required_version: str = "",
    cached_canonical_sha: str = "",
    current_canonical_sha: str = "",
    job_id: str = "",
) -> None:
    """Emit [SEMANTIC_CACHE_REJECT] log."""
    print(
        f"[SEMANTIC_CACHE_REJECT] jobId={job_id}"
        f" reason={reason!r}"
        f" cachedVersion={cached_version!r}"
        f" requiredVersion={required_version!r}"
        f" cachedCanonicalSha={cached_canonical_sha[:16]!r}"
        f" currentCanonicalSha={current_canonical_sha[:16]!r}",
        flush=True,
    )
