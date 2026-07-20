"""Grounded-SAM-2 provider.

GroundingDINO (HuggingFace transformers) + SAM 2 (Meta 공식 GitHub v1.1).

Model cache:
  /models/huggingface/hub/      → GDINO (HF hub standard layout)
  /models/sam2/                 → SAM 2 checkpoint (.pt)

Hydra 초기화:
  build_sam2() 호출 전 반드시 ensure_sam2_hydra_initialized()를 먼저 호출.
  프로세스 당 1회, thread-safe double-checked locking 패턴.
  GlobalHydra clear() 직접 호출 금지 — 서비스 코드에서 Hydra 상태를 리셋하면 안 됨.
"""

from __future__ import annotations
import os
import io
import base64
import logging
import threading
import time
import traceback as _tb

# HF xet/cache 설정 — transformers import 전에 반드시 적용
os.environ.setdefault("HF_HUB_DISABLE_XET",      "1")
os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT",  "1200")
os.environ.setdefault("HF_HUB_ETAG_TIMEOUT",      "60")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

log = logging.getLogger("segmentation.provider.grounded_sam2")

# ── 환경 설정 ─────────────────────────────────────────────────────────────────
MODELS_DIR     = os.environ.get("MODELS_DIR", "/models")
GDINO_MODEL_ID = os.environ.get("GDINO_MODEL_ID", "IDEA-Research/grounding-dino-tiny")
_HF_HUB_CACHE  = os.environ.get(
    "HF_HUB_CACHE", os.path.join(MODELS_DIR, "huggingface", "hub")
)
SAM2_CHECKPOINT = os.environ.get(
    "SAM2_CHECKPOINT",
    os.path.join(MODELS_DIR, "sam2", "sam2.1_hiera_tiny.pt"),
)
# SAM2.1 hiera-tiny 전용 config — Hydra initialize_config_module("sam2") 후 사용
SAM2_CONFIG_NAME = os.environ.get(
    "SAM2_CONFIG", "configs/sam2.1/sam2.1_hiera_t.yaml"
)
CPU_MAX_SIDE = 640


# ── Hydra 초기화 (thread-safe, 프로세스 당 1회) ──────────────────────────────

_hydra_init_lock: threading.Lock = threading.Lock()
_hydra_initialized: bool = False


def ensure_sam2_hydra_initialized() -> bool:
    """Hydra를 SAM2 config 모듈로 프로세스당 한 번만 초기화.

    Thread-safe double-checked locking 패턴.
    GlobalHydra clear() 직접 호출 금지 (Hydra 상태 리셋 불가).

    Returns:
        True:  이 호출에서 초기화됨 (처음)
        False: 이미 초기화되어 있었음 (생략)

    Raises:
        RuntimeError: import 실패 또는 initialize_config_module 예외
    """
    global _hydra_initialized

    try:
        from hydra.core.global_hydra import GlobalHydra
        from hydra import initialize_config_module
    except ImportError as e:
        raise RuntimeError(f"Hydra import 실패: {e}") from e

    # 1차 확인 (lock 없음, 빠른 경로)
    if GlobalHydra.instance().is_initialized():
        return False

    with _hydra_init_lock:
        # 2차 확인 (lock 획득 후 double-check)
        if GlobalHydra.instance().is_initialized():
            return False

        log.info(
            "Hydra 초기화 시작: config_module=sam2 version_base=1.2"
        )
        initialize_config_module(
            config_module="sam2",
            version_base="1.2",
            job_name="creative_segmentation_sam2",
        )
        _hydra_initialized = True
        log.info(
            "Hydra 초기화 완료: is_initialized=%s",
            GlobalHydra.instance().is_initialized(),
        )
        return True


def _gdino_cache_ready() -> bool:
    repo_slug = GDINO_MODEL_ID.replace("/", "--")
    model_dir = os.path.join(_HF_HUB_CACHE, f"models--{repo_slug}")
    if not os.path.isdir(model_dir):
        return False
    for _root, _dirs, files in os.walk(model_dir):
        if "model.safetensors" in files or "pytorch_model.bin" in files:
            return True
    return False


def _sam2_cache_ready() -> bool:
    if not os.path.isfile(SAM2_CHECKPOINT):
        return False
    return os.path.getsize(SAM2_CHECKPOINT) > 100_000_000


# ── Provider 클래스 ──────────────────────────────────────────────────────────

