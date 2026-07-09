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


def _make_zip(job_id: str, files: list[str]) -> str:
    os.makedirs(ZIP_DIR, exist_ok=True)
    zip_path = os.path.join(ZIP_DIR, f"{job_id}.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(f, os.path.basename(f))
    return zip_path


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
