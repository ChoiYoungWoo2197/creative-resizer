"""Stage E: Non-invasive diagnostic logging for production job failures.

All public functions are pure log emitters:
  - Never raise (all wrapped in try/except)
  - Never modify state
  - Never log raw image data or API keys
  - Output structured [TAG] lines readable by log aggregators
  - Mask data: SHA-256 prefix, coverage, bbox only — no raw pixels saved

Log tags emitted:
  [SEMANTIC_OBJECT]        per-object in fg_layers after D-2 scaling
  [SEMANTIC_OBJECT_REJECT] fg objects absent from final manifest (required missing)
  [SEMANTIC_INVENTORY]     full role inventory: detected / required / extracted
  [MASK_LINEAGE]           per-mask stats: type, step, size, coverage, sha256
  [MASK_ANOMALY]           detected anomalous mask conditions
  [TRANSFORM_GEOMETRY]     canvas transform geometry audit
  [PIXEL_RESTORE_AUDIT]    pixel restoration breakdown
  [PIXEL_RESTORE_SKIPPED]  why restoration produced 0 restored pixels
  [MANIFEST_AUDIT]         manifest completeness audit
  [RESULT_SEMANTICS]       result counters and status flags
  [ROOT_CAUSE_SUMMARY]     synthesized failure analysis
"""
from __future__ import annotations

import hashlib


# ── Private helpers ───────────────────────────────────────────────────────────

def _sha256_arr(arr: object) -> str:
    """SHA-256[:16] of numpy array raw bytes; empty string on error."""
    try:
        return hashlib.sha256(arr.tobytes()).hexdigest()[:16]
    except Exception:
        return ""


def _bbox_of_nonzero(arr: object) -> dict:
    """Bounding box enclosing all non-zero pixels in a 2-D uint8 array."""
    try:
        import numpy as np
        rows = np.any(arr > 0, axis=1)
        cols = np.any(arr > 0, axis=0)
        if not rows.any():
            return {"x1": None, "y1": None, "x2": None, "y2": None}
        y1 = int(rows.argmax())
        y2 = int(len(rows) - rows[::-1].argmax() - 1)
        x1 = int(cols.argmax())
        x2 = int(len(cols) - cols[::-1].argmax() - 1)
        return {"x1": x1, "y1": y1, "x2": x2, "y2": y2}
    except Exception:
        return {"x1": None, "y1": None, "x2": None, "y2": None}


def _mask_stats(arr: object, mask_type: str, step: str, target_w: int, target_h: int) -> dict:
    """Return serialisable stats for a mask array (no pixel data)."""
    try:
        if arr is None or not hasattr(arr, "shape"):
            return {
                "maskType": mask_type, "step": step,
                "error": "null_or_non_array",
            }
        h, w = arr.shape[:2]
        total = h * w
        non_zero = int((arr > 0).sum())
        coverage = round(non_zero / total, 6) if total > 0 else 0.0
        return {
            "maskType": mask_type,
            "step": step,
            "size": f"{w}x{h}",
            "targetSize": f"{target_w}x{target_h}",
            "sizeMatch": (w == target_w and h == target_h),
            "nonZeroPixels": non_zero,
            "totalPixels": total,
            "coverage": coverage,
            "bbox": _bbox_of_nonzero(arr),
            "sha256": _sha256_arr(arr),
        }
    except Exception as e:
        return {"maskType": mask_type, "step": step, "error": str(e)}


def _layer_role(layer: dict) -> str:
    return (
        layer.get("role")
        or layer.get("semanticRole")
        or layer.get("semantic_role")
        or ""
    )


def _layer_objectid(layer: dict) -> str:
    return layer.get("objectId") or layer.get("object_id") or ""


# ── Public log functions ──────────────────────────────────────────────────────

