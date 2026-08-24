import io
import os
import shutil
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

    clean_pipeline.orchestrator.run()은 spec 1개만 처리한다 (조사 완료, MVP 제약).
    선택된 spec이 여러 개면 여기서 spec별로 한 번씩 반복 호출해 결과를 모은다.
    """
    from clean_pipeline.bridge.request_adapter import adapt_request
    from clean_pipeline.bridge.response_adapter import adapt_response
    from clean_pipeline.orchestrator import run as clean_run

    api_key = (
        os.environ.get("OPENAI_API_KEY", "")
        or os.environ.get("BACKGROUND_AI_API_KEY", "")
    )

    specs_raw = data.get("specs", [])
    result_items: list[dict] = []
    missing_ratio_types: list[str] = []

    for spec_index, spec_raw in enumerate(specs_raw):
        # orchestrator.run()은 target_specs[0]만 사용하므로 spec 1개짜리 요청으로 호출한다.
        single_spec_data = {**data, "specs": [spec_raw]}
        cp_request = adapt_request(single_spec_data, job_id, job_output_dir)
        print(
            f"[CLEAN_V1_START] jobId={job_id} specIndex={spec_index} "
            f"specCount={len(specs_raw)} slug={spec_raw.get('slug')!r}",
            flush=True,
        )

        cp_result = clean_run(cp_request, api_key=api_key)
        print(
            f"[CLEAN_V1_SPEC_DONE] jobId={job_id} specIndex={spec_index} "
            f"status={cp_result.status.value}",
            flush=True,
        )

        items, missing = adapt_response(cp_result, [spec_raw])
        for item in items:
            file_path = item.get("filePath")
            if file_path:
                # 모든 TYPE이 고정 경로(예: 08_final/result.png)에 쓰므로,
                # 다음 spec 처리 전에 spec별 고유 파일명으로 옮겨 덮어쓰기를 막는다.
                item["filePath"], item["fileName"] = _uniquify_result_file(
                    file_path, spec_index, item.get("slug", ""),
                    item.get("width", 0), item.get("height", 0),
                )
            result_items.append(item)
        missing_ratio_types.extend(missing)

    file_paths = [r["filePath"] for r in result_items if r.get("filePath")]
    zip_path = _make_zip(job_id, file_paths) if file_paths else ""

    elapsed_ms = int((time.time() - t_start) * 1000)
    print(
        f"[CLEAN_V1_END] jobId={job_id} specCount={len(specs_raw)} "
        f"resultCount={len(file_paths)} missingCount={len(missing_ratio_types)} "
        f"elapsedMs={elapsed_ms}",
        flush=True,
    )
    return jsonify({
        "jobId": job_id,
        "zipPath": zip_path,
        "count": len(result_items),
        "results": result_items,
        "missingRatioTypes": missing_ratio_types,
    })


def _uniquify_result_file(file_path: str, spec_index: int, slug: str, width: int, height: int) -> tuple[str, str]:
    """spec별 결과가 같은 고정 경로(result.png)에 쓰이므로, 다음 spec 처리 전에
    고유 파일명으로 옮긴다. 반환값: (새 filePath, 새 fileName)
    """
    directory = os.path.dirname(file_path)
    ext = os.path.splitext(file_path)[1] or ".png"
    safe_slug = slug or f"spec{spec_index}"
    size_suffix = f"{width}x{height}"
    # slug가 이미 _WxH로 끝나면 중복 방지
    if safe_slug.endswith(size_suffix):
        new_name = f"{spec_index:02d}_{safe_slug}{ext}"
    else:
        new_name = f"{spec_index:02d}_{safe_slug}_{size_suffix}{ext}"
    new_path = os.path.join(directory, new_name)
    shutil.move(file_path, new_path)
    return new_path, new_name


def _make_zip(job_id: str, files: list[str]) -> str:
    os.makedirs(ZIP_DIR, exist_ok=True)
    zip_path = os.path.join(ZIP_DIR, f"{job_id}.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(f, os.path.basename(f))
    return zip_path


@app.route("/recomposite", methods=["POST"])
def recomposite():
    """P5 에디터에서 수정된 레이어 위치로 P4를 재실행한다.

    Body:
      jobId       str   — 원본 job ID
      bgPath      str   — P3 배경 이미지 절대 경로 (컨테이너 내부)
      psdPath     str   — 원본 PSD 절대 경로 (컨테이너 내부)
      targetW     int   — 타겟 너비
      targetH     int   — 타겟 높이
      sourceW     int   — 소스 너비
      sourceH     int   — 소스 높이
      layers      list  — [{name, render_x, render_y, render_w, render_h}]
      resultPath  str   — 덮어쓸 result.png 절대 경로
    """
    data = request.json
    job_id = data.get("jobId", "recomposite")
    bg_path = data.get("bgPath")
    psd_path = data.get("psdPath")
    target_w = int(data.get("targetW", 0))
    target_h = int(data.get("targetH", 0))
    source_w = int(data.get("sourceW", 0))
    source_h = int(data.get("sourceH", 0))
    layers_payload = data.get("layers", [])
    result_path = data.get("resultPath")

    if not all([bg_path, psd_path, target_w, target_h, source_w, source_h, result_path]):
        return jsonify({"error": "bgPath, psdPath, targetW, targetH, sourceW, sourceH, resultPath are required"}), 400

    try:
        from PIL import Image
        from psd_tools import PSDImage

        canvas = Image.open(bg_path).convert("RGBA")
        psd = PSDImage.open(psd_path)

        from clean_pipeline.psd.element_compositor import _find_layer

        placed = 0
        for lyr in layers_payload:
            layer = _find_layer(psd, lyr["name"])
            if layer is None:
                print(f"[RECOMPOSITE][LAYER_NOT_FOUND] name={lyr['name']!r}", flush=True)
                continue
            try:
                img = layer.composite()
            except Exception as exc:
                print(f"[RECOMPOSITE][COMPOSITE_SKIP] name={lyr['name']!r} err={exc}", flush=True)
                continue
            if img is None:
                continue
            img = img.convert("RGBA")
            b = layer.bbox
            lw, lh = b[2] - b[0], b[3] - b[1]
            if lw <= 0 or lh <= 0:
                continue
            if img.size == (psd.width, psd.height):
                img = img.crop((b[0], b[1], b[2], b[3]))
            new_w = max(1, int(lyr["render_w"]))
            new_h = max(1, int(lyr["render_h"]))
            scaled = img.resize((new_w, new_h), Image.LANCZOS)
            canvas.paste(scaled, (int(lyr["render_x"]), int(lyr["render_y"])), scaled)
            placed += 1
            print(f"[RECOMPOSITE][LAYER_PLACED] name={lyr['name']!r} pos=({lyr['render_x']},{lyr['render_y']}) size={new_w}x{new_h}", flush=True)

        canvas.convert("RGB").save(result_path)
        print(f"[RECOMPOSITE][DONE] jobId={job_id} placed={placed} result={result_path}", flush=True)
        return jsonify({"resultPath": result_path, "placedCount": placed})

    except Exception as exc:
        print(f"[RECOMPOSITE][ERROR] jobId={job_id} err={exc}", flush=True)
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
