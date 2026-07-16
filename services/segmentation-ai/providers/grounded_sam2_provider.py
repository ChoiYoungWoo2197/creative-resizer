"""Grounded-SAM-2 provider.

GroundingDINO (via HuggingFace transformers) + SAM 2 (sam2 package).

CPU fallback:
  - max_image_side 축소 (기본 640)
  - 후보 수 제한
  - SAM 2 대신 bbox-based mask 생성 (sam2 unavailable 시)

Model cache:
  /models/grounding-dino/     → GDINO 모델
  /models/sam2/               → SAM 2 체크포인트
"""

from __future__ import annotations
import os
import io
import base64
import logging
import time
import math

log = logging.getLogger("segmentation.provider.grounded_sam2")

# 환경 설정
MODELS_DIR = os.environ.get("MODELS_DIR", "/models")
GDINO_MODEL_ID = os.environ.get(
    "GDINO_MODEL_ID", "IDEA-Research/grounding-dino-tiny"
)
SAM2_CHECKPOINT = os.environ.get(
    "SAM2_CHECKPOINT",
    os.path.join(MODELS_DIR, "sam2", "sam2.1_hiera_tiny.pt"),
)
SAM2_CONFIG = os.environ.get(
    "SAM2_CONFIG", "configs/sam2.1/sam2.1_hiera_t.yaml"
)

CPU_MAX_SIDE = 640    # CPU 환경 기본 최대 한 변 길이

# SAM2 config 절대경로 (Windows/Linux 공통 — 설치된 패키지 기준)
def _resolve_sam2_config() -> str:
    """sam2 패키지 설치 경로 기준으로 config YAML 절대경로 반환."""
    try:
        import sam2 as _s2
        pkg = os.path.dirname(_s2.__file__)
        abs_path = os.path.join(pkg, "configs", "sam2.1", "sam2.1_hiera_t.yaml")
        if os.path.exists(abs_path):
            return abs_path
    except Exception:
        pass
    # fallback: hydra search path (패키지 내부 relative)
    return SAM2_CONFIG


SAM2_CONFIG_RESOLVED = _resolve_sam2_config()


