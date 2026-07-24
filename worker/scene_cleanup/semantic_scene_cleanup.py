"""Bundle D-1: Full-image semantic scene cleanup pipeline.

Input:  full advertisement composite (PSD rendered / PNG / JPG).
Output: clean photographic background plate at target dimensions.

Fail-closed: no legacy fallback, no Smart Fit, no blur fill,
             no mirror edge, no native fallback.
"""
from __future__ import annotations
import hashlib
import os
import time

from scene_cleanup.models import (
    SemanticSceneCleanupResult, PROVIDER_INPUT_FULL_COMPOSITE,
)


def _sha256_image(img: object) -> str:
    """SHA-256 of raw RGBA pixel bytes."""
    from PIL import Image
    if img is None or not isinstance(img, Image.Image):
        return ""
    rgba = img.convert("RGBA") if img.mode != "RGBA" else img
    return hashlib.sha256(rgba.tobytes()).hexdigest()


def _blank_check(img: object, *, job_id: str = "", spec_id: str = "") -> None:
    """Fail-closed: raise if image variance < 5.0 (blank / uniform fill)."""
    import numpy as np
    from PIL import Image
    if not isinstance(img, Image.Image):
        raise RuntimeError("SEMANTIC_SCENE_PLATE_NONE: provider returned non-Image")
    arr = np.array(img.convert("RGB"), dtype=np.float32)
    variance = float(arr.var())
    if variance < 5.0:
        raise RuntimeError(
            f"SEMANTIC_SCENE_PLATE_BLANK: variance={variance:.2f} < 5.0"
            f" jobId={job_id} specId={spec_id}"
        )


