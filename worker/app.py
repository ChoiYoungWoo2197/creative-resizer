import os
import zipfile
import uuid
from flask import Flask, request, jsonify
import resizer

app = Flask(__name__)

OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "/app/storage/outputs")
ZIP_DIR = os.environ.get("ZIP_DIR", "/app/storage/zips")


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/generate", methods=["POST"])
def generate():
    data = request.json
    job_id = data.get("jobId")
    psd_path = data.get("psdPath")
    specs = data.get("specs", [])
    resize_mode = data.get("resizeMode", "cover")
    output_format = data.get("outputFormat", "png")

    if not psd_path or not os.path.exists(psd_path):
        return jsonify({"error": "psd_path not found"}), 400

    job_output_dir = os.path.join(OUTPUT_DIR, job_id)

    try:
        output_files = resizer.generate(psd_path, specs, resize_mode, output_format, job_output_dir)
        zip_path = _make_zip(job_id, output_files)
        return jsonify({"jobId": job_id, "zipPath": zip_path, "count": len(output_files)})
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
