"""P2.5 INPAINTING_CLEANUP: fal-ai/lama API로 텍스트 오버레이 제거 → clean_canonical 생성.

Input:  canonical.png  +  inpaint_mask.png (P2 SCENE_ANALYSIS에서 생성)
Output: clean_canonical.png (canonical과 동일 크기)

Fallback:
  - FAL_KEY 미설정 → canonical.png pass-through
  - inpaint_mask 없음 / all-black → pass-through
  - API 호출 실패 → pass-through
  - 결과 크기 불일치 → LANCZOS resize로 보정
"""
from __future__ import annotations

import base64
import io
import json
import os
import ssl
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

from clean_pipeline.contracts import PipelineStatus, StageName, StageResult
from clean_pipeline.pipeline_logger import PipelineLogger

STAGE = StageName.INPAINTING_CLEANUP
_OUTPUT_SUBDIR = Path("clean_v1") / "02.5_inpainting"
_LAMA_ENDPOINT = "https://fal.run/fal-ai/lama"
_ssl_ctx = ssl.create_default_context()


def clean(
    canonical_path: str,
    inpaint_mask_path: str,
    image_width: int,
    image_height: int,
    output_dir: str,
    job_id: str,
    logger: PipelineLogger,
) -> tuple[StageResult, str]:
    """P2에서 생성된 inpaint_mask로 fal-ai/lama 인페인팅 → clean_canonical 경로 반환."""
    fal_key = os.environ.get("FAL_KEY", "")
    stage_dir = Path(output_dir) / job_id / _OUTPUT_SUBDIR
    stage_dir.mkdir(parents=True, exist_ok=True)

    logger.stage_start(
        STAGE.value,
        f"endpoint=fal-ai/lama mask={inpaint_mask_path} fal_key={'set' if fal_key else 'missing'}",
        metrics={"hasFalKey": bool(fal_key)},
    )

    if not fal_key:
        logger.artifact_written(STAGE.value, "(skip)", "FAL_KEY not set — pass-through")
        return _pass_through(logger, canonical_path)

    # ── inpaint_mask 유효성 확인 ──────────────────────────────────────────────
    if not inpaint_mask_path or not Path(inpaint_mask_path).exists():
        logger.artifact_written(STAGE.value, "(skip)", "inpaint_mask not found — pass-through")
        return _pass_through(logger, canonical_path)

    try:
        mask = Image.open(inpaint_mask_path).convert("RGB")
        if np.array(mask).max() == 0:
            logger.artifact_written(STAGE.value, "(skip)", "inpaint_mask all-black — no targets")
            return _pass_through(logger, canonical_path)
    except Exception as exc:
        logger.artifact_written(STAGE.value, "(skip)", f"mask load failed: {exc} — pass-through")
        return _pass_through(logger, canonical_path)

    # ── canonical 로드 + 규격 검증 ────────────────────────────────────────────
    try:
        img = Image.open(canonical_path).convert("RGB")
    except Exception as exc:
        logger.artifact_written(STAGE.value, "(skip)", f"image load failed: {exc} — pass-through")
        return _pass_through(logger, canonical_path)

    W, H = img.size
    assert (W, H) == (image_width, image_height), (
        f"canonical size ({W},{H}) != declared ({image_width},{image_height})"
    )

    # ── fal-ai/lama 호출 ──────────────────────────────────────────────────────
    try:
        result_img = _call_lama(img, mask, fal_key)
    except Exception as exc:
        logger.artifact_written(STAGE.value, "(warn)", f"lama failed: {exc} — pass-through")
        return _pass_through(logger, canonical_path)

    # ── 크기 보정: 미세 오차 시 LANCZOS resize ────────────────────────────────
    if result_img.size != (W, H):
        logger.artifact_written(
            STAGE.value, "(resize)",
            f"lama returned {result_img.size} != original ({W},{H}) — LANCZOS resize",
        )
        result_img = result_img.resize((W, H), Image.LANCZOS)

    assert result_img.size == (W, H), f"post-resize size mismatch: {result_img.size}"

    clean_path = str(stage_dir / "clean_canonical.png")
    result_img.save(clean_path)
    logger.artifact_written(STAGE.value, clean_path, "clean_canonical saved (fal-ai/lama)")

    logger.stage_pass(STAGE.value, "lama inpainting done", metrics={"resultSize": f"{W}x{H}"})
    return StageResult(
        stage=STAGE,
        status=PipelineStatus.PASS,
        artifacts={"clean_canonical": clean_path},
    ), clean_path


def _call_lama(img: Image.Image, mask: Image.Image, fal_key: str) -> Image.Image:
    img_uri = _to_data_uri(img)
    mask_uri = _to_data_uri(mask)

    try:
        import fal_client
        os.environ["FAL_KEY"] = fal_key
        result = fal_client.subscribe(
            "fal-ai/lama",
            arguments={"image_url": img_uri, "mask_image_url": mask_uri},
        )
        result_url = (result.get("image") or {}).get("url", "") or result.get("url", "")
    except ImportError:
        headers = {
            "Authorization": f"Key {fal_key}",
            "Content-Type": "application/json",
        }
        body = json.dumps({"image_url": img_uri, "mask_image_url": mask_uri}).encode("utf-8")
        req = urllib.request.Request(_LAMA_ENDPOINT, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=90, context=_ssl_ctx) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            raise RuntimeError(
                f"lama HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')[:200]}"
            )
        result_url = (data.get("image") or {}).get("url", "") or data.get("url", "")

    if not result_url:
        raise RuntimeError("lama returned no result URL")

    raw = _http_get(result_url)
    return Image.open(io.BytesIO(raw)).convert("RGB")


def _to_data_uri(pil_img: Image.Image) -> str:
    buf = io.BytesIO()
    pil_img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _http_get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=30, context=_ssl_ctx) as resp:
        return resp.read()


def _pass_through(logger: PipelineLogger, canonical_path: str) -> tuple[StageResult, str]:
    logger.stage_pass(STAGE.value, "skipped — using original canonical", metrics={"skipped": True})
    return StageResult(
        stage=STAGE,
        status=PipelineStatus.PASS,
        artifacts={"clean_canonical": canonical_path},
    ), canonical_path
