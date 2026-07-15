"""Worker → creative-segmentation-ai HTTP 클라이언트.

기능 flag (환경변수):
  CREATIVE_EXTERNAL_SEGMENTATION_ENABLED=false  (기본 OFF)
  CREATIVE_SEGMENTATION_URL=http://creative-segmentation-ai:8090
  CREATIVE_SEGMENTATION_TIMEOUT_SECONDS=120

실패 시 ([], warning_list) 반환 — job을 죽이지 않는다.
"""

from __future__ import annotations
import os
import io
import json
import logging
import time
import uuid

log = logging.getLogger("worker.external_segmentation_client")

# ── 환경변수 ────────────────────────────────────────────────────────────────────

_ENABLED = os.environ.get("CREATIVE_EXTERNAL_SEGMENTATION_ENABLED", "false").lower() == "true"
_COMPARE_ONLY = os.environ.get("CREATIVE_EXTERNAL_SEGMENTATION_COMPARE_ONLY", "true").lower() == "true"
_SERVICE_URL = os.environ.get("CREATIVE_SEGMENTATION_URL", "http://creative-segmentation-ai:8090")
_TIMEOUT = int(os.environ.get("CREATIVE_SEGMENTATION_TIMEOUT_SECONDS", "120"))
_MIN_CONFIDENCE = float(os.environ.get("CREATIVE_SEGMENTATION_MIN_CONFIDENCE", "0.25"))
_MAX_IMAGE_SIDE = int(os.environ.get("CREATIVE_SEGMENTATION_MAX_IMAGE_SIDE", "1280"))


def is_enabled() -> bool:
    return _ENABLED


def is_compare_only() -> bool:
    return _COMPARE_ONLY


def call_segment(
    image,                          # PIL Image
    source_type: str = "unknown",
    target_roles: list[str] | None = None,
    custom_prompts: list[dict] | None = None,
    request_id: str | None = None,
    job_id: str | None = None,
) -> tuple[list[dict], dict]:
    """외부 segmentation 서비스 호출.

    반환: (detections: list[dict], metadata: dict)
    실패: ([], metadata_with_error)
    """
    prefix = f"[{job_id or 'job'}][ExtSeg]"
    req_id = request_id or str(uuid.uuid4())
    t0 = time.time()

    metadata: dict = {
        "externalSegmentationAttempted": False,
        "externalSegmentationUsed":      False,
        "externalSegmentationCompareOnly": _COMPARE_ONLY,
        "externalSegmentationError":     None,
        "externalProcessingMs":          0,
        "externalDevice":                "unknown",
        "segmentationProvider":          "grounded-sam2",
    }

    if not _ENABLED:
        return [], metadata

    metadata["externalSegmentationAttempted"] = True

    # ── 이미지 → bytes ──────────────────────────────────────────────────────────
    try:
        buf = io.BytesIO()
        rgb = image.convert("RGB") if image.mode != "RGB" else image
        rgb.save(buf, format="PNG")
        image_bytes = buf.getvalue()
    except Exception as e:
        metadata["externalSegmentationError"] = f"image_encode_failed:{e}"
        return [], metadata

    # ── prompts 구성 ────────────────────────────────────────────────────────────
    if custom_prompts:
        prompts_json = json.dumps(custom_prompts)
    else:
        prompts_json = json.dumps(_build_default_prompts(target_roles))

    # ── HTTP 요청 ───────────────────────────────────────────────────────────────
    try:
        import requests
        resp = requests.post(
            f"{_SERVICE_URL}/v1/segment",
            files={"image": ("input.png", image_bytes, "image/png")},
            data={
                "requestId":   req_id,
                "sourceType":  source_type,
                "prompts":     prompts_json,
            },
            timeout=_TIMEOUT,
        )
    except Exception as e:
        elapsed = int((time.time() - t0) * 1000)
        err = f"http_request_failed:{type(e).__name__}:{e}"
        log.warning("%s %s", prefix, err)
        metadata["externalSegmentationError"] = err
        metadata["externalProcessingMs"] = elapsed
        return [], metadata

    elapsed = int((time.time() - t0) * 1000)
    metadata["externalProcessingMs"] = elapsed

    if resp.status_code != 200:
        err = f"http_{resp.status_code}:{resp.text[:200]}"
        log.warning("%s HTTP 오류: %s", prefix, err)
        metadata["externalSegmentationError"] = err
        return [], metadata

    try:
        body = resp.json()
    except Exception as e:
        metadata["externalSegmentationError"] = f"json_parse_failed:{e}"
        return [], metadata

    detections = body.get("detections", [])
    warnings = body.get("warnings", [])
    metadata["externalDevice"] = body.get("device", "unknown")

    if warnings:
        log.info("%s 서비스 warnings: %s", prefix, warnings)

    log.info(
        "%s 완료: detections=%d ms=%d device=%s",
        prefix, len(detections), elapsed, metadata["externalDevice"],
    )

    return detections, metadata


def check_health() -> dict:
    """서비스 health 체크. 실패 시 {"status": "unreachable"} 반환."""
    try:
        import requests
        resp = requests.get(f"{_SERVICE_URL}/health", timeout=5)
        if resp.status_code == 200:
            return resp.json()
        return {"status": "error", "code": resp.status_code}
    except Exception as e:
        return {"status": "unreachable", "error": str(e)}


# ── 기본 프롬프트 ─────────────────────────────────────────────────────────────

def _build_default_prompts(target_roles: list[str] | None) -> list[dict]:
    all_prompts = [
        {
            "role": "product",
            "texts": [
                "cosmetic product", "product tube", "cosmetic tube",
                "product bottle", "cosmetic bottle", "product package",
                "cream jar", "skincare product", "makeup product",
                "product container",
            ],
        },
        {
            "role": "person",
            "texts": ["person", "woman", "man", "model", "face"],
        },
        {
            "role": "hand",
            "texts": ["hand", "hands", "fingers"],
        },
        {
            "role": "logo",
            "texts": ["brand logo", "logo", "brand symbol"],
        },
    ]
    if target_roles:
        return [p for p in all_prompts if p["role"] in target_roles]
    return all_prompts