def log_semantic_objects(
    fg_layers: list,
    *,
    job_id: str = "",
    spec_id: str = "",
) -> None:
    """Emit [SEMANTIC_OBJECT] for every layer in fg_layers."""
    try:
        if not fg_layers:
            print(
                f"[SEMANTIC_OBJECT] jobId={job_id} specId={spec_id}"
                f" count=0 note=no_fg_layers",
                flush=True,
            )
            return
        for layer in fg_layers:
            if not isinstance(layer, dict):
                continue
            obj_id = _layer_objectid(layer)
            role = _layer_role(layer)
            confidence = float(layer.get("confidence") or layer.get("extraction_confidence") or 0.0)
            bbox = layer.get("bbox") or {}
            required = bool(layer.get("required", False))
            immutable = bool(layer.get("immutable", False))
            remove_from_scene = bool(layer.get("removeFromScene", False))
            recompose = bool(layer.get("recompose", False))
            semantic_evidence = layer.get("semanticEvidence") or layer.get("semantic_evidence") or []
            mask_ref = layer.get("maskRef") or layer.get("mask_sha256") or ""
            print(
                f"[SEMANTIC_OBJECT] jobId={job_id} specId={spec_id}"
                f" objectId={obj_id!r}"
                f" role={role!r}"
                f" confidence={confidence:.4f}"
                f" bbox={bbox}"
                f" required={required}"
                f" immutable={immutable}"
                f" removeFromScene={remove_from_scene}"
                f" recompose={recompose}"
                f" semanticEvidence={semantic_evidence}"
                f" maskRef={mask_ref!r}",
                flush=True,
            )
    except Exception as e:
        print(f"[DIAG_LOG_ERROR] log_semantic_objects: {e}", flush=True)


def log_semantic_object_reject(
    layer: dict,
    reason_codes: list,
    mask_metrics: dict,
    fail_closed: bool,
    *,
    job_id: str = "",
    spec_id: str = "",
) -> None:
    """Emit [SEMANTIC_OBJECT_REJECT] for a layer rejected during processing."""
    try:
        obj_id = _layer_objectid(layer)
        role = _layer_role(layer)
        required = bool(layer.get("required", False))
        mm = mask_metrics or {}
        print(
            f"[SEMANTIC_OBJECT_REJECT] jobId={job_id} specId={spec_id}"
            f" objectId={obj_id!r}"
            f" role={role!r}"
            f" required={required}"
            f" reasonCodes={reason_codes}"
            f" maskCoverage={mm.get('coverage', 0.0):.4f}"
            f" maskNonZeroPixels={mm.get('nonZeroPixels', 0)}"
            f" failClosed={fail_closed}",
            flush=True,
        )
    except Exception as e:
        print(f"[DIAG_LOG_ERROR] log_semantic_object_reject: {e}", flush=True)


def log_semantic_inventory(
    fg_layers: list,
    manifest: object,
    *,
    job_id: str = "",
    spec_id: str = "",
) -> None:
    """Emit [SEMANTIC_INVENTORY] and [SEMANTIC_OBJECT_REJECT] for missing required roles."""
    try:
        detected_roles = sorted(set(
            _layer_role(l)
            for l in (fg_layers or [])
            if isinstance(l, dict) and _layer_role(l)
        ))

        extracted_roles: list = []
        required_roles: list = []

        if manifest is not None:
            if hasattr(manifest, "objects"):
                # UnifiedObjectManifest
                for obj in (manifest.objects or []):
                    r = getattr(obj, "semanticRole", "") or getattr(obj, "role", "") or ""
                    if r:
                        extracted_roles.append(r)
                    if getattr(obj, "required", False) and r:
                        required_roles.append(r)
            elif hasattr(manifest, "preserve_roles"):
                # SemanticManifest
                extracted_roles = list(getattr(manifest, "preserve_roles", []))
                required_roles = list(getattr(manifest, "preserve_roles", []))

        extracted_roles = sorted(set(extracted_roles))
        required_roles = sorted(set(required_roles))
        extracted_set = set(extracted_roles)

        missing_required = sorted(r for r in required_roles if r not in extracted_set)
        rejected_required: list = []

        print(
            f"[SEMANTIC_INVENTORY] jobId={job_id} specId={spec_id}"
            f" detectedRoles={detected_roles}"
            f" requiredRoles={required_roles}"
            f" extractedRoles={extracted_roles}"
            f" missingRequiredRoles={missing_required}"
            f" rejectedRequiredRoles={rejected_required}"
            f" detectedCount={len(detected_roles)}"
            f" extractedCount={len(extracted_roles)}"
            f" missingCount={len(missing_required)}",
            flush=True,
        )

        # Emit REJECT for each detected layer whose required role is missing from manifest
        for layer in (fg_layers or []):
            if not isinstance(layer, dict):
                continue
            if _layer_role(layer) in missing_required:
                log_semantic_object_reject(
                    layer,
                    reason_codes=["ROLE_MISSING_FROM_MANIFEST"],
                    mask_metrics={},
                    fail_closed=True,
                    job_id=job_id,
                    spec_id=spec_id,
                )

    except Exception as e:
        print(f"[DIAG_LOG_ERROR] log_semantic_inventory: {e}", flush=True)


