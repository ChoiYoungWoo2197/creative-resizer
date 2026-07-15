"""Request / Response schemas for creative-segmentation-ai service.

JSON 직렬화 / 역직렬화에만 사용. Pydantic 의존 없이 dataclass로 구현.
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

    def to_dict(self) -> dict:
        return {
            "detectionId":        self.detection_id,
            "role":               self.role,
            "prompt":             self.prompt,
            "bbox":               self.bbox.to_dict(),
            "detectionConfidence": round(self.detection_confidence, 4),
            "maskConfidence":     round(self.mask_confidence, 4),
            "maskPngBase64":      self.mask_png_base64,
            "maskAreaRatio":      round(self.mask_area_ratio, 4),
            "edgeSharpness":      round(self.edge_sharpness, 4),
            "fragmentCount":      self.fragment_count,
        }


@dataclass
class SegmentationResponse:
    request_id: str = ""
    provider: str = ""
    device: str = ""
    processing_ms: int = 0
    detections: list[DetectionResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "requestId":    self.request_id,
            "provider":     self.provider,
            "device":       self.device,
            "processingMs": self.processing_ms,
            "detections":   [d.to_dict() for d in self.detections],
            "warnings":     self.warnings,
        }


@dataclass
class HealthResponse:
    status: str = "ok"
    provider: str = ""
    device: str = ""
    models_loaded: bool = False
    model_load_error: str = ""

    def to_dict(self) -> dict:
        d = {
            "status":       self.status,
            "provider":     self.provider,
            "device":       self.device,
            "modelsLoaded": self.models_loaded,
        }
        if self.model_load_error:
            d["modelLoadError"] = self.model_load_error
        return d
