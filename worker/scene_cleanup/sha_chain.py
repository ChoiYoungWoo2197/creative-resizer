"""Stage E P0-C: Canonical SHA chain — single canonical image authority tracking.

Tracks SHA-256 hashes of the canonical image at each pipeline stage:
  - analysisSha:     SHA of image used for semantic analysis (D-2)
  - manifestSha:     SHA recorded in the SemanticManifest
  - extractionSha:   SHA of image used for foreground extraction
  - generationSha:   SHA of image used for AI scene generation

All SHAs must match the canonical source SHA. Any mismatch means a different
image was used at some stage, which violates the single-canonical-authority rule.

Raises:
  RuntimeError("CANONICAL_SOURCE_HASH_MISMATCH: ...") on validate_chain failure
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CanonicalSHAChain:
    """Tracks SHA-256 of canonical image through pipeline stages.

    All SHAs must equal the canonical image SHA. If any differ, a different
    image was substituted at that stage — a critical invariant violation.
    """
    canonical_sha: str = ""    # reference: the one true canonical image SHA
    analysis_sha: str = ""     # SHA when D-2 analysis ran
    manifest_sha: str = ""     # SHA recorded in SemanticManifest
    extraction_sha: str = ""   # SHA when foreground extraction ran
    generation_sha: str = ""   # SHA when scene generation ran

    def record_analysis(self, sha: str) -> None:
        self.analysis_sha = sha

    def record_manifest(self, sha: str) -> None:
        self.manifest_sha = sha

    def record_extraction(self, sha: str) -> None:
        self.extraction_sha = sha

    def record_generation(self, sha: str) -> None:
        self.generation_sha = sha

    def validate_chain(self) -> list[str]:
        """Validate that all recorded SHAs match the canonical SHA.

        Returns list of mismatch descriptions (empty = all valid).
        Skips stages where SHA was not recorded (empty string).
        """
        if not self.canonical_sha:
            return ["CANONICAL_SHA_NOT_SET"]

        mismatches: list[str] = []

        checks = [
            ("analysis", self.analysis_sha),
            ("manifest", self.manifest_sha),
            ("extraction", self.extraction_sha),
            ("generation", self.generation_sha),
        ]
        for stage, sha in checks:
            if sha and sha != self.canonical_sha:
                mismatches.append(
                    f"CANONICAL_SOURCE_HASH_MISMATCH:"
                    f" stage={stage}"
                    f" expected={self.canonical_sha[:16]}"
                    f" actual={sha[:16]}"
                )

        return mismatches

    def all_match(self) -> bool:
        """True when all recorded SHAs match canonical (or are unset)."""
        return len(self.validate_chain()) == 0

    def validate_or_raise(self, job_id: str = "", spec_id: str = "") -> None:
        """Raise RuntimeError if any SHA mismatch detected."""
        mismatches = self.validate_chain()
        if mismatches:
            raise RuntimeError(
                f"CANONICAL_SOURCE_HASH_MISMATCH: jobId={job_id} specId={spec_id}"
                f" mismatches={mismatches}"
            )


def log_sha_chain(chain: CanonicalSHAChain, *, job_id: str = "", spec_id: str = "") -> None:
    """Emit [CANONICAL_SHA_CHAIN] log."""
    mismatches = chain.validate_chain()
    all_matched = len(mismatches) == 0
    print(
        f"[CANONICAL_SHA_CHAIN] jobId={job_id} specId={spec_id}"
        f" allMatched={str(all_matched).lower()}"
        f" canonicalSha={chain.canonical_sha[:16]!r}"
        f" analysisSha={chain.analysis_sha[:16]!r}"
        f" manifestSha={chain.manifest_sha[:16]!r}"
        f" extractionSha={chain.extraction_sha[:16]!r}"
        f" generationSha={chain.generation_sha[:16]!r}"
        f" mismatches={mismatches}",
        flush=True,
    )


def build_sha_chain_from_canonical(canonical_sha: str) -> CanonicalSHAChain:
    """Build a CanonicalSHAChain initialized with the canonical SHA."""
    return CanonicalSHAChain(canonical_sha=canonical_sha)