def log_mask_lineage(
    allowed_mask_arr: object,
    target_w: int,
    target_h: int,
    *,
    job_id: str = "",
    spec_id: str = "",
) -> None:
    """Emit [MASK_LINEAGE] for each mask derived from allowed_generation_mask."""
    try:
        import numpy as np

        if allowed_mask_arr is None:
            print(
                f"[MASK_LINEAGE] jobId={job_id} specId={spec_id}"
                f" maskType=allowedGenerationMask step=stage4_pixel_restore"
                f" size=none nonZeroPixels=none coverage=none bbox=None sha256=''"
                f" inputMasks=[] fallbackApplied=True"
                f" generatedAt=stage5_outpaint",
                flush=True,
            )
            return

        arr = np.asarray(allowed_mask_arr, dtype=np.uint8)
        if arr.ndim == 3:
            arr = arr[:, :, 0]

        # allowedGenerationMask: white=AI may edit, black=immutable source region
        s = _mask_stats(arr, "allowedGenerationMask", "stage4_pixel_restore", target_w, target_h)
        print(
            f"[MASK_LINEAGE] jobId={job_id} specId={spec_id}"
            f" maskType={s['maskType']}"
            f" step={s['step']}"
            f" size={s.get('size', '?')}"
            f" nonZeroPixels={s.get('nonZeroPixels', '?')}"
            f" coverage={s.get('coverage', '?')}"
            f" bbox={s.get('bbox')}"
            f" sha256={s.get('sha256', '')!r}"
            f" inputMasks=[]"
            f" fallbackApplied={not s.get('sizeMatch', True)}"
            f" generatedAt=stage5_outpaint",
            flush=True,
        )

        # sourceMappedRegionMask = pixels where allowed_mask is 0 (source preserved)
        source_mapped = np.where(arr == 0, np.uint8(255), np.uint8(0))
        s2 = _mask_stats(source_mapped, "sourceMappedRegionMask", "stage5_outpaint", target_w, target_h)
        print(
            f"[MASK_LINEAGE] jobId={job_id} specId={spec_id}"
            f" maskType={s2['maskType']}"
            f" step={s2['step']}"
            f" size={s2.get('size', '?')}"
            f" nonZeroPixels={s2.get('nonZeroPixels', '?')}"
            f" coverage={s2.get('coverage', '?')}"
            f" bbox={s2.get('bbox')}"
            f" sha256={s2.get('sha256', '')!r}"
            f" inputMasks=['allowedGenerationMask']"
            f" fallbackApplied=False"
            f" generatedAt=stage4_pixel_restore",
            flush=True,
        )

        # newCanvasRegionMask = outpaint regions (AI fills here)
        s3 = _mask_stats(arr, "newCanvasRegionMask", "stage5_outpaint", target_w, target_h)
        print(
            f"[MASK_LINEAGE] jobId={job_id} specId={spec_id}"
            f" maskType={s3['maskType']}"
            f" step={s3['step']}"
            f" size={s3.get('size', '?')}"
            f" nonZeroPixels={s3.get('nonZeroPixels', '?')}"
            f" coverage={s3.get('coverage', '?')}"
            f" bbox={s3.get('bbox')}"
            f" sha256={s3.get('sha256', '')!r}"
            f" inputMasks=['allowedGenerationMask']"
            f" fallbackApplied=False"
            f" generatedAt=stage5_outpaint",
            flush=True,
        )

        # immutableMask = same region as sourceMappedRegionMask
        s4 = _mask_stats(source_mapped, "immutableMask", "stage4_pixel_restore", target_w, target_h)
        print(
            f"[MASK_LINEAGE] jobId={job_id} specId={spec_id}"
            f" maskType={s4['maskType']}"
            f" step={s4['step']}"
            f" size={s4.get('size', '?')}"
            f" nonZeroPixels={s4.get('nonZeroPixels', '?')}"
            f" coverage={s4.get('coverage', '?')}"
            f" bbox={s4.get('bbox')}"
            f" sha256={s4.get('sha256', '')!r}"
            f" inputMasks=['sourceMappedRegionMask']"
            f" fallbackApplied=False"
            f" generatedAt=stage4_pixel_restore",
            flush=True,
        )

        # removalMask: not applicable in semantic/outpaint path
        print(
            f"[MASK_LINEAGE] jobId={job_id} specId={spec_id}"
            f" maskType=removalMask"
            f" step=stage4_pixel_restore"
            f" size=N/A nonZeroPixels=N/A coverage=N/A bbox=None sha256=''"
            f" inputMasks=[] fallbackApplied=False"
            f" generatedAt=N/A"
            f" note=not_applicable_in_semantic_outpaint_path",
            flush=True,
        )

        # restoreMask: computed at runtime from canonical vs AI diff — logged by PIXEL_RESTORE_AUDIT
        print(
            f"[MASK_LINEAGE] jobId={job_id} specId={spec_id}"
            f" maskType=restoreMask"
            f" step=stage4_pixel_restore"
            f" size=computed_at_runtime nonZeroPixels=see_PIXEL_RESTORE_AUDIT"
            f" coverage=see_PIXEL_RESTORE_AUDIT bbox=None sha256=''"
            f" inputMasks=['sourceMappedRegionMask','aiResultImage']"
            f" fallbackApplied=False"
            f" generatedAt=stage4_pixel_restore",
            flush=True,
        )

    except Exception as e:
        print(f"[DIAG_LOG_ERROR] log_mask_lineage: {e}", flush=True)


