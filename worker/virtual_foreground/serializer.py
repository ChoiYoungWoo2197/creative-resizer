"""D-2: Serialize VirtualForegroundExtractionResult for renderProvenance."""
from __future__ import annotations

from virtual_foreground.models import VirtualForegroundExtractionResult


def extract_d2_provenance_fields(
    result: VirtualForegroundExtractionResult | None,
) -> dict:
    """Extract D-2 provenance fields for inclusion in renderProvenance."""
    if result is None:
        return {
            "d2VirtualForegroundApplicable": False,
            "d2VirtualForegroundSucceeded": False,
            "d2VirtualDetectedCount": 0,
            "d2VirtualExtractedCount": 0,
            "d2VirtualRejectedCount": 0,
            "d2FinalRecompositionPossible": False,
            "d2SourceAlignedReferenceSha256": "",
            "d2ProviderRequestCount": 0,
            "d2Reason": "",
            "d2Implemented": True,
        }

    ref_sha = result.source_aligned_reference_sha256 or ""
    return {
        "d2VirtualForegroundApplicable": result.d2_applicable,
        "d2VirtualForegroundSucceeded": bool(
            result.success and result.d2_applicable
        ),
        "d2VirtualDetectedCount": result.detected_object_count,
        "d2VirtualExtractedCount": result.virtual_extracted_count,
        "d2VirtualRejectedCount": result.virtual_rejected_count,
        "d2FinalRecompositionPossible": result.final_recomposition_possible,
        "d2SourceAlignedReferenceSha256": ref_sha[:16] if ref_sha else "",
        "d2ProviderRequestCount": result.provider_request_count,
        "d2Reason": result.d2_reason,
        "d2Implemented": result.d2_implemented,
    }
