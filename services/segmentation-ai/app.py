"""creative-segmentation-ai Flask 서비스.

Port: 8090 (Docker 내부 네트워크 전용, 운영 nginx에 노출 안 함)

Endpoints:
  GET  /health
  POST /v1/segment   (multipart/form-data)
"""

from __future__ import annotations
import os
import io
import json
import logging
import time
import uuid

from flask import Flask, request, jsonify
from PIL import Image

import model_loader
import cache
from schemas import PromptSchema, SegmentationResponse, DetectionResult, BboxSchema, HealthResponse
from mask_quality import score_external_mask

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("segmentation.app")

app = Flask(__name__)

# ── 설정 ─────────────────────────────────────────────────────────────────────

MIN_CONFIDENCE = float(os.environ.get("CREATIVE_SEGMENTATION_MIN_CONFIDENCE", "0.25"))
MAX_IMAGE_SIDE = int(os.environ.get("CREATIVE_SEGMENTATION_MAX_IMAGE_SIDE", "1280"))
MASK_SCORE_THRESHOLD = float(os.environ.get("CREATIVE_SEGMENTATION_MASK_SCORE_THRESHOLD", "70"))

# preload 모드 (PRELOAD_MODELS=true 이면 startup 시 즉시 로드)
_PRELOAD = os.environ.get("PRELOAD_MODELS", "false").lower() == "true"


# ── startup ───────────────────────────────────────────────────────────────────

if _PRELOAD:
    log.info("PRELOAD_MODELS=true: 백그라운드 모델 로드 시작")
    model_loader.preload()


# ── health ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    provider = model_loader.get_provider()
    err = model_loader.get_load_error()
    meta = provider.get_metadata() if provider else {}

    resp = HealthResponse(
        status="ok" if provider is not None else ("error" if err else "loading"),
        provider=os.environ.get("CREATIVE_SEGMENTATION_PROVIDER", "grounded-sam2"),
        device=provider.device if provider else "unknown",
        models_loaded=provider is not None and provider.models_loaded,
        model_load_error=err or "",
        real_inference_available=meta.get("externalModelRealInference", False),
        grounding_dino_model_id=meta.get("groundingDinoModelId", ""),
        sam2_model_id=meta.get("sam2ModelId", ""),
        bbox_fallback_enabled=meta.get("bboxFallbackEnabled", True),
        model_cache_path=meta.get("modelCachePath", ""),
    )
    status_code = 200 if resp.status == "ok" else 503
    return jsonify(resp.to_dict()), status_code


# ── segment ───────────────────────────────────────────────────────────────────