def log_mask_anomalies(
    allowed_mask_arr: object,
    immutable_metrics: dict,
    target_w: int,
    target_h: int,
    *,
    job_id: str = "",
    spec_id: str = "",
) -> None:
    """Emit [MASK_ANOMALY] for each detected anomalous mask condition."""
    try:
        import numpy as np

        if allowed_mask_arr is None:
            print(
                f"[MASK_ANOMALY] jobId={job_id} specId={spec_id}"
                f" maskType=allowedGenerationMask"
                f" anomalyCode=MASK_IS_NONE"
                f" actualValue=None"
                f" expectedRange=numpy_uint8_array",
                flush=True,
            )

        else:
            arr = np.asarray(allowed_mask_arr, dtype=np.uint8)
            if arr.ndim == 3:
                arr = arr[:, :, 0]
            h, w = arr.shape[:2]
            total = h * w if h * w > 0 else 1

            allowed_count = int((arr > 0).sum())
            source_count = int((arr == 0).sum())
            allowed_coverage = allowed_count / total
            source_coverage = source_count / total

            # Mask size mismatch vs target
            if w != target_w or h != target_h:
                print(
                    f"[MASK_ANOMALY] jobId={job_id} specId={spec_id}"
                    f" maskType=allowedGenerationMask"
                    f" anomalyCode=MASK_SIZE_MISMATCH"
                    f" actualValue={w}x{h}"
                    f" expectedRange={target_w}x{target_h}",
                    flush=True,
                )

            # sourceMappedCoverage=0 means no source region preserved
            if source_coverage == 0.0:
                print(
                    f"[MASK_ANOMALY] jobId={job_id} specId={spec_id}"
                    f" maskType=sourceMappedRegionMask"
                    f" anomalyCode=SOURCE_MAPPED_ZERO"
                    f" actualValue={source_coverage:.4f}"
                    f" expectedRange=>0.0"
                    f" note=mask_is_all_white_no_source_region_preserved",
                    flush=True,
                )

            # newCanvasCoverage=0 means no outpaint region (unusual in outpaint mode)
            if allowed_coverage == 0.0:
                print(
                    f"[MASK_ANOMALY] jobId={job_id} specId={spec_id}"
                    f" maskType=newCanvasRegionMask"
                    f" anomalyCode=NEW_CANVAS_ZERO"
                    f" actualValue={allowed_coverage:.4f}"
                    f" expectedRange=>0.0"
                    f" note=mask_is_all_black_no_outpaint_region",
                    flush=True,
                )

            # allowedGenerationCoverage >= 0.95 means almost no immutable protection
            if allowed_coverage >= 0.95:
                print(
                    f"[MASK_ANOMALY] jobId={job_id} specId={spec_id}"
                    f" maskType=allowedGenerationMask"
                    f" anomalyCode=ALLOWED_GEN_FULL_CANVAS"
                    f" actualValue={allowed_coverage:.4f}"
                    f" expectedRange=<0.95"
                    f" note=AI_allowed_to_edit_almost_entire_canvas",
                    flush=True,
                )

            # sourceMapped + newCanvas coverage should sum to exactly 1.0
            total_cov = allowed_coverage + source_coverage
            if abs(total_cov - 1.0) > 0.01:
                print(
                    f"[MASK_ANOMALY] jobId={job_id} specId={spec_id}"
                    f" maskType=allowedGenerationMask+sourceMappedRegionMask"
                    f" anomalyCode=COVERAGE_SUM_MISMATCH"
                    f" actualValue={total_cov:.4f}"
                    f" expectedRange=~1.0",
                    flush=True,
                )

        # From compute_immutable_metrics: allowedGenerationCoverage=1.0 indicates size mismatch
        if immutable_metrics:
            metric_cov = float(immutable_metrics.get("allowedGenerationCoverage", 0.0))
            if metric_cov >= 0.999:
                print(
                    f"[MASK_ANOMALY] jobId={job_id} specId={spec_id}"
                    f" maskType=allowedGenerationMask"
                    f" anomalyCode=IMMUTABLE_METRICS_FULL_CANVAS"
                    f" actualValue={metric_cov:.4f}"
                    f" expectedRange=<0.999"
                    f" note=compute_immutable_metrics_size_mismatch_fallback_suspected",
                    flush=True,
                )

    except Exception as e:
        print(f"[DIAG_LOG_ERROR] log_mask_anomalies: {e}", flush=True)


