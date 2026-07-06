import os
import zipfile
from flask import Flask, request, jsonify
import resizer
import psd_analyzer

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

    if not psd_path or not os.path.exists(psd_path):
        return jsonify({"error": "psd_path not found"}), 400

    job_output_dir = os.path.join(OUTPUT_DIR, job_id)

    try:
        result_items = resizer.generate(
            psd_path, specs, resize_mode, output_format, job_output_dir,
            smart_fit_strength, focal_position, source_type, psd_mode
        )
        file_paths = [r["filePath"] for r in result_items]
        zip_path = _make_zip(job_id, file_paths)
        return jsonify({
            "jobId": job_id,
            "zipPath": zip_path,
            "count": len(result_items),
            "results": result_items,
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


def _make_zip(job_id: str, files: list[str]) -> str:
    os.makedirs(ZIP_DIR, exist_ok=True)
    zip_path = os.path.join(ZIP_DIR, f"{job_id}.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(f, os.path.basename(f))
    return zip_path


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