class GroundedSam2Provider:

    def __init__(self, device: str = "auto"):
        self._device_pref = device
        self._device: str | None = None
        self._gdino_model = None
        self._gdino_processor = None
        self._sam2_predictor = None
        self._loaded = False
        self._load_error: str | None = None
        self._gdino_model_id: str = GDINO_MODEL_ID
        self._gdino_revision: str = "unknown"
        self._sam2_checkpoint: str = SAM2_CHECKPOINT
        self._sam2_config_name: str = SAM2_CONFIG_NAME
        self._model_load_ms: int = 0
        self._gdino_cache_hit: bool = False

        # ── 세분화 상태 ──────────────────────────────────────────────────────
        self._gdino_ready: bool = False
        self._sam2_import_ok: bool = False
        self._sam2_checkpoint_ok: bool = False
        self._sam2_model_ok: bool = False
        self._sam2_predictor_ok: bool = False
        self._sam2_available: bool = False   # = predictor_ok (bbox fallback 없는 실제 추론)
        self._sam2_config_used: str | None = None
        self._sam2_error_type: str | None = None
        self._sam2_error_msg: str | None = None
        self._sam2_error_tb: str | None = None
        # Hydra 초기화 상태
        self._hydra_init_by_service: bool = False
        self._hydra_init_error: str | None = None

    # ── public ────────────────────────────────────────────────────────────────

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
        return self._gdino_ready and self._sam2_predictor_ok

    def get_metadata(self) -> dict:
        real_avail = self.real_inference_available
        return {
            # ── GDINO ──────────────────────────────────────────────────────
            "groundingDinoModelId":      self._gdino_model_id,
            "groundingDinoRevision":     self._gdino_revision,
            "groundingDinoReady":        self._gdino_ready,
            "groundingDinoRealInference": self._gdino_ready,
            # ── SAM2 ───────────────────────────────────────────────────────
            "sam2ModelId":               "sam2.1_hiera_tiny",
            "sam2CheckpointPath":        self._sam2_checkpoint,
            "sam2CheckpointAvailable":   os.path.isfile(self._sam2_checkpoint),
            "sam2CheckpointSizeBytes":   (
                os.path.getsize(self._sam2_checkpoint)
                if os.path.isfile(self._sam2_checkpoint) else 0
            ),
            "sam2ConfigName":            self._sam2_config_name,
            "sam2ConfigUsed":            self._sam2_config_used or "",
            "sam2ImportOk":              self._sam2_import_ok,
            "sam2CheckpointReady":       self._sam2_checkpoint_ok,
            "sam2ModelReady":            self._sam2_model_ok,
            "sam2PredictorReady":        self._sam2_predictor_ok,
            "sam2RealInference":         self._sam2_predictor_ok,
            # ── Hydra ──────────────────────────────────────────────────────
            "hydraInitializedBefore":    not self._hydra_init_by_service,
            "hydraInitializedByService": self._hydra_init_by_service,
            "hydraConfigModule":         "sam2",
            "hydraInitErrorType":        (
                self._hydra_init_error.split(":")[0]
                if self._hydra_init_error else ""
            ),
            "hydraInitErrorMessage":     self._hydra_init_error or "",
            # ── 오류 ───────────────────────────────────────────────────────
            "sam2LoadErrorType":         self._sam2_error_type or "",
            "sam2LoadErrorMessage":      self._sam2_error_msg or "",
            # ── 종합 ───────────────────────────────────────────────────────
            "externalModelMode":         "real" if self._sam2_available else "bbox_fallback",
            "externalModelRealInference": real_avail,
            "realInferenceAvailable":    real_avail,
            "bboxFallbackEnabled":       True,
            # ── 캐시 ───────────────────────────────────────────────────────
            "modelCachePath":            MODELS_DIR,
            "modelLoadMs":               self._model_load_ms,
            "groundingDinoCacheReady":   _gdino_cache_ready(),
            "sam2CacheReady":            _sam2_cache_ready(),
            "groundingDinoCachePath":    _HF_HUB_CACHE,
        }

    def load_models(self) -> None:
        """GDINO + SAM 2 로드. SAM 2 실패 시 bbox fallback."""
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
            log.warning(
                "SAM2 로드 실패 → bbox fallback 모드로 동작: "
                "error_type=%s msg=%s",
                self._sam2_error_type or type(e).__name__,
                self._sam2_error_msg or str(e),
            )
            if self._sam2_error_tb:
                log.warning("SAM2 traceback:\n%s", self._sam2_error_tb)
            self._sam2_available = False

        self._loaded = True
        elapsed = int((time.time() - t0) * 1000)
        self._model_load_ms = elapsed
        log.info(
            "모델 로드 완료: device=%s gdino=%s sam2=%s "
            "gdino_cache_hit=%s ms=%d",
            dev,
            "READY" if self._gdino_ready else "FAIL",
            "READY" if self._sam2_predictor_ok else "FALLBACK",
            self._gdino_cache_hit,
            elapsed,
        )

    def segment(
        self,
        image,
        prompts: list,
        min_confidence: float = 0.25,
        max_image_side: int = 1280,
    ) -> tuple[list, list[str]]:
        if not self._loaded:
            return [], ["provider_not_loaded"]

        warnings: list[str] = []
        detections: list[dict] = []

        if self._device == "cpu":
            max_image_side = min(max_image_side, CPU_MAX_SIDE)
            image = _resize_for_inference(image, max_image_side)

        det_idx = 0
        for prompt_schema in prompts:
            if getattr(prompt_schema, "experimental", False):
                warnings.append(f"experimental_prompt_skipped:{prompt_schema.role}")
                continue

            role  = getattr(prompt_schema, "role", "product")
            texts = getattr(prompt_schema, "texts", [])
            if not texts:
                continue

            gdino_prompt = " . ".join(texts)
            try:
                boxes, scores, labels = self._run_gdino(image, gdino_prompt, min_confidence)
            except Exception as e:
                warnings.append(f"gdino_failed:{role}:{e}")
                continue

            for box, score, label in zip(boxes, scores, labels):
                det_idx += 1
                det_id  = f"det_{det_idx:03d}"
                bbox    = _box_to_bbox(box, image.width, image.height)

                if self._sam2_available:
                    mask_pil, mask_conf, frags = self._run_sam2(image, box)
                    mask_source = "real_sam2"
                else:
                    mask_pil, mask_conf, frags = _bbox_to_mask(image, box)
                    mask_source = "external_bbox_fallback"

                mask_b64 = _mask_to_base64(mask_pil)
                edge_sh  = _compute_edge_sharpness(mask_pil)

                try:
                    import numpy as np
                    arr = np.array(mask_pil)
                    area_ratio = float((arr > 127).sum()) / (image.width * image.height)
                except Exception:
                    area_ratio = (bbox["width"] * bbox["height"]) / (
                        image.width * image.height
                    )

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
                    "maskSource":          mask_source,
                    "_maskPil":            mask_pil,
                })

        if not self._sam2_available and detections:
            warnings.append("sam2_unavailable_bbox_mask_used")

        return detections, warnings

    # ── 내부 ─────────────────────────────────────────────────────────────────

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

        cache_dir = _HF_HUB_CACHE
        os.makedirs(cache_dir, exist_ok=True)
        load_kwargs = dict(cache_dir=cache_dir)

        if _gdino_cache_ready():
            log.info("GroundingDINO 캐시 로드 (local_files_only=True): %s", GDINO_MODEL_ID)
            try:
                self._gdino_processor = AutoProcessor.from_pretrained(
                    GDINO_MODEL_ID, local_files_only=True, **load_kwargs
                )
                self._gdino_model = AutoModelForZeroShotObjectDetection.from_pretrained(
                    GDINO_MODEL_ID, local_files_only=True, **load_kwargs
                )
                self._gdino_cache_hit = True
            except (EnvironmentError, OSError) as e:
                log.warning("캐시 불완전 — 네트워크 다운로드: %s", e)
                self._gdino_processor = AutoProcessor.from_pretrained(
                    GDINO_MODEL_ID, **load_kwargs
                )
                self._gdino_model = AutoModelForZeroShotObjectDetection.from_pretrained(
                    GDINO_MODEL_ID, **load_kwargs
                )
        else:
            log.info("GroundingDINO 네트워크 다운로드: %s", GDINO_MODEL_ID)
            self._gdino_processor = AutoProcessor.from_pretrained(
                GDINO_MODEL_ID, **load_kwargs
            )
            self._gdino_model = AutoModelForZeroShotObjectDetection.from_pretrained(
                GDINO_MODEL_ID, **load_kwargs
            )

        dev = torch.device(device)
        self._gdino_model = self._gdino_model.to(dev)
        self._gdino_model.eval()
        try:
            self._gdino_revision = getattr(
                self._gdino_model.config, "_commit_hash", "unknown"
            )
        except Exception:
            pass
        self._gdino_ready = True
        log.info("GroundingDINO 로드 완료: device=%s cache_hit=%s", device, self._gdino_cache_hit)

    def _load_sam2(self, device: str) -> None:
        """SAM2 초기화 — Hydra 명시적 초기화 후 build_sam2 단일 config 사용."""

        # Step 1: import + 패키지 진단
        log.info("SAM2 로드 시작: checkpoint=%s config=%s device=%s",
                 self._sam2_checkpoint, self._sam2_config_name, device)
        try:
            from sam2.build_sam import build_sam2
            from sam2.sam2_image_predictor import SAM2ImagePredictor
            import sam2 as _s2, glob, inspect
            pkg_dir = os.path.dirname(_s2.__file__)
            self._sam2_import_ok = True
            log.info("sam2 import 성공: package_dir=%s", pkg_dir)
            # build_sam2 소스에서 Hydra 자동 초기화 코드 확인 (진단용)
            try:
                src = inspect.getsource(build_sam2)
                has_auto = "initialize_config_module" in src
                log.info("build_sam2 has_auto_hydra_init=%s", has_auto)
            except Exception:
                pass
            for cf in sorted(glob.glob(
                os.path.join(pkg_dir, "**", "*.yaml"), recursive=True
            ))[:10]:
                log.info("sam2 config file: %s", os.path.relpath(cf, pkg_dir))
        except ImportError as e:
            self._sam2_import_ok = False
            self._sam2_error_type = "ImportError"
            self._sam2_error_msg  = str(e)
            self._sam2_error_tb   = _tb.format_exc()
            log.error("sam2 import 실패:\n%s", self._sam2_error_tb)
            raise RuntimeError(f"sam2_import_failed: {e}") from e

        # Step 2: checkpoint 검증
        ckpt = self._sam2_checkpoint
        ckpt_exists = os.path.isfile(ckpt)
        ckpt_size   = os.path.getsize(ckpt) if ckpt_exists else 0
        self._sam2_checkpoint_ok = ckpt_exists and ckpt_size > 100_000_000
        log.info(
            "SAM2 checkpoint: path=%s exists=%s size=%d ok=%s",
            ckpt, ckpt_exists, ckpt_size, self._sam2_checkpoint_ok,
        )
        if not ckpt_exists:
            log.info("SAM2 checkpoint 없음 — 다운로드 시작")
            _download_sam2_checkpoint(ckpt)
            ckpt_exists = os.path.isfile(ckpt)
            ckpt_size   = os.path.getsize(ckpt) if ckpt_exists else 0
            self._sam2_checkpoint_ok = ckpt_exists and ckpt_size > 100_000_000

        # Step 3: Hydra 명시적 초기화 (GlobalHydra.clear() 호출 금지)
        log.info("SAM2 Hydra 초기화: config_module=sam2 version_base=1.2")
        try:
            self._hydra_init_by_service = ensure_sam2_hydra_initialized()
            log.info(
                "Hydra 초기화 상태: initialized_by_service=%s",
                self._hydra_init_by_service,
            )
        except RuntimeError as e:
            self._hydra_init_error = str(e)
            self._sam2_error_type  = "HydraInitError"
            self._sam2_error_msg   = str(e)
            self._sam2_error_tb    = _tb.format_exc()
            log.error("Hydra 초기화 실패:\n%s", self._sam2_error_tb)
            raise

        # Step 4: build_sam2 — 단일 config
        cfg = self._sam2_config_name
        log.info("SAM2 build_sam2: config=%s checkpoint=%s device=%s", cfg, ckpt, device)
        try:
            sam2_model = build_sam2(cfg, ckpt, device=device)
            self._sam2_model_ok    = True
            self._sam2_config_used = cfg
            log.info("SAM2 build_sam2 성공: config=%s", cfg)
        except Exception as exc:
            self._sam2_error_type = type(exc).__name__
            self._sam2_error_msg  = str(exc)
            self._sam2_error_tb   = _tb.format_exc()
            log.error(
                "SAM2 build_sam2 실패 (config=%s):\n%s",
                cfg, self._sam2_error_tb,
            )
            raise RuntimeError(f"SAM2 build_sam2 실패: {exc}") from exc

        # Step 5: predictor 생성
        try:
            self._sam2_predictor    = SAM2ImagePredictor(sam2_model)
            self._sam2_predictor_ok = True
            self._sam2_available    = True
            log.info("SAM2 predictor 생성 완료: config_used=%s", self._sam2_config_used)
        except Exception as exc:
            self._sam2_error_type = type(exc).__name__
            self._sam2_error_msg  = str(exc)
            self._sam2_error_tb   = _tb.format_exc()
            log.error("SAM2 predictor 생성 실패:\n%s", self._sam2_error_tb)
            raise RuntimeError(f"sam2_predictor_failed: {exc}") from exc

    def _run_gdino(self, image, text: str, threshold: float) -> tuple:
        import torch
        device  = torch.device(self._device or "cpu")
        inputs  = self._gdino_processor(images=image, text=text, return_tensors="pt")
        inputs  = {k: v.to(device) for k, v in inputs.items()}

        with torch.no_grad():
            outputs = self._gdino_model(**inputs)

        target_sizes = torch.tensor([[image.height, image.width]])
        results = self._gdino_processor.post_process_grounded_object_detection(
            outputs,
            inputs["input_ids"],
            box_threshold  = threshold,
            text_threshold = threshold,
            target_sizes   = target_sizes,
        )
        if not results:
            return [], [], []

        r = results[0]
        boxes  = r.get("boxes",  [])
        scores = r.get("scores", [])
        labels = r.get("labels", [])
        return (
            boxes.cpu().tolist()  if hasattr(boxes,  "cpu") else list(boxes),
            scores.cpu().tolist() if hasattr(scores, "cpu") else list(scores),
            list(labels),
        )

    def _run_sam2(self, image, box_xyxy: list) -> tuple:
        import numpy as np
        import torch
        # set_image: numpy RGB 배열로 전달
        if hasattr(image, "mode"):
            img_np = np.array(image.convert("RGB"))
        else:
            img_np = np.asarray(image)
        self._sam2_predictor.set_image(img_np)
        # box: shape (4,) — 단일 박스
        box_np = np.array(box_xyxy, dtype=np.float32)
        with torch.inference_mode():
            masks, scores, _ = self._sam2_predictor.predict(
                box=box_np, multimask_output=False
            )
        if masks is None or len(masks) == 0:
            return _bbox_to_mask(image, box_xyxy)[0], 0.5, 1

        mask_arr = masks[0]
        conf = float(scores[0]) if scores is not None and len(scores) > 0 else 0.8
        from PIL import Image
        mask_pil = Image.fromarray((mask_arr * 255).astype("uint8"), mode="L")
        frags    = _count_fragments(mask_arr)
        return mask_pil, conf, frags