def log_transform_geometry(
    canvas_transform: object,
    source_w: int,
    source_h: int,
    target_w: int,
    target_h: int,
    *,
    job_id: str = "",
    spec_id: str = "",
) -> None:
    """Emit [TRANSFORM_GEOMETRY] audit for canvas transform from SSC result."""
    try:
        if canvas_transform is None:
            print(
                f"[TRANSFORM_GEOMETRY] jobId={job_id} specId={spec_id}"
                f" strategy=none sourceSize={source_w}x{source_h}"
                f" targetSize={target_w}x{target_h}"
                f" geometryValid=False"
                f" reasonCodes=['NO_CANVAS_TRANSFORM']",
                flush=True,
            )
            return

        strategy = getattr(canvas_transform, "strategy", "unknown")
        scale = float(getattr(canvas_transform, "scale", 0.0))
        crop_x = int(getattr(canvas_transform, "crop_x", 0))
        crop_y = int(getattr(canvas_transform, "crop_y", 0))
        ct_src_w = int(getattr(canvas_transform, "source_w", source_w))
        ct_src_h = int(getattr(canvas_transform, "source_h", source_h))
        canvas_w = int(getattr(canvas_transform, "canvas_w", target_w))
        canvas_h = int(getattr(canvas_transform, "canvas_h", target_h))
        outpaint_required = bool(getattr(canvas_transform, "outpaint_required", False))

        # Stage 1: prefer paste_offset_x/y (authoritative) over crop_x/y for outpaint mode
        stored_paste_x = int(getattr(canvas_transform, "paste_offset_x", -1))
        stored_paste_y = int(getattr(canvas_transform, "paste_offset_y", -1))
        stored_scaled_w = int(getattr(canvas_transform, "scaled_width", 0))
        stored_scaled_h = int(getattr(canvas_transform, "scaled_height", 0))
        stored_mapped_rect = getattr(canvas_transform, "mapped_rect", None) or {}

        if scale > 0:
            scaled_w = stored_scaled_w if stored_scaled_w > 0 else max(int(ct_src_w * scale + 0.5), 1)
            scaled_h = stored_scaled_h if stored_scaled_h > 0 else max(int(ct_src_h * scale + 0.5), 1)
        else:
            scaled_w = stored_scaled_w if stored_scaled_w > 0 else ct_src_w
            scaled_h = stored_scaled_h if stored_scaled_h > 0 else ct_src_h

        expected_off_x = (canvas_w - scaled_w) // 2
        expected_off_y = (canvas_h - scaled_h) // 2

        if strategy == "subject_preserving_outpaint" and stored_paste_x >= 0:
            actual_off_x = stored_paste_x
            actual_off_y = stored_paste_y
        else:
            actual_off_x = crop_x
            actual_off_y = crop_y

        mapped_rect = stored_mapped_rect if stored_mapped_rect else {
            "x1": actual_off_x, "y1": actual_off_y,
            "x2": actual_off_x + scaled_w, "y2": actual_off_y + scaled_h,
        }

        reason_codes = []
        if canvas_w != target_w or canvas_h != target_h:
            reason_codes.append("CANVAS_SIZE_MISMATCH")
        if strategy == "subject_preserving_outpaint":
            if abs(actual_off_x - expected_off_x) > 1:
                reason_codes.append(
                    f"OFFSET_X_MISMATCH_expected={expected_off_x}_actual={actual_off_x}"
                )
            if abs(actual_off_y - expected_off_y) > 1:
                reason_codes.append(
                    f"OFFSET_Y_MISMATCH_expected={expected_off_y}_actual={actual_off_y}"
                )

        subject_bbox = {
            "x1": actual_off_x, "y1": actual_off_y,
            "x2": actual_off_x + scaled_w, "y2": actual_off_y + scaled_h,
        }
        subject_crop_ratio = (
            round(scaled_w * scaled_h / (canvas_w * canvas_h), 4)
            if canvas_w * canvas_h > 0
            else 0.0
        )

        print(
            f"[TRANSFORM_GEOMETRY] jobId={job_id} specId={spec_id}"
            f" strategy={strategy!r}"
            f" sourceSize={ct_src_w}x{ct_src_h}"
            f" targetSize={canvas_w}x{canvas_h}"
            f" scale={scale:.6f}"
            f" scaledSourceSize={scaled_w}x{scaled_h}"
            f" expectedOffset={expected_off_x},{expected_off_y}"
            f" actualOffset={actual_off_x},{actual_off_y}"
            f" mappedRect={mapped_rect}"
            f" subjectBBox={subject_bbox}"
            f" subjectCropRatio={subject_crop_ratio}"
            f" outpaintRequired={outpaint_required}"
            f" geometryValid={len(reason_codes) == 0}"
            f" reasonCodes={reason_codes}",
            flush=True,
        )

    except Exception as e:
        print(f"[DIAG_LOG_ERROR] log_transform_geometry: {e}", flush=True)