class GroundedSam2Provider:
    """GroundingDINO + SAM 2 통합 provider.

    providers/base.py 의 ABC를 따르지 않지만 동일한 인터페이스를 제공.
    (순환 import 방지를 위해 ABC 상속 생략)
    """

    def __init__(self, device: str = "auto"):
        self._device_pref = device
        self._device: str | None = None
        self._gdino_model = None
        self._gdino_processor = None
        self._sam2_predictor = None
        self._sam2_available = False
        self._loaded = False
        self._load_error: str | None = None
        # 메타데이터
        self._gdino_model_id: str = GDINO_MODEL_ID
        self._gdino_revision: str = "unknown"
        self._sam2_checkpoint: str = SAM2_CHECKPOINT
        self._sam2_config: str = SAM2_CONFIG_RESOLVED
        self._model_load_ms: int = 0

    # ── public interface ─────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "grounded-sam2"

    @property
    def device(self) -> str:
        return self._device or self._resolve_device()

    @property
    def models_loaded(self) -> bool:
        return self._loaded

    @property
    def load_error(self) -> str | None:
        return self._load_error

    @property
    def real_inference_available(self) -> bool:
        """실제 GDINO + SAM2 추론 가능 여부 (bbox fallback 아님)."""
        return self._loaded and self._sam2_available

    def get_metadata(self) -> dict:
        """health 엔드포인트용 메타데이터."""
        return {
            "groundingDinoModelId":   self._gdino_model_id,
            "groundingDinoRevision":  self._gdino_revision,
            "sam2ModelId":            "sam2.1_hiera_tiny",
            "sam2CheckpointPath":     self._sam2_checkpoint,
            "sam2ConfigPath":         self._sam2_config,
            "externalModelMode":      "real" if self._sam2_available else "bbox_fallback",
            "externalModelRealInference": self.real_inference_available,
            "bboxFallbackEnabled":    True,
            "modelCachePath":         MODELS_DIR,
            "modelLoadMs":            self._model_load_ms,
        }

    def load_models(self) -> None:
        """GDINO + SAM 2 로드. SAM 2 불가 시 bbox mask fallback."""
        dev = self._resolve_device()
        self._device = dev
        t0 = time.time()

        try:
            self._load_gdino(dev)
        except Exception as e:
            self._load_error = f"gdino_load_failed: {e}"
            log.error("GroundingDINO 로드 실패: %s", e)
            raise RuntimeError(self._load_error) from e

        try:
            self._load_sam2(dev)
        except Exception as e:
            log.warning("SAM 2 로드 실패 (bbox fallback 사용): %s", e)
            self._sam2_available = False

        self._loaded = True
        elapsed_ms = int((time.time() - t0) * 1000)
        self._model_load_ms = elapsed_ms
        log.info(
            "모델 로드 완료: device=%s gdino=OK sam2=%s elapsed_ms=%d",
            dev, "OK" if self._sam2_available else "FALLBACK", elapsed_ms,
        )

    def segment(
        self,
        image,                  # PIL Image (RGB)
        prompts: list,
        min_confidence: float = 0.25,
        max_image_side: int = 1280,
    ) -> tuple[list, list[str]]:
        """탐지 + segmentation 실행.

        반환: (detections: list[dict], warnings: list[str])
        """
        if not self._loaded:
            return [], ["provider_not_loaded"]

        warnings: list[str] = []
        detections: list[dict] = []

        # CPU 환경에서 해상도 제한
        if self._device == "cpu":
            max_image_side = min(max_image_side, CPU_MAX_SIDE)
            image = _resize_for_inference(image, max_image_side)

        det_idx = 0
        for prompt_schema in prompts:
            if getattr(prompt_schema, "experimental", False):
                warnings.append(f"experimental_prompt_skipped:{prompt_schema.role}")
                continue

            role = getattr(prompt_schema, "role", "product")
            texts = getattr(prompt_schema, "texts", [])
            if not texts:
                continue

            # GroundingDINO: 텍스트 프롬프트를 " . " 구분자로 조합
            gdino_prompt = " . ".join(texts)

            try:
                boxes, scores, labels = self._run_gdino(
                    image, gdino_prompt, min_confidence
                )
            except Exception as e:
                warnings.append(f"gdino_failed:{role}:{e}")
                continue

            for i, (box, score, label) in enumerate(zip(boxes, scores, labels)):
                det_idx += 1
                det_id = f"det_{det_idx:03d}"
                bbox = _box_to_bbox(box, image.width, image.height)

                # SAM 2 or bbox fallback mask
                if self._sam2_available:
                    mask_pil, mask_conf, frags = self._run_sam2(image, box)
                else:
                    mask_pil, mask_conf, frags = _bbox_to_mask(image, box)

                # mask → base64 PNG
                mask_b64 = _mask_to_base64(mask_pil)

                # edge sharpness
                edge_sh = _compute_edge_sharpness(mask_pil)

                # mask area ratio
                try:
                    import numpy as np
                    arr = __import__("numpy").array(mask_pil)
                    area_ratio = float((arr > 127).sum()) / (image.width * image.height)
                except Exception:
                    area_ratio = (bbox["width"] * bbox["height"]) / (image.width * image.height)

                detections.append({
                    "detectionId":         det_id,
                    "role":                role,
                    "prompt":              label or gdino_prompt,
                    "bbox":                bbox,
                    "detectionConfidence": round(float(score), 4),
                    "maskConfidence":      round(float(mask_conf), 4),
                    "maskPngBase64":       mask_b64,
                    "maskAreaRatio":       round(area_ratio, 4),
                    "edgeSharpness":       round(edge_sh, 4),
                    "fragmentCount":       frags,
                    "_maskPil":            mask_pil,  # in-memory only
                })

        if not self._sam2_available and detections:
            warnings.append("sam2_unavailable_bbox_mask_used")

        return detections, warnings

    # ── 내부 메서드 ───────────────────────────────────────────────────────────

    def _resolve_device(self) -> str:
        if self._device_pref in ("cuda", "cpu"):
            return self._device_pref
        try:
            import torch
            return "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            return "cpu"

    def _load_gdino(self, device: str) -> None:
        from transformers import AutoProcessor, AutoModelForZeroShotObjectDetection
        import torch

        cache_dir = os.path.join(MODELS_DIR, "grounding-dino")
        os.makedirs(cache_dir, exist_ok=True)

        log.info("GroundingDINO 로드 중: %s device=%s", GDINO_MODEL_ID, device)
        self._gdino_processor = AutoProcessor.from_pretrained(
            GDINO_MODEL_ID, cache_dir=cache_dir
        )
        self._gdino_model = AutoModelForZeroShotObjectDetection.from_pretrained(
            GDINO_MODEL_ID, cache_dir=cache_dir
        )
        dev = torch.device(device)
        self._gdino_model = self._gdino_model.to(dev)
        self._gdino_model.eval()
        # revision 기록
        try:
            cfg = self._gdino_model.config
            self._gdino_revision = getattr(cfg, "_commit_hash", "unknown")
        except Exception:
            pass
        log.info("GroundingDINO 로드 완료")

    def _load_sam2(self, device: str) -> None:
        try:
            from sam2.build_sam import build_sam2
            from sam2.sam2_image_predictor import SAM2ImagePredictor
        except ImportError as e:
            raise RuntimeError(f"sam2 패키지 없음: {e}") from e

        os.makedirs(os.path.dirname(SAM2_CHECKPOINT), exist_ok=True)
        if not os.path.exists(SAM2_CHECKPOINT):
            log.info("SAM 2 체크포인트 다운로드 중...")
            _download_sam2_checkpoint(SAM2_CHECKPOINT)

        log.info("SAM 2 로드 중: checkpoint=%s config=%s device=%s",
                 SAM2_CHECKPOINT, SAM2_CONFIG_RESOLVED, device)
        sam2_model = build_sam2(SAM2_CONFIG_RESOLVED, SAM2_CHECKPOINT, device=device)
        self._sam2_predictor = SAM2ImagePredictor(sam2_model)
        self._sam2_available = True
        log.info("SAM 2 로드 완료")

    def _run_gdino(
        self, image, text: str, threshold: float
    ) -> tuple[list, list, list]:
        """GDINO 탐지 실행. 반환: (boxes_xyxy, scores, labels)"""
        import torch

        device = torch.device(self._device or "cpu")
        inputs = self._gdino_processor(
            images=image,
            text=text,
            return_tensors="pt",
        )
        inputs = {k: v.to(device) for k, v in inputs.items()}

        with torch.no_grad():
            outputs = self._gdino_model(**inputs)

        target_sizes = torch.tensor([[image.height, image.width]])
        results = self._gdino_processor.post_process_grounded_object_detection(
            outputs,
            inputs["input_ids"],
            box_threshold=threshold,
            text_threshold=threshold,
            target_sizes=target_sizes,
        )
        if not results:
            return [], [], []

        r = results[0]
        boxes = r.get("boxes", [])
        scores = r.get("scores", [])
        labels = r.get("labels", [])

        boxes_list = boxes.cpu().tolist() if hasattr(boxes, "cpu") else list(boxes)
        scores_list = scores.cpu().tolist() if hasattr(scores, "cpu") else list(scores)
        labels_list = list(labels)

        return boxes_list, scores_list, labels_list

    def _run_sam2(
        self, image, box_xyxy: list
    ) -> tuple:
        """SAM 2 실행. 반환: (mask_L_PIL, confidence, fragment_count)"""
        import numpy as np
        import torch

        self._sam2_predictor.set_image(image)
        box_np = np.array([box_xyxy], dtype=np.float32)

        masks, scores, _ = self._sam2_predictor.predict(
            box=box_np,
            multimask_output=False,
        )
        if masks is None or len(masks) == 0:
            return _bbox_to_mask(image, box_xyxy)[0], 0.5, 1

        mask_arr = masks[0]
        conf = float(scores[0]) if scores is not None and len(scores) > 0 else 0.8

        from PIL import Image
        mask_pil = Image.fromarray((mask_arr * 255).astype("uint8"), mode="L")
        frags = _count_fragments(mask_arr)
        return mask_pil, conf, frags


