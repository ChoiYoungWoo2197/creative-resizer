"""D-2: Build source-aligned cleanup reference (job-level, once per job).

Calls D-1 semantic scene cleanup at source image dimensions so we get a clean
photographic background reference in source coordinate space.

This is DISTINCT from per-spec target scene plates (which are scaled to target dims).
Spec Section 6: D-1 target scene plate ≠ source reference — never confuse them.
"""
from __future__ import annotations

from PIL import Image

from scene_cleanup.semantic_scene_cleanup import run_semantic_scene_cleanup


def build_source_aligned_reference(
    *,
    source_image: Image.Image,
    source_path: str,
    source_type: str,
    source_file_sha256: str,
    composite_sha256: str,
    provider: object,
    output_dir: str,
    has_native_layers: bool = False,
    composite_render_method: str = "psd_composite",
    job_id: str = "",
) -> object:  # SemanticSceneCleanupResult
    """Run D-1 at source image dimensions → source-coordinate reference plate.

    Returns SemanticSceneCleanupResult.
    render_ctx=None is safe — the SSC pipeline guards all render_ctx usages.
    """
    src_w, src_h = source_image.size

    print(
        f"[D2_SOURCE_REFERENCE]"
        f" jobId={job_id} targetSize={src_w}x{src_h}"
        f" sourceSha256={source_file_sha256[:16]}",
        flush=True,
    )

    result = run_semantic_scene_cleanup(
        source_path=source_path,
        source_type=source_type,
        source_image=source_image,
        source_file_sha256=source_file_sha256,
        composite_sha256=composite_sha256,
        target_w=src_w,
        target_h=src_h,
        provider=provider,
        output_dir=output_dir,
        render_ctx=None,           # no per-spec render_ctx for job-level reference
        has_native_layers=has_native_layers,
        composite_render_method=composite_render_method,
        max_attempts=1,
        job_id=job_id,
        spec_id="source_reference",
    )

    if result.success:
        print(
            f"[D2_SOURCE_REFERENCE] OK jobId={job_id}"
            f" sha256={result.scene_plate_sha256[:16]}"
            f" providerRequests={result.actual_provider_request_count}",
            flush=True,
        )
    else:
        print(
            f"[D2_SOURCE_REFERENCE] FAIL jobId={job_id}"
            f" reason={result.failure_reason}",
            flush=True,
        )

    return result
