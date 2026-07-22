"""Stage 21 P0: Per-request AI render context with SHA-256 source provenance.

AiRenderContext is created once per (jobId, specId) pair at the start of
_generate_ai_only() and passed through the entire pipeline.  Every provider
call validates that the image bytes match the hash that was computed when the
source PSD was first loaded — this catches cross-job source contamination
regardless of where it enters the pipeline.

Security: source_file_sha256 / composite_sha256 are safe to log (hashes, not
keys).  AI API keys are never stored here.
"""
from __future__ import annotations

import hashlib
import io
import os
from dataclasses import dataclass, field
from typing import Optional

from PIL import Image


# ── Hash helpers ──────────────────────────────────────────────────────────────

def sha256_image(img: Image.Image) -> str:
    """SHA-256 of PNG-encoded RGB pixels (deterministic, size-independent)."""
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return hashlib.sha256(buf.getvalue()).hexdigest()


def sha256_file(path: str) -> str:
    """SHA-256 of raw file bytes.  Returns empty string on read failure."""
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except Exception:
        return ""


# ── Context dataclass ─────────────────────────────────────────────────────────

@dataclass
class AiRenderContext:
    """Request-scoped context that travels from _generate_ai_only → SFR → provider.

    All SHA-256 fields are computed at job start and verified before each
    provider call.  A mismatch raises RuntimeError with a CROSS_JOB_SOURCE_*
    prefix so the error propagates up cleanly without masking the original bug.
    """

    # Identity
    job_id: str = ""
    spec_id: str = ""          # e.g. "1200x628"
    source_path: str = ""

    # Provenance hashes (computed at job start, never mutated)
    source_file_sha256: str = ""     # SHA-256 of raw PSD file bytes
    composite_sha256: str = ""       # SHA-256 of flat composite PNG at original size

    # Geometry
    target_width: int = 0
    target_height: int = 0

    # Isolated work directory for this (job_id, spec_id) pair
    work_dir: str = ""

    # Per-attempt provenance (filled during SFR run)
    provider_input_sha256: str = ""
    ai_background_sha256: str = ""
    final_artifact_sha256: str = ""

    # Prompt provenance (filled in SFR attempt loop)
    prompt_sha256: str = ""
    prompt_version: str = ""
    prompt_contains_mother_hand_terms: bool = False

    # Attempt logs (list of per-attempt dicts, appended by SFR)
    attempt_provenance: list = field(default_factory=list)

    @classmethod
    def from_source(
        cls,
        job_id: str,
        spec_id: str,
        source_path: str,
        composite_image: Image.Image,
        target_w: int,
        target_h: int,
        work_dir: str = "",
    ) -> "AiRenderContext":
        """Factory: compute hashes from live objects at job start."""
        ctx = cls(
            job_id=job_id,
            spec_id=spec_id,
            source_path=source_path,
            source_file_sha256=sha256_file(source_path),
            composite_sha256=sha256_image(composite_image),
            target_width=target_w,
            target_height=target_h,
            work_dir=work_dir,
        )
        return ctx

    # ── Guard ────────────────────────────────────────────────────────────────

    def assert_source_integrity(self, candidate: Image.Image, label: str = "") -> None:
        """Verify candidate image SHA-256 matches composite_sha256.

        candidate is the image about to be sent to the AI provider (before
        resize) OR the source_image at the top of run_source_faithful_repair().

        If the hashes differ, raises RuntimeError with
        CROSS_JOB_SOURCE_CONTAMINATION prefix so callers can surface it as a
        hard failure without triggering the normal AI retry loop.
        """
        if not self.composite_sha256:
            return  # context was built without a composite (rare fallback)
        actual = sha256_image(candidate)
        if actual != self.composite_sha256:
            raise RuntimeError(
                f"CROSS_JOB_SOURCE_CONTAMINATION "
                f"job_id={self.job_id} spec_id={self.spec_id} label={label} "
                f"expected_sha256={self.composite_sha256[:16]} "
                f"actual_sha256={actual[:16]}"
            )

    def record_provider_input(self, provider_input: Image.Image) -> str:
        """Compute + store providerInputSha256 for the current attempt."""
        h = sha256_image(provider_input)
        self.provider_input_sha256 = h
        return h

    def record_ai_background(self, ai_bg: Image.Image) -> str:
        """Compute + store aiBackgroundSha256 for the accepted AI result."""
        h = sha256_image(ai_bg)
        self.ai_background_sha256 = h
        return h

    def record_final_artifact(self, final: Image.Image) -> str:
        """Compute + store finalArtifactSha256 after foreground compositing."""
        h = sha256_image(final)
        self.final_artifact_sha256 = h
        return h

    def record_prompt_provenance(
        self,
        prompt_sha256_hex: str,
        version: str,
        contains_mother_hand: bool,
    ) -> None:
        """Store prompt provenance fields from the SFR attempt loop."""
        self.prompt_sha256 = prompt_sha256_hex
        self.prompt_version = version
        self.prompt_contains_mother_hand_terms = contains_mother_hand

    # ── Debug artifacts ───────────────────────────────────────────────────────

    def save_debug_artifact(
        self,
        stage: str,
        img: Image.Image,
        label: str = "",
    ) -> Optional[str]:
        """Save a debug PNG to work_dir.  Silent on failure.

        stage: "01-source-composite", "02-provider-input", "03-gen-mask",
               "04-ai-background", "05-composited", "06-final"
        """
        if not self.work_dir:
            return None
        try:
            os.makedirs(self.work_dir, exist_ok=True)
            name = f"{stage}.png" if not label else f"{stage}-{label}.png"
            path = os.path.join(self.work_dir, name)
            img.convert("RGB").save(path, format="PNG")
            return path
        except Exception as e:
            print(f"[AiRenderContext] debug artifact save failed stage={stage}: {e}", flush=True)
            return None

    def provenance_dict(self) -> dict:
        """Serialisable provenance summary for renderProvenance field."""
        return {
            "jobId": self.job_id,
            "specId": self.spec_id,
            "sourcePath": os.path.basename(self.source_path),
            "sourceFileSha256": self.source_file_sha256[:16] if self.source_file_sha256 else "",
            "compositeSha256": self.composite_sha256[:16] if self.composite_sha256 else "",
            "providerInputSha256": self.provider_input_sha256[:16] if self.provider_input_sha256 else "",
            "aiBackgroundSha256": self.ai_background_sha256[:16] if self.ai_background_sha256 else "",
            "finalArtifactSha256": self.final_artifact_sha256[:16] if self.final_artifact_sha256 else "",
            "workDir": self.work_dir,
            "promptSha256": self.prompt_sha256[:16] if self.prompt_sha256 else "",
            "promptVersion": self.prompt_version,
            "promptContainsMotherHandTerms": self.prompt_contains_mother_hand_terms,
        }