# ─── 유틸 함수 ────────────────────────────────────────────────────────────────

def _resize_for_inference(image, max_side: int):
    w, h = image.size
    if max(w, h) <= max_side:
        return image
    scale = max_side / max(w, h)
    nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
    from PIL import Image
    return image.resize((nw, nh), Image.LANCZOS)


def _box_to_bbox(box_xyxy: list, img_w: int, img_h: int) -> dict:
    x1, y1, x2, y2 = [float(v) for v in box_xyxy]
    x1 = max(0, min(x1, img_w))
    y1 = max(0, min(y1, img_h))
    x2 = max(0, min(x2, img_w))
    y2 = max(0, min(y2, img_h))
    return {
        "x": int(x1),
        "y": int(y1),
        "width": max(1, int(x2 - x1)),
        "height": max(1, int(y2 - y1)),
    }


def _bbox_to_mask(image, box_xyxy: list) -> tuple:
    """SAM 2 없을 때 bbox 기반 사각형 mask 생성 (feather 포함)."""
    from PIL import Image, ImageFilter
    w, h = image.size
    x1, y1, x2, y2 = [int(float(v)) for v in box_xyxy]
    x1 = max(0, x1); y1 = max(0, y1)
    x2 = min(w, x2); y2 = min(h, y2)
    mask = Image.new("L", (w, h), 0)
    if x2 > x1 and y2 > y1:
        mask.paste(Image.new("L", (x2 - x1, y2 - y1), 255), (x1, y1))
        mask = mask.filter(ImageFilter.GaussianBlur(radius=3))
    return mask, 0.55, 1  # conf=0.55 (bbox only)


def _mask_to_base64(mask_pil) -> str:
    buf = io.BytesIO()
    mask_pil.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def _compute_edge_sharpness(mask_pil) -> float:
    try:
        from PIL import ImageFilter
        import numpy as np
        edge = mask_pil.filter(ImageFilter.FIND_EDGES)
        arr = np.array(edge).astype(float)
        return min(float(arr.std()) / 64.0, 1.0)
    except Exception:
        return 0.35


def _count_fragments(mask_arr) -> int:
    try:
        from skimage.measure import label as sk_label
        labeled = sk_label(mask_arr > 0)
        return int(labeled.max())
    except Exception:
        return 1


def _download_sam2_checkpoint(checkpoint_path: str) -> None:
    import urllib.request
    url = (
        "https://dl.fbaipublicfiles.com/segment_anything_2/092824/"
        "sam2.1_hiera_tiny.pt"
    )
    os.makedirs(os.path.dirname(checkpoint_path), exist_ok=True)
    log.info("SAM 2 체크포인트 다운로드: %s", url)
    urllib.request.urlretrieve(url, checkpoint_path)
    log.info("SAM 2 체크포인트 다운로드 완료: %s", checkpoint_path)
