"""creative-segmentation-ai Flask 서비스.

Port: 8090 (Docker 내부 네트워크 전용, 운영 nginx에 노출 안 함)

Endpoints:
  GET  /health         — 프로세스 생존 + fallback 포함 서비스 가능 여부
  GET  /ready          — strict readiness: GDINO AND SAM2 실제 추론 모두 준비 완료
  POST /v1/segment     — 이미지 segmentation (multipart/form-data)

Stage 18.1 변경:
  - PSD 입력 시 psd-tools composite 우선, Pillow fallback 유지
  - DetectionResult에 handOverlapRatio / personOverlapRatio 포함
  - score_external_mask에 overlap 비율 전달 (hand/person leak 감점)
  - scoreBreakdown 응답 포함

Stage 18.2 변경:
  - _parse_image: apply_icc=False 2차 시도, flatten_meta dict 반환
  - flattenMethod: "psd_tools" → "psd_tools_composite"
  - SegmentationResponse에 flattenMeta 포함
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

MIN_CONFIDENCE       = float(os.environ.get("CREATIVE_SEGMENTATION_MIN_CONFIDENCE", "0.25"))
MAX_IMAGE_SIDE       = int(os.environ.get("CREATIVE_SEGMENTATION_MAX_IMAGE_SIDE", "1280"))
MASK_SCORE_THRESHOLD = float(os.environ.get("CREATIVE_SEGMENTATION_MASK_SCORE_THRESHOLD", "70"))

_PRELOAD = os.environ.get("PRELOAD_MODELS", "false").lower() == "true"

_PSD_MAGIC = b"8BPS"


# ── startup ───────────────────────────────────────────────────────────────────

if _PRELOAD:
    log.info("PRELOAD_MODELS=true: 백그라운드 모델 로드 시작")
    model_loader.preload()


# ── PSD 파싱 ─────────────────────────────────────────────────────────────────

def _parse_image(image_bytes: bytes) -> tuple[Image.Image, str, dict]:
    """이미지 bytes → (PIL RGB, flatten_method, flatten_meta).

    PSD:
      1차: psd.composite() 기본값
      2차: psd.composite(apply_icc=False)  ← ICC 프로필 오류 우회
      3차: Pillow 직접 로드 (pillow_psd_fallback)
    기타: Pillow 직접 로드 (pillow).
    """
    if image_bytes[:4] != _PSD_MAGIC:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        return img, "pillow", {}

    meta: dict = {}

    # psd-tools 설치 확인
    try:
        import importlib.metadata as _im
        meta["psdToolsVersion"]   = _im.version("psd-tools")
        meta["psdToolsInstalled"] = True
    except Exception as _e:
        meta["psdToolsInstalled"]      = False
        meta["psdToolsVersion"]        = None
        meta["psdToolsImportErrorType"] = type(_e).__name__

    if meta.get("psdToolsInstalled"):
        try:
            from psd_tools import PSDImage
            psd = PSDImage.open(io.BytesIO(image_bytes))
            meta["psdLayerCount"] = len(list(psd))
            meta["psdSize"]       = f"{psd.width}x{psd.height}"

            # 1차: composite() 기본값
            try:
                composite = psd.composite()
                if composite is None:
                    raise ValueError("psd.composite() returned None")
                meta["psdCompositeCreated"]   = True
                meta["psdFlattenAttempt"]     = "default"
                log.info(
                    "PSD composite (psd-tools): size=%dx%d layers=%d",
                    psd.width, psd.height, meta["psdLayerCount"],
                )
                return composite.convert("RGB"), "psd_tools_composite", meta
            except Exception as _e1:
                meta["psdFlattenErrorType"]    = type(_e1).__name__
                meta["psdFlattenErrorMessage"] = str(_e1)[:300]
                log.warning("psd.composite() 실패: %s", _e1)

            # 2차: apply_icc=False (ICC 프로필 문제 우회)
            try:
                composite = psd.composite(apply_icc=False)
                if composite is None:
                    raise ValueError("psd.composite(apply_icc=False) returned None")
                meta["psdCompositeCreated"] = True
                meta["psdFlattenAttempt"]   = "apply_icc_false"
                log.info(
                    "PSD composite (apply_icc=False): size=%dx%d layers=%d",
                    psd.width, psd.height, meta["psdLayerCount"],
                )
                return composite.convert("RGB"), "psd_tools_composite", meta
            except Exception as _e2:
                meta["psdFlattenError2Type"]    = type(_e2).__name__
                meta["psdFlattenError2Message"] = str(_e2)[:300]
                meta["psdCompositeCreated"]     = False
                log.warning("psd.composite(apply_icc=False) 실패: %s", _e2)

        except Exception as _e_open:
            meta["psdToolsOpenError"] = str(_e_open)[:300]
            log.warning("psd-tools open 실패 → Pillow fallback: %s", _e_open)

    # 3차: Pillow fallback
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        meta.setdefault("psdCompositeCreated", False)
        return img, "pillow_psd_fallback", meta
    except Exception as _e_pil:
        raise ValueError(f"PSD image_parse_failed: {_e_pil}") from _e_pil


# ── health ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    """모델 로드 상태 조회 — 절대 모델 다운로드를 새로 시작하지 않는다."""
    state    = model_loader.get_state()
    provider = model_loader.get_provider()
    diag     = model_loader.get_diagnostics()
    meta     = provider.get_metadata() if provider else {}

    if state == model_loader.READY:
        status_str  = "ok"
        status_code = 200
    elif state == model_loader.LOADING:
        status_str  = "loading"
        status_code = 503
    elif state == model_loader.FAILED:
        status_str  = "error"
        status_code = 503
    else:  # NOT_STARTED
        status_str  = "not_started"
        status_code = 503

    resp = HealthResponse(
        status                    = status_str,
        provider                  = os.environ.get("CREATIVE_SEGMENTATION_PROVIDER", "grounded-sam2"),
        device                    = provider.device if provider else "unknown",
        models_loaded             = provider is not None and provider.models_loaded,
        model_load_error          = diag.get("modelLoadError") or "",
        real_inference_available  = meta.get("externalModelRealInference", False),
        grounding_dino_model_id   = meta.get("groundingDinoModelId", ""),
        sam2_model_id             = meta.get("sam2ModelId", ""),
        bbox_fallback_enabled     = meta.get("bboxFallbackEnabled", True),
        model_cache_path          = meta.get("modelCachePath", ""),
        model_load_state          = diag.get("modelLoadState", state),
        model_load_attempt        = diag.get("modelLoadAttempt", 0),
        concurrent_load_prevented = diag.get("concurrentLoadPrevented", 0),
        model_load_ms             = diag.get("modelLoadMs", 0),
        model_load_started_at     = diag.get("modelLoadStartedAt"),
        model_load_completed_at   = diag.get("modelLoadCompletedAt"),
        grounding_dino_cache_ready = meta.get("groundingDinoCacheReady", False),
        sam2_cache_ready           = meta.get("sam2CacheReady", False),
        grounding_dino_ready           = meta.get("groundingDinoReady", False),
        grounding_dino_real_inference  = meta.get("groundingDinoRealInference", False),
        sam2_checkpoint_ready          = meta.get("sam2CheckpointReady", False),
        sam2_model_ready               = meta.get("sam2ModelReady", False),
        sam2_predictor_ready           = meta.get("sam2PredictorReady", False),
        sam2_real_inference            = meta.get("sam2RealInference", False),
        sam2_load_error_type           = meta.get("sam2LoadErrorType", ""),
        sam2_load_error_message        = meta.get("sam2LoadErrorMessage", ""),
        sam2_config_used               = meta.get("sam2ConfigUsed", ""),
    )
    return jsonify(resp.to_dict()), status_code


# ── ready (strict) ────────────────────────────────────────────────────────────

@app.get("/ready")
def ready():
    """Strict readiness: GDINO AND SAM2 실제 추론 모두 준비 완료여야 HTTP 200."""
    state    = model_loader.get_state()
    provider = model_loader.get_provider()

    if state != model_loader.READY or provider is None:
        return jsonify({
            "ready":       False,
            "modelState":  state,
            "reason":      f"provider_not_ready:{state}",
        }), 503

    meta     = provider.get_metadata()
    gdino_ok = meta.get("groundingDinoRealInference", False)
    sam2_ok  = meta.get("sam2RealInference", False)
    real_ok  = gdino_ok and sam2_ok

    resp_body: dict = {
        "ready":                  real_ok,
        "groundingDinoOk":        gdino_ok,
        "sam2Ok":                 sam2_ok,
        "realInferenceAvailable": real_ok,
        "device":                 meta.get("device", provider.device),
        "sam2CheckpointReady":    meta.get("sam2CheckpointReady", False),
        "sam2ModelReady":         meta.get("sam2ModelReady", False),
        "sam2PredictorReady":     meta.get("sam2PredictorReady", False),
        "sam2ConfigUsed":         meta.get("sam2ConfigUsed", ""),
    }
    if not real_ok:
        resp_body["sam2LoadErrorType"]    = meta.get("sam2LoadErrorType", "")
        resp_body["sam2LoadErrorMessage"] = meta.get("sam2LoadErrorMessage", "")
    if not gdino_ok:
        resp_body["groundingDinoModelId"] = meta.get("groundingDinoModelId", "")

    return jsonify(resp_body), (200 if real_ok else 503)


# ── segment ───────────────────────────────────────────────────────────────────

@app.post("/v1/segment")
def segment():
    t0         = time.time()
    request_id = request.form.get("requestId") or str(uuid.uuid4())
    source_type = request.form.get("sourceType", "unknown")

    # ── 이미지 파싱 (PSD 지원) ────────────────────────────────────────────────
    if "image" not in request.files:
        return jsonify({"error": "image field required"}), 400

    image_bytes = request.files["image"].read()
    try:
        pil_image, flatten_method, flatten_meta = _parse_image(image_bytes)
    except Exception as e:
        return jsonify({"error": str(e)}), 400

    if flatten_method != "pillow":
        log.info("이미지 파싱 방식: %s size=%dx%d", flatten_method, pil_image.width, pil_image.height)

    # ── prompts 파싱 ───────────────────────────────────────────────────────────
    prompts_raw = request.form.get("prompts", "[]")
    try:
        prompts = [PromptSchema.from_dict(p) for p in json.loads(prompts_raw)]
    except Exception as e:
        return jsonify({"error": f"prompts_parse_failed: {e}"}), 400
    if not prompts:
        prompts = _default_prompts()

    # ── 캐시 확인 ──────────────────────────────────────────────────────────────
    cache_key = cache.compute_cache_key(image_bytes, prompts_raw, MIN_CONFIDENCE)
    cached    = cache.get(cache_key)
    if cached is not None:
        cached["requestId"] = request_id
        cached["cacheHit"]  = True
        return jsonify(cached), 200

    # ── 모델 상태 확인 ─────────────────────────────────────────────────────────
    state    = model_loader.get_state()
    provider = model_loader.get_provider()

    if provider is None:
        if state == model_loader.NOT_STARTED:
            log.info("lazy load 시작")
            model_loader.preload()
            return jsonify({
                "requestId":    request_id,
                "provider":     "grounded-sam2",
                "device":       "unknown",
                "processingMs": int((time.time() - t0) * 1000),
                "detections":   [],
                "warnings":     ["model_loading_retry_later"],
            }), 503
        elif state == model_loader.LOADING:
            return jsonify({
                "requestId":    request_id,
                "provider":     "grounded-sam2",
                "device":       "unknown",
                "processingMs": int((time.time() - t0) * 1000),
                "detections":   [],
                "warnings":     ["model_loading_retry_later"],
            }), 503
        else:  # FAILED
            return jsonify({
                "requestId": request_id,
                "error":     f"model_load_failed: {model_loader.get_load_error()}",
            }), 503

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

    if flatten_method not in ("pillow",):
        warnings.append(f"flatten_method:{flatten_method}")

    # ── DetectionResult 변환 ──────────────────────────────────────────────────
    detections: list[DetectionResult] = []
    for d in raw_detections:
        bbox_dict  = d.get("bbox", {})
        mask_pil   = d.get("_maskPil")

        quality = score_external_mask(
            detection_confidence = d.get("detectionConfidence", 0.0),
            mask_confidence      = d.get("maskConfidence", 0.0),
            mask_pil             = mask_pil,
            bbox                 = bbox_dict,
            canvas_w             = canvas_w,
            canvas_h             = canvas_h,
            role                 = d.get("role", "product"),
            fragment_count       = d.get("fragmentCount", 1),
            hand_overlap_ratio   = d.get("handOverlapRatio", 0.0),
            person_overlap_ratio = d.get("personOverlapRatio", 0.0),
        )

        if quality["hardFail"]:
            warnings.append(
                f"detection_hard_fail:{d.get('detectionId','?')}:{quality['hardFailReason']}"
            )
            continue

        det = DetectionResult(
            detection_id          = d.get("detectionId", ""),
            role                  = d.get("role", "product"),
            prompt                = d.get("prompt", ""),
            bbox                  = BboxSchema.from_dict(bbox_dict),
            detection_confidence  = d.get("detectionConfidence", 0.0),
            mask_confidence       = d.get("maskConfidence", 0.0),
            mask_png_base64       = d.get("maskPngBase64", ""),
            mask_area_ratio       = d.get("maskAreaRatio", 0.0),
            edge_sharpness        = quality["edgeSharpness"],
            fragment_count        = d.get("fragmentCount", 1),
            mask_quality_score    = quality["overallMaskScore"],
            leak_risk             = quality["leakRisk"],
            hard_fail             = quality.get("hardFail", False),
            mask_source           = d.get("maskSource", "real_sam2"),
            hand_overlap_ratio    = d.get("handOverlapRatio", 0.0),
            person_overlap_ratio  = d.get("personOverlapRatio", 0.0),
            hand_subtract_applied = d.get("handSubtractApplied", False),
            score_breakdown       = quality.get("scoreBreakdown", {}),
            # Stage 18.2: 경계 품질 + completeness
            edge_pixel_count        = quality.get("edgePixelCount", 0),
            raw_boundary_gradient   = quality.get("rawBoundaryGradient", 0.0),
            low_contrast_edge_ratio = quality.get("lowContrastEdgeRatio", 0.0),
            completeness_metrics    = quality.get("completenessMetrics", {}),
        )
        detections.append(det)

    processing_ms = int((time.time() - t0) * 1000)
    log.info(
        "segment 완료: requestId=%s source=%s flatten=%s detections=%d warnings=%d ms=%d",
        request_id, source_type, flatten_method, len(detections), len(warnings), processing_ms,
    )

    resp   = SegmentationResponse(
        request_id    = request_id,
        provider      = provider.name,
        device        = provider.device,
        processing_ms = processing_ms,
        detections    = detections,
        warnings      = warnings,
        flatten_method = flatten_method,
        flatten_meta   = flatten_meta,
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
        PromptSchema(role="hand",   texts=["hand", "hands"]),
        PromptSchema(role="logo",   texts=["brand logo", "logo"]),
        PromptSchema(role="cta",    texts=["button", "call to action button"], experimental=True),
    ]


# ── 진입점 ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port  = int(os.environ.get("PORT", 8090))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug, threaded=True)
