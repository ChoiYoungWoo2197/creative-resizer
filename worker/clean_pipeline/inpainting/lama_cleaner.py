"""P2.5 INPAINTING_CLEANUP — title_group/logo 오버레이 텍스트를 lama로 제거.

FAL_KEY 미설정 또는 lama API 실패 시 canonical_path 그대로 반환 (pass-through).
마스크 규칙:
  - title_group: bbox y2를 이미지 하단까지 확장 (bbox 아래 잔류 텍스트 방지)
  - logo: bbox + MASK_PADDING 사방
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

from PIL import Image, ImageDraw

from clean_pipeline.analysis.models import SceneManifest
from clean_pipeline.contracts import PipelineStatus, StageName, StageResult
from clean_pipeline.pipeline_logger import PipelineLogger

STAGE = StageName.INPAINTING_CLEANUP
_OUTPUT_SUBDIR = Path("clean_v1") / "02.5_inpainting"
_MASK_PADDING = 10
_LAMA_ENDPOINT = "https://fal.run/fal-ai/lama"
_INPAINT_ROLES = {"title_group", "logo"}
_ssl_ctx = ssl.create_default_context()


def clean(
    canonical_path: str,
    manifest: SceneManifest,
    image_width: int,
    image_height: int,
    output_dir: str,
    job_id: str,
    logger: PipelineLogger,
) -> tuple[StageResult, str]:
    """title_group/logo bbox를 lama로 인페인트한 clean_canonical 경로 반환."""
    fal_key = os.environ.get("FAL_KEY", "")
    stage_dir = Path(output_dir) / job_id / _OUTPUT_SUBDIR
    stage_dir.mkdir(parents=True, exist_ok=True)

    logger.stage_start(
        STAGE.value,
        f"canonical={canonical_path} fal_key={'set' if fal_key else 'missing'}",
        metrics={"hasFalKey": bool(fal_key)},
    )

    if not fal_key:
        logger.artifact_written(STAGE.value, "(skip)", "FAL_KEY not set — pass-through")
        return _pass_through(logger, canonical_path)

    targets = [o for o in manifest.objects if o.role in _INPAINT_ROLES]
    if not targets:
        logger.artifact_written(STAGE.value, "(skip)", "no title_group/logo objects — pass-through")
        return _pass_through(logger, canonical_path)

    try:
        img = Image.open(canonical_path).convert("RGB")
    except Exception as exc:
        logger.artifact_written(STAGE.value, "(skip)", f"image load failed: {exc} — pass-through")
        return _pass_through(logger, canonical_path)

    W, H = img.size
    mask = Image.new("RGB", (W, H), (0, 0, 0))
    draw = ImageDraw.Draw(mask)

    for obj in targets:
        bx1 = max(0, obj.bbox.x - _MASK_PADDING)
        by1 = max(0, obj.bbox.y - _MASK_PADDING)
        bx2 = min(W, obj.bbox.x + obj.bbox.width + _MASK_PADDING)
        # title_group은 bbox 하단을 이미지 끝까지 확장
        by2 = H if obj.role == "title_group" else min(H, obj.bbox.y + obj.bbox.height + _MASK_PADDING)
        draw.rectangle([bx1, by1, bx2, by2], fill=(255, 255, 255))
        logger.artifact_written(
            STAGE.value, "(mask)",
            f"id={obj.id} role={obj.role} rect=({bx1},{by1})→({bx2},{by2})",
        )

    try:
        result_img = _call_lama(img, mask, fal_key)
    except Exception as exc:
        logger.artifact_written(STAGE.value, "(skip)", f"lama failed: {exc} — pass-through")
        return _pass_through(logger, canonical_path)

    clean_path = str(stage_dir / "clean_canonical.png")
    result_img.save(clean_path)
    logger.artifact_written(STAGE.value, clean_path, f"lama inpaint done ({len(targets)} regions)")

    logger.stage_pass(
        STAGE.value,
        f"{len(targets)} regions inpainted",
        metrics={"regionCount": len(targets)},
    )
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
