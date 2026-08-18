"""TYPE F P3: fal-ai/object-removal 텍스트·오버레이 제거 → clean_canonical.png.

prompt: "text, logos, badges, watermarks"
FAL_KEY 없거나 API 실패 시 canonical pass-through (PASS — 이 단계는 선택적).

Outputs under output/{jobId}/clean_v1/03_bg_cleanup/:
  clean_canonical.png
"""
from __future__ import annotations

import io
import os
import shutil
import ssl
import time
import urllib.request
from pathlib import Path

from PIL import Image

from clean_pipeline.contracts import PipelineStatus, StageName, StageResult
from clean_pipeline.pipeline_logger import PipelineLogger

STAGE = StageName.BACKGROUND_CLEANUP
_OUTPUT_SUBDIR = Path("clean_v1") / "03_bg_cleanup"
_FAL_ENDPOINT = "fal-ai/object-removal"
_REMOVAL_PROMPT = "text, letters, Korean characters, logos, badges"


def clean(
    canonical_path: str,
    output_dir: str,
    job_id: str,
    logger: PipelineLogger,
) -> tuple[StageResult, str]:
    """fal-ai/object-removal 호출, 실패 시 canonical pass-through.

    Returns (StageResult, clean_canonical_path):
      clean_canonical_path: 텍스트·오버레이 제거된 이미지 경로
                            (실패 시 canonical_path 그대로)
    """
    stage_dir = Path(output_dir) / job_id / _OUTPUT_SUBDIR
    stage_dir.mkdir(parents=True, exist_ok=True)

    fal_key = os.environ.get("FAL_KEY", "")
    clean_path = str(stage_dir / "clean_canonical.png")

    logger.stage_start(
        STAGE.value,
        f"object-removal prompt='{_REMOVAL_PROMPT}' fal={'set' if fal_key else 'missing'}",
        metrics={"hasFalKey": bool(fal_key), "prompt": _REMOVAL_PROMPT},
    )

    if not fal_key:
        logger.artifact_written(STAGE.value, "(skip)", "FAL_KEY 없음 — canonical pass-through")
        shutil.copy2(canonical_path, clean_path)
        return _pass_through(logger, clean_path, "FAL_KEY 없음")

    try:
        canonical = Image.open(canonical_path).convert("RGB")
    except Exception as exc:
        logger.artifact_written(STAGE.value, "(warn)", f"canonical 로드 실패: {exc} — pass-through")
        shutil.copy2(canonical_path, clean_path)
        return _pass_through(logger, clean_path, f"canonical 로드 실패: {exc}")

    try:
        result_img, fal_meta = _call_object_removal(canonical, fal_key)
        result_img.save(clean_path)
        logger.artifact_written(
            STAGE.value, clean_path,
            f"object-removal OK ({fal_meta['durationSec']}s requestId={fal_meta.get('requestId', 'n/a')})",
        )
        logger.stage_pass(
            STAGE.value,
            f"PASS — clean_canonical.png 생성",
            metrics={"fal": fal_meta, "passThrough": False},
        )
        return StageResult(
            stage=STAGE,
            status=PipelineStatus.PASS,
            metrics={"fal": fal_meta, "passThrough": False},
            artifacts={"clean": clean_path},
        ), clean_path

    except Exception as exc:
        logger.artifact_written(STAGE.value, "(warn)", f"object-removal 실패: {exc} — pass-through")
        shutil.copy2(canonical_path, clean_path)
        return _pass_through(logger, clean_path, f"API 실패: {exc}")


# ── fal-ai 호출 ───────────────────────────────────────────────────────────────


def _call_object_removal(img: Image.Image, fal_key: str) -> tuple[Image.Image, dict]:
    """fal-ai/object-removal 호출. (RGB PIL Image, fal_meta dict) 반환."""
    import fal_client
    os.environ.setdefault("FAL_KEY", fal_key)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    image_url = fal_client.upload(buf.read(), content_type="image/png")

    request_id_holder: dict = {}

    def on_queue_update(update):
        if isinstance(update, fal_client.InProgress):
            if hasattr(update, "request_id") and update.request_id:
                request_id_holder["id"] = update.request_id
            for log in update.logs:
                print(log["message"])

    t0 = time.time()
    result = fal_client.subscribe(
        _FAL_ENDPOINT,
        arguments={"image_url": image_url, "prompt": _REMOVAL_PROMPT},
        with_logs=True,
        on_queue_update=on_queue_update,
    )
    elapsed = round(time.time() - t0, 2)

    images_meta = result.get("images") or []
    url = images_meta[0].get("url", "") if images_meta else result.get("url", "")
    if not url:
        raise RuntimeError("fal-ai/object-removal: 응답에 URL 없음")

    ctx = ssl.create_default_context()
    with urllib.request.urlopen(url, timeout=60, context=ctx) as resp:
        raw = resp.read()

    result_img = Image.open(io.BytesIO(raw)).convert("RGB")
    fal_meta = {
        "endpoint": _FAL_ENDPOINT,
        "requestId": request_id_holder.get("id"),
        "durationSec": elapsed,
        "outputUrl": url,
        "outputWidth": images_meta[0].get("width") if images_meta else None,
        "outputHeight": images_meta[0].get("height") if images_meta else None,
    }
    return result_img, fal_meta


# ── Helper ────────────────────────────────────────────────────────────────────


def _pass_through(
    logger: PipelineLogger, clean_path: str, reason: str
) -> tuple[StageResult, str]:
    logger.stage_pass(
        STAGE.value,
        f"PASS (pass-through) — {reason}",
        metrics={"passThrough": True},
    )
    return StageResult(
        stage=STAGE,
        status=PipelineStatus.PASS,
        metrics={"passThrough": True},
        artifacts={"clean": clean_path},
    ), clean_path