def log_pixel_restore_audit(
    ai_result: object,
    restored_img: object,
    allowed_mask_arr: object,
    immutable_metrics: dict,
    *,
    job_id: str = "",
    spec_id: str = "",
) -> None:
    """Emit [PIXEL_RESTORE_AUDIT] or [PIXEL_RESTORE_SKIPPED] with restoration stats."""
    try:
        import numpy as np
        from PIL import Image

        if ai_result is None or restored_img is None:
            print(
                f"[PIXEL_RESTORE_SKIPPED] jobId={job_id} specId={spec_id}"
                f" reason=NULL_INPUT",
                flush=True,
            )
            return

        if not isinstance(ai_result, Image.Image) or not isinstance(restored_img, Image.Image):
            print(
                f"[PIXEL_RESTORE_SKIPPED] jobId={job_id} specId={spec_id}"
                f" reason=NOT_PIL_IMAGE",
                flush=True,
            )
            return

        ai_w, ai_h = ai_result.size
        rs_w, rs_h = restored_img.size
        if (ai_w, ai_h) != (rs_w, rs_h):
            print(
                f"[PIXEL_RESTORE_SKIPPED] jobId={job_id} specId={spec_id}"
                f" reason=AI_RESTORED_SIZE_MISMATCH"
                f" aiResultSize={ai_w}x{ai_h}"
                f" restoredSize={rs_w}x{rs_h}",
                flush=True,
            )
            return

        ai_arr = np.array(ai_result.convert("RGB"), dtype=np.int32)
        rs_arr = np.array(restored_img.convert("RGB"), dtype=np.int32)
        diff = np.any(np.abs(ai_arr - rs_arr) > 0, axis=2)
        restored_pixel_count = int(diff.sum())
        total_pixels = ai_w * ai_h

        if restored_pixel_count == 0:
            # Determine most likely reason for skip
            reason = "NO_DIFFERENCE_BETWEEN_AI_AND_RESTORED"
            mm = immutable_metrics or {}
            if mm.get("allowedGenerationCoverage", 0.0) >= 0.999:
                reason = "CANONICAL_SIZE_MISMATCH_COMPUTE_METRICS_FAILED"
            elif allowed_mask_arr is not None:
                arr = np.asarray(allowed_mask_arr, dtype=np.uint8)
                if arr.ndim == 3:
                    arr = arr[:, :, 0]
                if int((arr == 0).sum()) == 0:
                    reason = "ALLOWED_MASK_FULLY_WHITE_NO_IMMUTABLE_REGION"
            print(
                f"[PIXEL_RESTORE_SKIPPED] jobId={job_id} specId={spec_id}"
                f" reason={reason}"
                f" restoredPixelCount=0"
                f" totalPixels={total_pixels}",
                flush=True,
            )
            return

        mm = immutable_metrics or {}
        allowed_coverage = float(mm.get("allowedGenerationCoverage", 1.0))
        outside_ratio = float(mm.get("outsideAllowedChangedPixelRatio", 0.0))
        immutable_pixels = max(0, int(total_pixels * (1.0 - allowed_coverage)))
        illegal_changed = int(immutable_pixels * outside_ratio) if immutable_pixels > 0 else 0
        remaining_illegal = max(0, illegal_changed - restored_pixel_count)

        print(
            f"[PIXEL_RESTORE_AUDIT] jobId={job_id} specId={spec_id}"
            f" totalPixels={total_pixels}"
            f" immutableRegionPixels={immutable_pixels}"
            f" providerChangedPixels={illegal_changed}"
            f" illegalChangedPixels={illegal_changed}"
            f" restorableChangedPixels={illegal_changed}"
            f" restoredPixelCount={restored_pixel_count}"
            f" remainingIllegalChangedPixels={remaining_illegal}"
            f" immutableChangedPixelRatio={outside_ratio:.4f}"
            f" outsideAllowedChangedPixelRatio={outside_ratio:.4f}"
            f" allowedGenerationCoverage={allowed_coverage:.4f}",
            flush=True,
        )

    except Exception as e:
        print(f"[DIAG_LOG_ERROR] log_pixel_restore_audit: {e}", flush=True)