def run_semantic_scene_cleanup(
    *,
    source_path: str,
    source_type: str,
    source_image: object,
    source_file_sha256: str,
    composite_sha256: str,
    target_w: int,
    target_h: int,
    provider: object,
    output_dir: str,
    render_ctx: object,
    has_native_layers: bool,
    composite_render_method: str,
    max_attempts: int = 1,
    job_id: str = "",
    spec_id: str = "",
) -> SemanticSceneCleanupResult:
    """Run full-image semantic scene cleanup (Bundle D-1).

    Steps:
      1. Build FullImageSource (validate full composite)
      2. Detect flattened input (d2_required)
      3. Cover-crop to target + full-white mask
      4. Build semantic prompt (SHA guard)
      5. Record provider input SHA in render_ctx
      6. Call provider.inpaint() — fail-closed, no legacy fallback
      7. normalize_provider_result()
      8. Blank check
      9. Save scene plate, compute scene_plate_sha256
      10. Return SemanticSceneCleanupResult
    """
    from scene_cleanup.full_image_source import build_full_image_source
    from scene_cleanup.canvas_builder import build_provider_canvas
    from scene_cleanup.prompt_builder import build_semantic_prompt, _TEMPLATE_SHA256
    from background.external_provider import normalize_provider_result
    from PIL import Image

    attempt_count = 0
    actual_provider_request_count = 0

    # Flattened input: PNG/JPG without native layers requires D-2 (not implemented)
    d2_required = (not has_native_layers) and (source_type not in ("psd",))
    d2_reason = ""
    if d2_required:
        d2_reason = (
            f"source_type={source_type!r} has_native_layers=False — "
            "D-2 segmentation required for foreground re-extraction (not implemented in D-1)"
        )
        print(
            f"[SSC_D2_REQUIRED] jobId={job_id} specId={spec_id}"
            f" sourceType={source_type!r} hasNativeLayers={has_native_layers}",
            flush=True,
        )

    try:
        # 1: Validate full image source
        full_source = build_full_image_source(
            source_image=source_image,
            source_path=source_path,
            source_file_sha256=source_file_sha256,
            composite_sha256=composite_sha256,
            source_type=source_type,
            has_native_layers=has_native_layers,
            composite_render_method=composite_render_method,
        )

        # 3: Cover-crop to target + full-white mask
        provider_input, mask, canvas_transform = build_provider_canvas(
            full_source, target_w, target_h
        )

        # 4: Build prompt (SHA guard runs inside)
        prompt, prompt_version = build_semantic_prompt(target_w, target_h)
        prompt_sha256 = _TEMPLATE_SHA256

        # 5: Record provider input SHA
        provider_input_sha = _sha256_image(provider_input)
        if render_ctx is not None:
            if hasattr(render_ctx, "record_provider_input_sha256"):
                render_ctx.record_provider_input_sha256(provider_input_sha)
            elif hasattr(render_ctx, "_provider_input_sha256"):
                render_ctx._provider_input_sha256 = provider_input_sha

        if render_ctx is not None and hasattr(render_ctx, "save_debug_artifact"):
            render_ctx.save_debug_artifact("02-ssc-provider-input", provider_input)
            render_ctx.save_debug_artifact("02-ssc-mask", mask)

        print(
            f"[SSC_START] jobId={job_id} specId={spec_id}"
            f" sourceType={source_type!r}"
            f" source={full_source.width}x{full_source.height}"
            f" target={target_w}x{target_h}"
            f" scale={canvas_transform.scale:.4f}"
            f" cropX={canvas_transform.crop_x} cropY={canvas_transform.crop_y}"
            f" promptVersion={prompt_version}"
            f" d2Required={d2_required}",
            flush=True,
        )

        # 6: Call provider — fail-closed, no fallback
        scene_plate_img = None
        provider_name = "unknown"
        n_attempts = max(max_attempts, 1)
        _request_id = f"{job_id}_{spec_id}" if (job_id or spec_id) else "unknown"
        _meta_provider_name = "unknown"
        if hasattr(provider, "metadata"):
            try:
                _meta_provider_name = (provider.metadata() or {}).get("providerName", "unknown")
            except Exception:
                pass

        for attempt in range(n_attempts):
            attempt_count = attempt + 1
            actual_provider_request_count += 1

            t_attempt = time.time()
            print(
                f"[AI_PROVIDER_START] requestId={_request_id}"
                f" attempt={attempt_count}/{n_attempts}"
                f" provider={_meta_provider_name}"
                f" target={target_w}x{target_h}",
                flush=True,
            )

            try:
                raw_result = provider.inpaint(
                    provider_input, mask, prompt,
                    {"prompt_version": prompt_version},
                )
            except Exception as _prov_err:
                _attempt_elapsed_ms = int((time.time() - t_attempt) * 1000)
                print(
                    f"[AI_PROVIDER_END] requestId={_request_id}"
                    f" attempt={attempt_count}/{n_attempts}"
                    f" provider={_meta_provider_name}"
                    f" elapsedMs={_attempt_elapsed_ms}"
                    f" success=False",
                    flush=True,
                )
                if attempt < n_attempts - 1:
                    print(
                        f"[SSC_ATTEMPT_FAIL] jobId={job_id}"
                        f" attempt={attempt + 1}/{n_attempts}: {_prov_err}",
                        flush=True,
                    )
                    continue
                raise RuntimeError(
                    f"SEMANTIC_PROVIDER_FAILED after {attempt_count} attempts: {_prov_err}"
                ) from _prov_err

            _attempt_elapsed_ms = int((time.time() - t_attempt) * 1000)
            print(
                f"[AI_PROVIDER_END] requestId={_request_id}"
                f" attempt={attempt_count}/{n_attempts}"
                f" provider={_meta_provider_name}"
                f" elapsedMs={_attempt_elapsed_ms}"
                f" success=True",
                flush=True,
            )

            # 7: normalize
            scene_plate_img, provider_name = normalize_provider_result(raw_result)
            if scene_plate_img is not None:
                break

        if scene_plate_img is None:
            raise RuntimeError(
                f"SEMANTIC_PROVIDER_ALL_ATTEMPTS_FAILED: attempts={attempt_count}"
            )

        # 8: Blank check — fail-closed
        _blank_check(scene_plate_img, job_id=job_id, spec_id=spec_id)

        # Resize if provider returned wrong dimensions
        if scene_plate_img.size != (target_w, target_h):
            scene_plate_img = scene_plate_img.resize((target_w, target_h), Image.LANCZOS)

        # 9: Save scene plate + compute SHA
        os.makedirs(output_dir, exist_ok=True)
        plate_path = os.path.join(
            output_dir, f"ssc_scene_plate_{target_w}x{target_h}.png"
        )
        scene_plate_img.save(plate_path)
        scene_plate_sha = _sha256_image(scene_plate_img)

        if render_ctx is not None and hasattr(render_ctx, "save_debug_artifact"):
            render_ctx.save_debug_artifact("03-ssc-scene-plate", scene_plate_img)

        # Resolve provider model if available
        provider_model = ""
        for attr in ("model", "_model", "model_name"):
            if hasattr(provider, attr):
                v = getattr(provider, attr)
                if v:
                    provider_model = str(v)
                    break

        print(
            f"[SSC_END] jobId={job_id} specId={spec_id}"
            f" success=True"
            f" providerName={provider_name!r}"
            f" scenePlateSha={scene_plate_sha[:16]}"
            f" attempts={attempt_count}"
            f" d2Required={d2_required}",
            flush=True,
        )

        return SemanticSceneCleanupResult(
            success=True,
            failure_reason="",
            provider_name=provider_name,
            provider_model=provider_model,
            provider_input_source=PROVIDER_INPUT_FULL_COMPOSITE,
            prompt_version=prompt_version,
            prompt_sha256=prompt_sha256,
            scene_plate_sha256=scene_plate_sha,
            scene_plate_image=scene_plate_img,
            scene_plate_path=plate_path,
            canvas_transform=canvas_transform,
            attempt_count=attempt_count,
            actual_provider_request_count=actual_provider_request_count,
            d2_required=d2_required,
            d2_reason=d2_reason,
            source_w=full_source.width,
            source_h=full_source.height,
            target_w=target_w,
            target_h=target_h,
        )

    except Exception as err:
        print(
            f"[SSC_FAILED] jobId={job_id} specId={spec_id} error={err}",
            flush=True,
        )
        return SemanticSceneCleanupResult(
            success=False,
            failure_reason=str(err),
            attempt_count=attempt_count,
            actual_provider_request_count=actual_provider_request_count,
            d2_required=d2_required,
            d2_reason=d2_reason,
            target_w=target_w,
            target_h=target_h,
        )
