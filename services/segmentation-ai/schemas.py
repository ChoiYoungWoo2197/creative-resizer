"""Request / Response schemas for creative-segmentation-ai service.

JSON 직렬화 / 역직렬화에만 사용. Pydantic 의존 없이 dataclass로 구현.

Stage 18.1 변경:
  - DetectionResult: handOverlapRatio / personOverlapRatio / handSubtractApplied / scoreBreakdown 추가
  - SegmentationResponse: flattenMethod 추가
"""

from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class BboxSchema:
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0

    def to_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}

    @classmethod
    def from_dict(cls, d: dict) -> "BboxSchema":
        return cls(
            x=int(d.get("x", 0)),
            y=int(d.get("y", 0)),
            width=int(d.get("width", 0)),
            height=int(d.get("height", 0)),
        )


@dataclass
class PromptSchema:
    role: str = "product"
    texts: list[str] = field(default_factory=list)
    experimental: bool = False

    @classmethod
    def from_dict(cls, d: dict) -> "PromptSchema":
        return cls(
            role=d.get("role", "product"),
            texts=d.get("texts", []),
            experimental=bool(d.get("experimental", False)),
        )


@dataclass
class DetectionResult:
    detection_id: str = ""
    role: str = "product"
    prompt: str = ""
    bbox: BboxSchema = field(default_factory=BboxSchema)
    detection_confidence: float = 0.0
    mask_confidence: float = 0.0
    mask_png_base64: str = ""
    mask_area_ratio: float = 0.0
    edge_sharpness: float = 0.0
    fragment_count: int = 1
    mask_quality_score: float = 0.0
    leak_risk: float = 0.0
    hard_fail: bool = False
    mask_source: str = "real_sam2"
    # Stage 18.1: overlap / subtract / score breakdown
    hand_overlap_ratio: float = 0.0
    person_overlap_ratio: float = 0.0
    hand_subtract_applied: bool = False
    score_breakdown: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {
            "detectionId":          self.detection_id,
            "role":                 self.role,
            "prompt":               self.prompt,
            "bbox":                 self.bbox.to_dict(),
            "detectionConfidence":  round(self.detection_confidence, 4),
            "maskConfidence":       round(self.mask_confidence, 4),
            "maskPngBase64":        self.mask_png_base64,
            "maskAreaRatio":        round(self.mask_area_ratio, 4),
            "edgeSharpness":        round(self.edge_sharpness, 4),
            "fragmentCount":        self.fragment_count,
            "maskQualityScore":     round(self.mask_quality_score, 2),
            "leakRisk":             round(self.leak_risk, 4),
            "hardFail":             self.hard_fail,
            "maskSource":           self.mask_source,
            "handOverlapRatio":     round(self.hand_overlap_ratio, 4),
            "personOverlapRatio":   round(self.person_overlap_ratio, 4),
            "handSubtractApplied":  self.hand_subtract_applied,
        }
        if self.score_breakdown:
            d["scoreBreakdown"] = self.score_breakdown
        return d


@dataclass
class SegmentationResponse:
    request_id: str = ""
    provider: str = ""
    device: str = ""
    processing_ms: int = 0
    detections: list[DetectionResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    flatten_method: str = "pillow"

    def to_dict(self) -> dict:
        return {
            "requestId":     self.request_id,
            "provider":      self.provider,
            "device":        self.device,
            "processingMs":  self.processing_ms,
            "detections":    [d.to_dict() for d in self.detections],
            "warnings":      self.warnings,
            "flattenMethod": self.flatten_method,
        }


@dataclass
class HealthResponse:
    status: str = "ok"
    provider: str = ""
    device: str = ""
    models_loaded: bool = False
    model_load_error: str = ""
    real_inference_available: bool = False
    grounding_dino_model_id: str = ""
    sam2_model_id: str = ""
    bbox_fallback_enabled: bool = True
    model_cache_path: str = ""
    # single-flight 진단
    model_load_state: str = "NOT_STARTED"
    model_load_attempt: int = 0
    concurrent_load_prevented: int = 0
    model_load_ms: int = 0
    model_load_started_at: float | None = None
    model_load_completed_at: float | None = None
    # 캐시 상태
    grounding_dino_cache_ready: bool = False
    sam2_cache_ready: bool = False
    # 세분화 모델 상태
    grounding_dino_ready: bool = False
    grounding_dino_real_inference: bool = False
    sam2_checkpoint_ready: bool = False
    sam2_model_ready: bool = False
    sam2_predictor_ready: bool = False
    sam2_real_inference: bool = False
    sam2_load_error_type: str = ""
    sam2_load_error_message: str = ""
    sam2_config_used: str = ""

    def to_dict(self) -> dict:
        d = {
            "status":                      self.status,
            "provider":                    self.provider,
            "device":                      self.device,
            "modelsLoaded":                self.models_loaded,
            "realInferenceAvailable":      self.real_inference_available,
            "groundingDinoModelId":        self.grounding_dino_model_id,
            "sam2ModelId":                 self.sam2_model_id,
            "bboxFallbackEnabled":         self.bbox_fallback_enabled,
            "modelCachePath":              self.model_cache_path,
            "modelLoadState":              self.model_load_state,
            "modelLoadAttempt":            self.model_load_attempt,
            "concurrentLoadPrevented":     self.concurrent_load_prevented,
            "modelLoadMs":                 self.model_load_ms,
            "groundingDinoCacheReady":     self.grounding_dino_cache_ready,
            "sam2CacheReady":              self.sam2_cache_ready,
            "groundingDinoReady":          self.grounding_dino_ready,
            "groundingDinoRealInference":  self.grounding_dino_real_inference,
            "sam2CheckpointReady":         self.sam2_checkpoint_ready,
            "sam2ModelReady":              self.sam2_model_ready,
            "sam2PredictorReady":          self.sam2_predictor_ready,
            "sam2RealInference":           self.sam2_real_inference,
        }
        if self.sam2_load_error_type:
            d["sam2LoadErrorType"]    = self.sam2_load_error_type
            d["sam2LoadErrorMessage"] = self.sam2_load_error_message
        if self.sam2_config_used:
            d["sam2ConfigUsed"] = self.sam2_config_used
        if self.model_load_error:
            d["modelLoadError"] = self.model_load_error
        if self.model_load_started_at:
            d["modelLoadStartedAt"] = self.model_load_started_at
        if self.model_load_completed_at:
            d["modelLoadCompletedAt"] = self.model_load_completed_at
        return d
