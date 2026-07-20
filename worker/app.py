import base64
import io
import os
import tempfile
import zipfile
from flask import Flask, request, jsonify
import psd_tools
import resizer
import psd_analyzer
import layer_object_matcher

print(f"[PSD] psd-tools version: {getattr(psd_tools, '__version__', 'unknown')}")

app = Flask(__name__)

OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "/app/storage/outputs")
ZIP_DIR = os.environ.get("ZIP_DIR", "/app/storage/zips")


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/analyze-psd", methods=["POST"])
def analyze_psd():
    data = request.json
    file_path = data.get("filePath")
    if not file_path or not os.path.exists(file_path):
        return jsonify({"error": "filePath not found"}), 400
    try:
        result = psd_analyzer.analyze_psd_file(file_path)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/generate", methods=["POST"])
def generate():
    data = request.json
    job_id = data.get("jobId")
    psd_path = data.get("psdPath")
    specs = data.get("specs", [])
    resize_mode = data.get("resizeMode", "smart-fit")
    smart_fit_strength = data.get("smartFitStrength", "balanced")
    focal_position = data.get("focalPosition", "center")
    output_format = data.get("outputFormat", "png")
    source_type = data.get("sourceType", "image")
    psd_mode = data.get("psdMode", "artboard-first")
    selected_artboard_ids = data.get("selectedArtboardIds") or []
    object_reflow_enabled = bool(data.get("objectReflowEnabled", False))
    object_analysis = data.get("objectAnalysis") or None

    if not psd_path or not os.path.exists(psd_path):
        return jsonify({"error": "psd_path not found"}), 400

    job_output_dir = os.path.join(OUTPUT_DIR, job_id)

    try:
        result_items, missing_ratio_types = resizer.generate(
            psd_path, specs, resize_mode, output_format, job_output_dir,
            smart_fit_strength, focal_position, source_type, psd_mode,
            selected_artboard_ids, object_reflow_enabled, object_analysis,
            job_id=job_id
        )
        file_paths = [r["filePath"] for r in result_items]
        zip_path = _make_zip(job_id, file_paths)
        return jsonify({
            "jobId": job_id,
            "zipPath": zip_path,
            "count": len(result_items),
            "results": result_items,
            "missingRatioTypes": missing_ratio_types,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/compare", methods=["POST"])
def compare():
    data = request.json
    compare_id = data.get("compareId")
    psd_path = data.get("psdPath")
    spec = data.get("spec")
    resize_mode = data.get("resizeMode", "smart-fit")
    focal_position = data.get("focalPosition", "center")
    strengths = data.get("strengths", ["safe", "balanced", "fill"])
    detected_elements = data.get("detectedElements", [])
    required_groups = data.get("requiredGroups", [])
    priority_groups = data.get("priorityGroups", [])
    content_bands = data.get("contentBands", [])

    if not psd_path or not os.path.exists(psd_path):
        return jsonify({"error": "psd_path not found"}), 400
    if not spec or not compare_id:
        return jsonify({"error": "compareId and spec are required"}), 400

    compare_output_dir = os.path.join(OUTPUT_DIR, "compare", compare_id)

    try:
        original_path, candidates = resizer.generate_candidates(
            psd_path, compare_output_dir, spec, resize_mode, focal_position, strengths,
            detected_elements, required_groups, priority_groups, content_bands
        )
        return jsonify({
            "compareId": compare_id,
            "originalFilePath": original_path,
            "candidates": candidates,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/extract-artboard", methods=["POST"])
def extract_artboard():
    """아트보드 프리뷰 JPEG(base64) + 레이어 목록 반환."""
    data = request.json or {}
    psd_path = data.get("psdPath")
    ab_box = data.get("artboardBox")  # {x, y, width, height} or null

    if not psd_path or not os.path.exists(psd_path):
        return jsonify({"error": "psdPath not found"}), 400

    try:
        from psd_layer_parser import parse_psd_layers

        psd = psd_tools.PSDImage.open(psd_path)
        canvas_w, canvas_h = psd.width, psd.height

        if not ab_box:
            ab_box = {"x": 0, "y": 0, "width": canvas_w, "height": canvas_h}

        x = max(0, min(int(ab_box["x"]), canvas_w - 1))
        y = max(0, min(int(ab_box["y"]), canvas_h - 1))
        w = max(1, min(int(ab_box["width"]), canvas_w - x))
        h = max(1, min(int(ab_box["height"]), canvas_h - y))

        composite = psd.composite()
        ab_img = composite.crop((x, y, x + w, y + h))
        buf = io.BytesIO()
        ab_img.convert("RGB").save(buf, format="JPEG", quality=88)
        buf.seek(0)
        preview_b64 = base64.b64encode(buf.read()).decode()

        tmp_dir = tempfile.mkdtemp()
        raw_layers = parse_psd_layers(psd, tmp_dir)
        safe_layers = [
            {k: v for k, v in layer.items() if k != "_layer_obj" and k != "previewPath"}
            for layer in raw_layers
        ]

        return jsonify({
            "previewBase64": preview_b64,
            "artboardBox": {"x": x, "y": y, "width": w, "height": h},
            "layers": safe_layers,
            "canvasWidth": canvas_w,
            "canvasHeight": canvas_h,
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/match-layers", methods=["POST"])
def match_layers():
    """AI 객체 목록을 PSD 레이어에 매칭."""
    data = request.json or {}
    ai_objects = data.get("aiObjects", [])
    psd_layers = data.get("layers", [])
    ab_box = data.get("artboardBox")
    canvas_w = int(data.get("canvasWidth", 1))
    canvas_h = int(data.get("canvasHeight", 1))

    try:
        matched = layer_object_matcher.match_objects_to_layers(
            ai_objects, psd_layers, canvas_w, canvas_h, ab_box
        )
        return jsonify({"matchedObjects": matched})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ── Stage 19 Background Pipeline endpoints ───────────────────────────────────

@app.route("/v1/background/health", methods=["GET"])
def background_health():
    """Stage 19 background pipeline health check."""
    import os
    enabled = os.environ.get("BACKGROUND_PIPELINE_ENABLED", "false").lower() == "true"
    return jsonify({
        "status": "ok",
        "backgroundPipelineEnabled": enabled,
        "compareOnly": os.environ.get("BACKGROUND_PIPELINE_COMPARE_ONLY", "true").lower() == "true",
        "localInpaintEnabled": os.environ.get("BACKGROUND_LOCAL_INPAINT_ENABLED", "true").lower() == "true",
        "externalInpaintEnabled": os.environ.get("BACKGROUND_EXTERNAL_INPAINT_ENABLED", "false").lower() == "true",
        "outpaintEnabled": os.environ.get("BACKGROUND_OUTPAINT_ENABLED", "false").lower() == "true",
        "shadowEnabled": os.environ.get("BACKGROUND_SHADOW_ENABLED", "false").lower() == "true",
    })


@app.route("/v1/background/process", methods=["POST"])
def background_process():
    """Stage 19 full background pipeline: inpaint + outpaint + shadow + quality gate."""
    import base64 as _b64
    from PIL import Image as _PILImage
    from background import BackgroundPipeline
    from background.schemas import BackgroundRequest, BackgroundOptions

    data = request.json or {}

    # decode source image
    source_b64 = data.get("sourceImageBase64")
    if not source_b64:
        return jsonify({"error": "sourceImageBase64 required"}), 400
    try:
        import io as _io
        img_bytes = _b64.b64decode(source_b64)
        source_image = _PILImage.open(_io.BytesIO(img_bytes)).convert("RGB")
    except Exception as e:
        return jsonify({"error": f"sourceImage decode failed: {e}"}), 400

    opts = BackgroundOptions.from_env()
    # allow per-request overrides
    req_opts = data.get("options", {})
    if "enabled" in req_opts:
        opts.enabled = bool(req_opts["enabled"])
    if "compareOnly" in req_opts:
        opts.compare_only = bool(req_opts["compareOnly"])
    if "allowLocalInpaint" in req_opts:
        opts.allow_local_inpaint = bool(req_opts["allowLocalInpaint"])
    if "allowOutpaint" in req_opts:
        opts.allow_outpaint = bool(req_opts["allowOutpaint"])
    if "allowShadow" in req_opts:
        opts.allow_shadow = bool(req_opts["allowShadow"])
    if "artifactLevel" in req_opts:
        opts.artifact_level = str(req_opts["artifactLevel"])

    bg_req = BackgroundRequest(
        source_image=source_image,
        target_width=int(data.get("targetWidth", source_image.width)),
        target_height=int(data.get("targetHeight", source_image.height)),
        protected_objects=data.get("protectedObjects", []),
        layout_candidate=data.get("layoutCandidate", {}),
        safe_zone=data.get("safeZone", {}),
        options=opts,
        request_id=data.get("requestId", ""),
    )

    job_out = os.path.join(OUTPUT_DIR, "stage19", data.get("requestId", "default"))
    pipeline = BackgroundPipeline(output_dir=job_out)
    try:
        result = pipeline.process(bg_req)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    # encode result image
    result_b64 = None
    if result.result_image is not None:
        try:
            buf = _io.BytesIO()
            result.result_image.convert("RGB").save(buf, format="PNG")
            result_b64 = _b64.b64encode(buf.getvalue()).decode()
        except Exception:
            pass

    return jsonify({
        "status": "ok" if result.success else "partial",
        "verdict": result.verdict,
        "selectedCandidateId": result.selected_candidate_id,
        "selectedBackgroundSource": result.selected_background_source,
        "appliedBackgroundSource": result.applied_background_source,
        "backgroundCompareOnly": result.background_compare_only,
        "bestEvaluatedBackgroundSource": result.best_evaluated_background_source,
        "bestEvaluatedBackgroundScore": result.best_evaluated_background_score,
        "fallbackUsed": result.fallback_used,
        "fallbackReason": result.fallback_reason,
        "localInpaintAttempted": result.local_inpaint_attempted,
        "localInpaintAccepted": result.local_inpaint_accepted,
        "externalInpaintAttempted": result.external_inpaint_attempted,
        "outpaintAttempted": result.outpaint_attempted,
        "shadowApplied": result.shadow_applied,
        "metrics": result.metrics,
        "warnings": result.warnings,
        "artifacts": result.artifacts,
        "elapsedMs": result.elapsed_ms,
        "resultImageBase64": result_b64,
    })


def _make_zip(job_id: str, files: list[str]) -> str:
    os.makedirs(ZIP_DIR, exist_ok=True)
    zip_path = os.path.join(ZIP_DIR, f"{job_id}.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(f, os.path.basename(f))
    return zip_path


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
