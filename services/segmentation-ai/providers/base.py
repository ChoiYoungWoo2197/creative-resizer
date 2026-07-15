"""Abstract base class for segmentation providers."""

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from schemas import PromptSchema, DetectionResult


class BaseSegmentationProvider(ABC):
    """모든 segmentation provider의 공통 인터페이스."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def device(self) -> str: ...

    @property
    @abstractmethod
    def models_loaded(self) -> bool: ...

    @abstractmethod
    def load_models(self) -> None:
        """모델 초기화. 실패 시 RuntimeError."""
        ...

    @abstractmethod
    def segment(
        self,
        image,                  # PIL Image RGB
        prompts: list,          # list[PromptSchema]
        min_confidence: float,
        max_image_side: int,
    ) -> tuple[list, list[str]]:
        """segmentation 실행.

        반환: (detections: list[DetectionResult], warnings: list[str])
        실패 시 ([], [error_message]).
        """
        ...

    def health(self) -> dict:
        return {
            "status": "ok" if self.models_loaded else "models_not_loaded",
            "provider": self.name,
            "device": self.device,
            "modelsLoaded": self.models_loaded,
        }