def log_manifest_audit(
    manifest: object,
    fg_layers: list,
    *,
    job_id: str = "",
    spec_id: str = "",
) -> None:
    """Emit [MANIFEST_AUDIT] summarising manifest completeness vs fg_layers."""
    try:
        detected_count = len([l for l in (fg_layers or []) if isinstance(l, dict)])
        extracted_count = 0
        manifest_count = 0
        required_roles: list = []
        present_required: list = []
        missing_required: list = []
        rejected_required: list = []
        finalized = False
        fail_closed = False

        if manifest is not None:
            if hasattr(manifest, "objects"):
                manifest_count = len(manifest.objects or [])
                extracted_count = manifest_count
                for obj in (manifest.objects or []):
                    role = (
                        getattr(obj, "semanticRole", "")
                        or getattr(obj, "role", "")
                        or ""
                    )
                    if getattr(obj, "required", False) and role:
                        required_roles.append(role)
                        present_required.append(role)
                finalized = bool(getattr(manifest, "finalized", False))
                fail_closed = bool(getattr(manifest, "failClosed", False))
            elif hasattr(manifest, "preserve_roles"):
                pr = list(getattr(manifest, "preserve_roles", []))
                extracted_count = len(pr)
                manifest_count = len(pr)
                required_roles = pr
                present_required = pr

        print(
            f"[MANIFEST_AUDIT] jobId={job_id} specId={spec_id}"
            f" detectedObjectCount={detected_count}"
            f" extractedObjectCount={extracted_count}"
            f" manifestObjectCount={manifest_count}"
            f" expectedRequiredRoles={required_roles}"
            f" presentRequiredRoles={present_required}"
            f" missingRequiredRoles={missing_required}"
            f" rejectedRequiredRoles={rejected_required}"
            f" finalized={finalized}"
            f" failClosed={fail_closed}",
            flush=True,
        )

    except Exception as e:
        print(f"[DIAG_LOG_ERROR] log_manifest_audit: {e}", flush=True)


def log_result_semantics(
    scene_result: object,
    verdict_summary: object,
    visual_verdict: object,
    *,
    job_id: str = "",
    spec_id: str = "",
) -> None:
    """Emit [RESULT_SEMANTICS] with outcome counters and status flags."""
    try:
        provider_succeeded = bool(
            scene_result is not None and getattr(scene_result, "success", False)
        )

        overall_status = "NOT_TESTED"
        if verdict_summary is not None:
            overall_status = getattr(verdict_summary, "overallStatus", "NOT_TESTED")
        elif scene_result is not None:
            overall_status = "PASS" if provider_succeeded else "FAIL"

        final_result_valid = (overall_status == "PASS")
        success_count_incremented = provider_succeeded
        valid_count_incremented = final_result_valid

        visual_status = "NOT_TESTED"
        if visual_verdict is not None:
            visual_status = getattr(visual_verdict, "status", "NOT_TESTED")

        print(
            f"[RESULT_SEMANTICS] jobId={job_id} specId={spec_id}"
            f" providerSucceeded={provider_succeeded}"
            f" artifactGenerated={provider_succeeded}"
            f" overallStatus={overall_status}"
            f" finalResultValid={final_result_valid}"
            f" visualVerdictStatus={visual_status}"
            f" successCountIncremented={success_count_incremented}"
            f" validCountIncremented={valid_count_incremented}",
            flush=True,
        )

    except Exception as e:
        print(f"[DIAG_LOG_ERROR] log_result_semantics: {e}", flush=True)


