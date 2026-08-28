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
                # 모든 TYPE이 고정 경로(예: 04_composite/result.png)에 쓰므로,
                # 다음 spec 처리 전에 spec별로 격리해 덮어쓰기를 막는다.
                item["filePath"], item["fileName"], item["specDirName"] = _isolate_spec_output(
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


def _isolate_spec_output(file_path: str, spec_index: int, slug: str, width: int, height: int) -> tuple[str, str, str]:
    """spec별 result.png를 고유 파일명으로 이동하고, JSON/layers를 spec 전용
    서브디렉터리로 격리한다. 같은 spec이 여러 개일 때 파일 덮어쓰기를 방지.

    반환값: (새 filePath, 새 fileName, specDirName)
    """
    directory = os.path.dirname(file_path)
    ext = os.path.splitext(file_path)[1] or ".png"
    safe_slug = slug or f"spec{spec_index}"
    size_suffix = f"{width}x{height}"

    # spec_dir_name: 기존 _uniquify_result_file과 동일한 명명 규칙
    if safe_slug.endswith(size_suffix):
        spec_dir_name = f"{spec_index:02d}_{safe_slug}"
    else:
        spec_dir_name = f"{spec_index:02d}_{safe_slug}_{size_suffix}"

    # 1. result.png → {spec_dir_name}.png (기존 동작 유지)
    new_name = spec_dir_name + ext
    new_path = os.path.join(directory, new_name)
    shutil.move(file_path, new_path)

    # 2. spec 전용 서브디렉터리 생성 후 JSON + layers 이동
    # file_key는 element_compositor._file_key와 동일한 규칙 사용
    file_key = slug if slug else size_suffix
    spec_dir = os.path.join(directory, spec_dir_name)
    os.makedirs(spec_dir, exist_ok=True)
    for item in [f"layout_result_{file_key}.json", f"layers_merged_{file_key}.json", "layers"]:
        src = os.path.join(directory, item)
        if os.path.exists(src):
            shutil.move(src, os.path.join(spec_dir, item))

    # 3. JSON 내부 layer_file 경로 패치 (이동 후 경로가 변경되므로)
    old_prefix = "clean_v1/04_composite/layers/"
    new_prefix = f"clean_v1/04_composite/{spec_dir_name}/layers/"
    for json_name in [f"layout_result_{file_key}.json", f"layers_merged_{file_key}.json"]:
        json_path = os.path.join(spec_dir, json_name)
        if os.path.exists(json_path):
            _patch_layer_file_paths(json_path, old_prefix, new_prefix)

    return new_path, new_name, spec_dir_name


def _patch_layer_file_paths(json_path: str, old_prefix: str, new_prefix: str) -> None:
    """layout_result.json 내 layer_file 경로를 spec 서브디렉터리 기준으로 수정."""
    try:
        with open(json_path, encoding="utf-8") as f:
            text = f.read()
        if old_prefix not in text:
            return
        with open(json_path, "w", encoding="utf-8") as f:
            f.write(text.replace(old_prefix, new_prefix))
    except Exception as exc:
        print(f"[ISOLATE_SPEC][PATCH_WARN] path={json_path} err={exc}", flush=True)


def _make_zip(job_id: str, files: list[str]) -> str:
    os.makedirs(ZIP_DIR, exist_ok=True)
    zip_path = os.path.join(ZIP_DIR, f"{job_id}.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(f, os.path.basename(f))
    return zip_path


def _draw_text_pil(canvas, text: str, x: int, y: int, w: int, h: int) -> None:
    """PIL ImageDraw로 텍스트를 canvas에 그린다 (textOverrides 처리용)."""
    import os
    from PIL import ImageDraw, ImageFont

    draw = ImageDraw.Draw(canvas)
    font_size = max(12, int(h * 0.65))

    font = None
    for fpath in [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKkr-Regular.otf",
    ]:
        if os.path.exists(fpath):
            try:
                font = ImageFont.truetype(fpath, font_size)
                break
            except Exception:
                pass
    if font is None:
        font = ImageFont.load_default()

    # 단어 단위 줄바꿈
    dummy_draw = ImageDraw.Draw(canvas)
    lines, current = [], ""
    for paragraph in text.split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            test = current + " " + word
            bbox = dummy_draw.textbbox((0, 0), test, font=font)
            if bbox[2] <= w:
                current = test
            else:
                lines.append(current)
                current = word
        lines.append(current)

    draw.multiline_text((x, y), "\n".join(lines), font=font, fill=(0, 0, 0, 255), spacing=4)


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
    text_overrides = data.get("textOverrides") or {}

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
            name = lyr["name"]
            override_text = text_overrides.get(name, "")

            if override_text:
                # textOverrides 있는 레이어 → PSD composite 없이 PIL 텍스트로 대체
                rx, ry = int(lyr["render_x"]), int(lyr["render_y"])
                rw, rh = int(lyr["render_w"]), int(lyr["render_h"])
                _draw_text_pil(canvas, override_text, rx, ry, rw, rh)
                placed += 1
                print(f"[RECOMPOSITE][TEXT_OVERRIDE] name={name!r} text={override_text[:30]!r}", flush=True)
                # 레이어 파일 갱신 → P5 재진입 시 업데이트된 텍스트 이미지 표시
                layer_file_path = lyr.get("layerFilePath")
                if layer_file_path:
                    try:
                        text_layer = Image.new("RGBA", (canvas.width, canvas.height), (0, 0, 0, 0))
                        _draw_text_pil(text_layer, override_text, rx, ry, rw, rh)
                        text_layer.crop((rx, ry, rx + rw, ry + rh)).save(layer_file_path)
                        print(f"[RECOMPOSITE][LAYER_FILE_UPDATED] name={name!r} path={layer_file_path}", flush=True)
                    except Exception as lf_exc:
                        print(f"[RECOMPOSITE][LAYER_FILE_FAIL] name={name!r} err={lf_exc}", flush=True)
                continue

            layer = _find_layer(psd, name)
            if layer is None:
                print(f"[RECOMPOSITE][LAYER_NOT_FOUND] name={name!r}", flush=True)
                continue
            try:
                img = layer.composite()
            except Exception as exc:
                print(f"[RECOMPOSITE][COMPOSITE_SKIP] name={name!r} err={exc}", flush=True)
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
            print(f"[RECOMPOSITE][LAYER_PLACED] name={name!r} pos=({lyr['render_x']},{lyr['render_y']}) size={new_w}x{new_h}", flush=True)

        canvas.convert("RGB").save(result_path)
        print(f"[RECOMPOSITE][DONE] jobId={job_id} placed={placed} result={result_path}", flush=True)
        return jsonify({"resultPath": result_path, "placedCount": placed})

    except Exception as exc:
        print(f"[RECOMPOSITE][ERROR] jobId={job_id} err={exc}", flush=True)
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