@app.post("/v1/segment")
def segment():
    t0 = time.time()
    request_id = request.form.get("requestId") or str(uuid.uuid4())
    source_type = request.form.get("sourceType", "unknown")

    # ── 이미지 파싱 ────────────────────────────────────────────────────────────
    if "image" not in request.files:
        return jsonify({"error": "image field required"}), 400

    image_file = request.files["image"]
    image_bytes = image_file.read()

    try:
        pil_image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    except Exception as e:
        return jsonify({"error": f"image_parse_failed: {e}"}), 400

    # ── prompts 파싱 ───────────────────────────────────────────────────────────
    prompts_raw = request.form.get("prompts", "[]")
    try:
        prompts_list = json.loads(prompts_raw)
        prompts = [PromptSchema.from_dict(p) for p in prompts_list]
    except Exception as e:
        return jsonify({"error": f"prompts_parse_failed: {e}"}), 400

    if not prompts:
        prompts = _default_prompts()

    # ── 캐시 확인 ──────────────────────────────────────────────────────────────
    cache_key = cache.compute_cache_key(image_bytes, prompts_raw, MIN_CONFIDENCE)
    cached = cache.get(cache_key)
    if cached is not None:
        cached["requestId"] = request_id
        cached["cacheHit"] = True
        return jsonify(cached), 200

    # ── 모델 로드 확인 ─────────────────────────────────────────────────────────
    provider = model_loader.get_provider()
    if provider is None:
        log.info("모델 미로드 — lazy loading 시작")
        model_loader.preload()
        # lazy load 시 첫 요청 실패 → 빈 결과 + warning
        resp = SegmentationResponse(
            request_id=request_id,
            provider=os.environ.get("CREATIVE_SEGMENTATION_PROVIDER", "grounded-sam2"),
            device="unknown",
            processing_ms=int((time.time() - t0) * 1000),
            detections=[],
            warnings=["model_loading_retry_later"],
        )
        return jsonify(resp.to_dict()), 503

    # ── segmentation 실행 ──────────────────────────────────────────────────────
    canvas_w, canvas_h = pil_image.width, pil_image.height
    try:
        raw_detections, warnings = provider.segment(
            pil_image, prompts,
            min_confidence=MIN_CONFIDENCE,
            max_image_side=MAX_IMAGE_SIDE,
        )
    except Exception as e:
        log.exception("segment 실행 실패: %s", e)
        raw_detections, warnings = [], [f"segment_exception:{e}"]

    # ── DetectionResult 변환 + mask quality 점수화 ─────────────────────────────
    detections: list[DetectionResult] = []
    for d in raw_detections:
        bbox_dict = d.get("bbox", {})
        mask_pil = d.get("_maskPil")

        quality = score_external_mask(
            detection_confidence=d.get("detectionConfidence", 0.0),
            mask_confidence=d.get("maskConfidence", 0.0),
            mask_pil=mask_pil,
            bbox=bbox_dict,
            canvas_w=canvas_w,
            canvas_h=canvas_h,
            role=d.get("role", "product"),
            fragment_count=d.get("fragmentCount", 1),
        )

        if quality["hardFail"]:
            warnings.append(
                f"detection_hard_fail:{d.get('detectionId','?')}:"
                f"{quality['hardFailReason']}"
            )
            continue

        det = DetectionResult(
            detection_id=d.get("detectionId", ""),
            role=d.get("role", "product"),
            prompt=d.get("prompt", ""),
            bbox=BboxSchema.from_dict(bbox_dict),
            detection_confidence=d.get("detectionConfidence", 0.0),
            mask_confidence=d.get("maskConfidence", 0.0),
            mask_png_base64=d.get("maskPngBase64", ""),
            mask_area_ratio=d.get("maskAreaRatio", 0.0),
            edge_sharpness=quality["edgeSharpness"],
            fragment_count=d.get("fragmentCount", 1),
            mask_quality_score=quality["overallMaskScore"],
            leak_risk=quality["leakRisk"],
            hard_fail=quality.get("hardFail", False),
            mask_source=d.get("maskSource", "real_sam2"),
        )
        detections.append(det)

    processing_ms = int((time.time() - t0) * 1000)
    log.info(
        "segment 완료: requestId=%s source=%s detections=%d warnings=%d ms=%d",
        request_id, source_type, len(detections), len(warnings), processing_ms,
    )

    resp = SegmentationResponse(
        request_id=request_id,
        provider=provider.name,
        device=provider.device,
        processing_ms=processing_ms,
        detections=detections,
        warnings=warnings,
    )
    result = resp.to_dict()
    cache.put(cache_key, result)
    return jsonify(result), 200


# ── 기본 프롬프트 ─────────────────────────────────────────────────────────────

def _default_prompts() -> list:
    return [
        PromptSchema(role="product", texts=[
            "cosmetic product", "product tube", "cosmetic tube",
            "product bottle", "cosmetic bottle", "product package",
            "cream jar", "skincare product", "makeup product",
        ]),
        PromptSchema(role="person", texts=["person", "woman", "man", "model"]),
        PromptSchema(role="hand", texts=["hand", "hands"]),
        PromptSchema(role="logo", texts=["brand logo", "logo"]),
        PromptSchema(role="cta", texts=["button", "call to action button"], experimental=True),
    ]


# ── 진입점 ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8090))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug, threaded=True)