def log_root_cause_summary(
    verdict_summary: object,
    scene_result: object,
    visual_verdict: object,
    immutable_metrics: dict,
    *,
    job_id: str = "",
    spec_id: str = "",
) -> None:
    """Emit [ROOT_CAUSE_SUMMARY] synthesising failure analysis across all stages."""
    try:
        overall_status = "NOT_TESTED"
        if verdict_summary is not None:
            overall_status = getattr(verdict_summary, "overallStatus", "NOT_TESTED")
        elif scene_result is not None:
            overall_status = "PASS" if getattr(scene_result, "success", False) else "FAIL"

        primary_failure = "NONE"
        upstream_causes: list = []
        affected_principles: list = []
        first_failing_stage = "none"
        recommended = "none"

        # Always check immutable metrics — allowedGenerationCoverage=1.0 indicates a
        # size-mismatch bug that occurs even when overallStatus appears to be PASS.
        mm = immutable_metrics or {}
        if float(mm.get("allowedGenerationCoverage", 0.0)) >= 0.999:
            upstream_causes.append("ALLOWED_GENERATION_MASK_FULL_CANVAS")
            affected_principles.append("IMMUTABLE_PIXEL_PROTECTION")
        if float(mm.get("outsideAllowedChangedPixelRatio", 0.0)) > 0:
            upstream_causes.append("IMMUTABLE_PIXELS_CHANGED")
            affected_principles.append("ORIGINAL_PIXEL_PRESERVATION")

        if overall_status not in ("PASS", "NOT_TESTED"):
            # Scan structured verdict dimensions
            if verdict_summary is not None:
                for v_name in (
                    "visualVerdict", "technicalVerdict", "extractionVerdict",
                    "compositionVerdict", "layoutVerdict",
                ):
                    v = getattr(verdict_summary, v_name, None)
                    if v is None:
                        continue
                    if getattr(v, "status", "") == "FAIL":
                        reason_codes = getattr(v, "reasonCodes", []) or []
                        if reason_codes and primary_failure == "NONE":
                            primary_failure = reason_codes[0]
                            first_failing_stage = v_name
                        upstream_causes.extend(reason_codes)

            # Visual-specific causes
            if visual_verdict is not None:
                v_status = getattr(visual_verdict, "status", "")
                v_rc = getattr(visual_verdict, "reasonCodes", []) or []
                if v_status == "FAIL":
                    if "FULL_SCENE_REGENERATION_DETECTED" in v_rc:
                        upstream_causes.append("FULL_SCENE_REGENERATION_DETECTED")
                        affected_principles.append("ORIGINAL_PIXEL_PRESERVATION")
                        if first_failing_stage == "none":
                            first_failing_stage = "stage6_visual"
                            primary_failure = "FULL_SCENE_REGENERATION_DETECTED"

            # SSC provider failure
            if scene_result is not None and not getattr(scene_result, "success", True):
                upstream_causes.append("SSC_PROVIDER_FAILED")
                affected_principles.append("CLEAN_SCENE_PLATE")
                if first_failing_stage == "none":
                    first_failing_stage = "stage1_ssc"
                    primary_failure = "SSC_PROVIDER_FAILED"

            # Set primary_failure from metrics causes if nothing else set it
            if primary_failure == "NONE" and "ALLOWED_GENERATION_MASK_FULL_CANVAS" in upstream_causes:
                primary_failure = "ALLOWED_GENERATION_MASK_FULL_CANVAS"

            # Recommended inspection point
            if "FULL_SCENE_REGENERATION_DETECTED" in primary_failure:
                recommended = "SSC_provider_output+pixel_diff_ratio+canonical_size_vs_result_size"
            elif "ALLOWED_GENERATION" in primary_failure:
                recommended = "Stage4_canonical_size_vs_result_size"
            elif "SSC_PROVIDER" in primary_failure:
                recommended = "provider_error_log+retry_count"
            elif "MISSING" in primary_failure or "REJECT" in primary_failure:
                recommended = "D2_extraction+manifest_audit"
            elif primary_failure != "NONE":
                recommended = "verdict_summary+visual_evidence"

        upstream_causes = list(dict.fromkeys(upstream_causes))
        affected_principles = list(dict.fromkeys(affected_principles))

        print(
            f"[ROOT_CAUSE_SUMMARY] jobId={job_id} specId={spec_id}"
            f" overallStatus={overall_status}"
            f" primaryFailure={primary_failure}"
            f" upstreamCauses={upstream_causes}"
            f" affectedPrinciples={affected_principles}"
            f" firstFailingStage={first_failing_stage}"
            f" recommendedInspectionPoint={recommended!r}",
            flush=True,
        )

    except Exception as e:
        print(f"[DIAG_LOG_ERROR] log_root_cause_summary: {e}", flush=True)
