import io
import os
import sys
import tempfile
import threading
import time
import zipfile
from flask import Flask, request, jsonify

app = Flask(__name__)

OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "/app/storage/outputs")
ZIP_DIR = os.environ.get("ZIP_DIR", "/app/storage/zips")

# ── 중복 실행 방지: jobId 단위 idempotency ───────────────────────────────────
# Java API가 timeout으로 실패 처리 후 재요청해도 동일 jobId는 한 번만 처리한다.
# 409: 이미 처리 중. 클라이언트는 Job 상태를 polling해서 완료를 기다려야 한다.
_active_jobs: set = set()
_active_jobs_lock = threading.Lock()


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/generate", methods=["POST"])
def generate():
    data = request.json
    job_id = data.get("jobId")
    specs = data.get("specs", [])

    if not data.get("psdPath") and not data.get("sourceFilePath"):
        return jsonify({"error": "psdPath or sourceFilePath is required"}), 400

    # ── 중복 실행 방지 ──────────────────────────────────────────────────────────
    if job_id:
        with _active_jobs_lock:
            if job_id in _active_jobs:
                print(f"[GENERATE_DUPLICATE] jobId={job_id} already processing, returning 409", flush=True)
                return jsonify({"error": f"jobId={job_id} is already being processed"}), 409
            _active_jobs.add(job_id)

    job_output_dir = os.path.join(OUTPUT_DIR, job_id)
    t_start = time.time()

    # ── Pipeline version resolution ───────────────────────────────────────────
    # Only clean_v1 is supported. "legacy" has been removed.
    pipeline_version = data.get("pipelineVersion") or "clean_v1"
    if pipeline_version != "clean_v1":
        print(f"[PIPELINE_VERSION_INVALID] jobId={job_id} pipelineVersion={pipeline_version!r}", flush=True)
        if job_id:
            with _active_jobs_lock:
                _active_jobs.discard(job_id)
        return jsonify({"error": f"Unsupported pipelineVersion: {pipeline_version!r}. "
                                 "Only 'clean_v1' is supported."}), 400
    pipeline_version_source = "explicit" if data.get("pipelineVersion") else "default"
    print(
        f"[AI_ONLY_START] jobId={job_id} specCount={len(specs)} "
        f"pipelineVersion={pipeline_version} source={pipeline_version_source}",
        flush=True,
    )

    try:
        return _run_clean_v1(data, job_id, job_output_dir, t_start)
    except Exception as e:
        elapsed_ms = int((time.time() - t_start) * 1000)
        print(f"[AI_ONLY_ERROR] jobId={job_id} elapsedMs={elapsed_ms} error={e}", flush=True)
        return jsonify({"error": str(e)}), 500
    finally:
        if job_id:
            with _active_jobs_lock:
                _active_jobs.discard(job_id)


def _run_clean_v1(data: dict, job_id: str, job_output_dir: str, t_start: float):
    """Route pipelineVersion=clean_v1 to the clean_pipeline orchestrator.

    Fail-closed: FAIL status → failure response. No legacy fallback under any circumstance.
    """
    from clean_pipeline.bridge.request_adapter import adapt_request
    from clean_pipeline.bridge.response_adapter import adapt_response
    from clean_pipeline.orchestrator import run as clean_run

    api_key = (
        os.environ.get("OPENAI_API_KEY", "")
        or os.environ.get("BACKGROUND_AI_API_KEY", "")
    )

    cp_request = adapt_request(data, job_id, job_output_dir)
    print(f"[CLEAN_V1_START] jobId={job_id} specCount={len(cp_request.target_specs)}", flush=True)

    cp_result = clean_run(cp_request, api_key=api_key)

    result_items, missing_ratio_types = adapt_response(cp_result, data.get("specs", []))

    file_paths = [r["filePath"] for r in result_items if r.get("filePath")]
    zip_path = _make_zip(job_id, file_paths) if file_paths else ""

    elapsed_ms = int((time.time() - t_start) * 1000)
    print(
        f"[CLEAN_V1_END] jobId={job_id} status={cp_result.status.value} elapsedMs={elapsed_ms}",
        flush=True,
    )
    return jsonify({
        "jobId": job_id,
        "zipPath": zip_path,
        "count": len(result_items),
        "results": result_items,
        "missingRatioTypes": missing_ratio_types,
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