# ─── 유틸 ─────────────────────────────────────────────────────────────────────

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
    x1 = max(0, min(x1, img_w)); y1 = max(0, min(y1, img_h))
    x2 = max(0, min(x2, img_w)); y2 = max(0, min(y2, img_h))
    return {
        "x": int(x1), "y": int(y1),
        "width": max(1, int(x2 - x1)), "height": max(1, int(y2 - y1)),
    }


def _bbox_to_mask(image, box_xyxy: list) -> tuple:
    from PIL import Image, ImageFilter
    w, h = image.size
    x1, y1, x2, y2 = [int(float(v)) for v in box_xyxy]
    x1 = max(0, x1); y1 = max(0, y1)
    x2 = min(w, x2); y2 = min(h, y2)
    mask = Image.new("L", (w, h), 0)
    if x2 > x1 and y2 > y1:
        mask.paste(Image.new("L", (x2 - x1, y2 - y1), 255), (x1, y1))
        mask = mask.filter(ImageFilter.GaussianBlur(radius=3))
    return mask, 0.55, 1


def _mask_to_base64(mask_pil) -> str:
    buf = io.BytesIO()
    mask_pil.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def _compute_edge_sharpness(mask_pil) -> float:
    try:
        from PIL import ImageFilter
        import numpy as np
        edge = mask_pil.filter(ImageFilter.FIND_EDGES)
        arr  = np.array(edge).astype(float)
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


def _download_sam2_checkpoint(checkpoint_path: str, max_retries: int = 3) -> None:
    import urllib.request
    url = (
        "https://dl.fbaipublicfiles.com/segment_anything_2/092824/"
        "sam2.1_hiera_tiny.pt"
    )
    os.makedirs(os.path.dirname(checkpoint_path), exist_ok=True)
    tmp_path = checkpoint_path + ".tmp"
    for attempt in range(1, max_retries + 1):
        try:
            log.info("SAM2 checkpoint 다운로드 (attempt=%d): %s", attempt, url)
            urllib.request.urlretrieve(url, tmp_path)
            os.replace(tmp_path, checkpoint_path)
            log.info("SAM2 checkpoint 다운로드 완료: %s", checkpoint_path)
            return
        except Exception as e:
            log.warning("SAM2 다운로드 실패 (attempt=%d): %s", attempt, e)
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            if attempt < max_retries:
                time.sleep(5 * attempt)
    raise RuntimeError(f"SAM2 checkpoint 다운로드 {max_retries}회 실패: {url}")
