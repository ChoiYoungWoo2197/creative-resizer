"""Grounded-SAM-2 provider.

GroundingDINO (HuggingFace transformers) + SAM 2 (Meta 공식 GitHub v1.1).

Model cache:
  /models/huggingface/hub/      → GDINO (HF hub standard layout)
  /models/sam2/                 → SAM 2 checkpoint (.pt)

Hydra 초기화:
  build_sam2() 호출 전 반드시 ensure_sam2_hydra_initialized()를 먼저 호출.
  프로세스 당 1회, thread-safe double-checked locking 패턴.
  GlobalHydra clear() 직접 호출 금지 — 서비스 코드에서 Hydra 상태를 리셋하면 안 됨.

Stage 18.1:
  segment()를 다단계 후보 파이프라인으로 재작성.
  1. 전 역할 GDINO 탐지 + SAM2 마스크 생성 (numpy)
  2. 손/사람 union mask 계산
  3. product 후보: 원본 + subtract(손/사람 제거) + union(근접 쌍)
  4. 후처리: morphological close + 작은 fragment 제거 + hole filling
  5. 손·사람 픽셀 overlap 비율 계산
  6. numpy → PIL 변환, 최종 필드 계산
  _run_sam2() 유지 (TC-28 회귀 보호), _run_sam2_np() 신규 추가.
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

# 후보 생성 상한
_MAX_UNION_PAIRS = 5   # union 후보 최대 쌍 수


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
        """다단계 후보 파이프라인으로 segmentation 수행.

        1. 전 역할 GDINO 탐지 + SAM2 마스크 생성 (numpy)
        2. 손/사람 union mask 계산
        3. product 후보 생성: 원본 + subtract + union(근접 쌍)
        4. 픽셀 overlap 비율 계산
        5. 후처리: morphological close, hole fill, fragment 제거
        6. numpy → PIL 변환, 최종 필드 계산
        """
        if not self._loaded:
            return [], ["provider_not_loaded"]

        warnings: list[str] = []

        if self._device == "cpu":
            max_image_side = min(max_image_side, CPU_MAX_SIDE)
            image = _resize_for_inference(image, max_image_side)

        canvas_w, canvas_h = image.width, image.height
        canvas_area = canvas_w * canvas_h

        # ── Phase 1: GDINO 탐지 + SAM2 마스크 생성 (전 역할) ─────────────────
        raw_by_role: dict[str, list] = {}
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
                det_id = f"det_{det_idx:03d}"
                bbox   = _box_to_bbox(box, canvas_w, canvas_h)

                if self._sam2_available:
                    mask_np, mask_conf, frags = self._run_sam2_np(image, box)
                    mask_source = "real_sam2"
                else:
                    mask_np, mask_conf, frags = _bbox_to_mask_np(image, box)
                    mask_source = "external_bbox_fallback"

                raw_by_role.setdefault(role, []).append({
                    "detectionId":         det_id,
                    "role":                role,
                    "prompt":              label or gdino_prompt,
                    "bbox":                bbox,
                    "detectionConfidence": round(float(score), 4),
                    "maskConfidence":      round(float(mask_conf), 4),
                    "maskNp":              mask_np,
                    "fragmentCount":       frags,
                    "maskSource":          mask_source,
                    "handOverlapRatio":    0.0,
                    "personOverlapRatio":  0.0,
                    "handSubtractApplied": False,
                })

        # ── Phase 2: 손·사람 union mask 계산 ──────────────────────────────────
        hand_union   = _union_mask_np(
            [d["maskNp"] for d in raw_by_role.get("hand", [])],
            canvas_w, canvas_h
        )
        person_union = _union_mask_np(
            [d["maskNp"] for d in raw_by_role.get("person", [])],
            canvas_w, canvas_h
        )

        # ── Phase 3: product 후보 생성 ─────────────────────────────────────────
        product_raw        = raw_by_role.get("product", [])
        product_candidates = []

        for d in product_raw:
            mask_np = d["maskNp"]

            # 후처리 (원본)
            proc_np    = _postprocess_mask(mask_np, canvas_area)
            proc_frags = _count_fragments(proc_np > 127)
            hand_ovl   = _pixel_overlap_ratio(proc_np, hand_union)
            person_ovl = _pixel_overlap_ratio(proc_np, person_union)

            cand = dict(d)
            cand["maskNp"]            = proc_np
            cand["fragmentCount"]     = proc_frags
            cand["handOverlapRatio"]  = round(hand_ovl, 4)
            cand["personOverlapRatio"] = round(person_ovl, 4)
            product_candidates.append(cand)

            # subtract 후보 (겹침 5% 이상)
            if hand_ovl > 0.05 or person_ovl > 0.05:
                sub_np    = _subtract_masks(proc_np, hand_union, person_union)
                sub_np    = _postprocess_mask(sub_np, canvas_area)
                sub_area  = float((sub_np > 127).sum())
                orig_area = float((proc_np > 127).sum())

                # subtract 후 최소 30% 면적 유지
                if sub_area > orig_area * 0.30:
                    sub_frags      = _count_fragments(sub_np > 127)
                    sub_hand_ovl   = _pixel_overlap_ratio(sub_np, hand_union)
                    sub_person_ovl = _pixel_overlap_ratio(sub_np, person_union)

                    sub_cand = dict(d)
                    sub_cand["detectionId"]        = d["detectionId"] + "_sub"
                    sub_cand["maskNp"]             = sub_np
                    sub_cand["fragmentCount"]      = sub_frags
                    sub_cand["handOverlapRatio"]   = round(sub_hand_ovl, 4)
                    sub_cand["personOverlapRatio"] = round(sub_person_ovl, 4)
                    sub_cand["handSubtractApplied"] = True
                    product_candidates.append(sub_cand)
                else:
                    log.debug(
                        "subtract 후보 스킵 (면적 %d%% < 30%%): %s",
                        int(sub_area / max(orig_area, 1) * 100),
                        d["detectionId"],
                    )

        # union 후보 (근접 product 쌍)
        union_cands = _generate_union_candidates(
            product_raw, hand_union, person_union, canvas_w, canvas_h, canvas_area
        )
        product_candidates.extend(union_cands)

        # ── Phase 4: 비product 역할 정리 ──────────────────────────────────────
        other_dets: list = []
        for role, role_dets in raw_by_role.items():
            if role == "product":
                continue
            other_dets.extend(role_dets)

        # ── Phase 5: numpy → PIL 변환, 최종 필드 계산 ─────────────────────────
        all_dets: list = []
        for d in product_candidates + other_dets:
            mask_np = d.pop("maskNp", None)
            if mask_np is None:
                continue

            mask_pil = _np_to_pil(mask_np)
            mask_b64 = _mask_to_base64(mask_pil)
            area_ratio = round(float((mask_np > 127).sum()) / max(canvas_area, 1), 4)
            edge_sh    = round(_compute_edge_sharpness(mask_pil), 4)

            all_dets.append({
                "detectionId":          d["detectionId"],
                "role":                 d["role"],
                "prompt":               d["prompt"],
                "bbox":                 d["bbox"],
                "detectionConfidence":  d["detectionConfidence"],
                "maskConfidence":       d["maskConfidence"],
                "maskPngBase64":        mask_b64,
                "maskAreaRatio":        area_ratio,
                "edgeSharpness":        edge_sh,
                "fragmentCount":        d["fragmentCount"],
                "maskSource":           d["maskSource"],
                "handOverlapRatio":     d.get("handOverlapRatio", 0.0),
                "personOverlapRatio":   d.get("personOverlapRatio", 0.0),
                "handSubtractApplied":  d.get("handSubtractApplied", False),
                "_maskPil":             mask_pil,
            })

        if not self._sam2_available and all_dets:
            warnings.append("sam2_unavailable_bbox_mask_used")

        return all_dets, warnings

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
        """SAM2 추론 → (PIL L-mode mask, confidence, frag_count). TC-28 회귀 유지."""
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

    def _run_sam2_np(self, image, box_xyxy: list) -> tuple:
        """SAM2 추론 → (numpy uint8 0/255, confidence, frag_count)."""
        import numpy as np
        import torch
        if hasattr(image, "mode"):
            img_np = np.array(image.convert("RGB"))
        else:
            img_np = np.asarray(image)
        self._sam2_predictor.set_image(img_np)
        box_np = np.array(box_xyxy, dtype=np.float32)
        with torch.inference_mode():
            masks, scores, _ = self._sam2_predictor.predict(
                box=box_np, multimask_output=False
            )
        if masks is None or len(masks) == 0:
            fb_np, fb_conf, fb_frags = _bbox_to_mask_np(image, box_xyxy)
            return fb_np, 0.5, fb_frags

        mask_arr = masks[0]
        conf  = float(scores[0]) if scores is not None and len(scores) > 0 else 0.8
        frags = _count_fragments(mask_arr)
        return (mask_arr * 255).astype("uint8"), conf, frags


# ─── 다단계 후보 유틸 ─────────────────────────────────────────────────────────

def _union_mask_np(mask_list: list, w: int, h: int):
    """여러 numpy mask를 pixel-wise max로 union. 빈 리스트면 None."""
    import numpy as np
    if not mask_list:
        return None
    union = np.zeros((h, w), dtype=np.uint8)
    for m in mask_list:
        if m is None:
            continue
        if m.shape == (h, w):
            union = np.maximum(union, m)
        else:
            from PIL import Image
            pil = Image.fromarray(m.astype("uint8")).resize((w, h), Image.NEAREST)
            union = np.maximum(union, np.array(pil))
    return union


def _pixel_overlap_ratio(mask_np, other_np) -> float:
    """product mask 면적 대비 other_np와 겹치는 픽셀 비율."""
    import numpy as np
    if other_np is None or mask_np is None:
        return 0.0
    binary1 = mask_np > 127
    binary2 = other_np > 127
    denom = float(binary1.sum())
    if denom == 0.0:
        return 0.0
    return float((binary1 & binary2).sum()) / denom


def _subtract_masks(mask_np, hand_np, person_np):
    """mask_np에서 hand·person 영역을 제거한 numpy mask 반환."""
    import numpy as np
    result = mask_np.copy()
    if hand_np is not None:
        result = np.where(hand_np > 127, 0, result).astype(np.uint8)
    if person_np is not None:
        result = np.where(person_np > 127, 0, result).astype(np.uint8)
    return result


def _postprocess_mask(mask_np, canvas_area: int):
    """형태학적 후처리.

    1. binary_closing: 작은 구멍 닫기 (disk 5)
    2. remove_small_holes: 제품 내부 빈 공간 채우기
    3. remove_small_objects: 작은 파편 제거 (제품 끝부분 보호 위해 최소값 제한)
    """
    try:
        import numpy as np
        from skimage.morphology import binary_closing, disk
        from skimage.morphology import remove_small_objects, remove_small_holes

        binary = mask_np > 127

        # 1. close (disk 5): 경계 불연속 닫기
        binary = binary_closing(binary, disk(5))

        # 2. hole fill (최소 0.1% canvas)
        min_hole = max(int(canvas_area * 0.001), 64)
        binary = remove_small_holes(binary, area_threshold=min_hole)

        # 3. fragment 제거 — 제품 상단/하단 보호: 0.2% 미만만 제거
        min_obj = max(int(canvas_area * 0.002), 128)
        binary = remove_small_objects(binary, min_size=min_obj)

        return (binary.astype(np.uint8)) * 255
    except Exception as e:
        log.debug("postprocess_mask 실패 (원본 반환): %s", e)
        return mask_np


def _generate_union_candidates(
    product_raws: list,
    hand_union,
    person_union,
    canvas_w: int,
    canvas_h: int,
    canvas_area: int,
) -> list:
    """근접 product 탐지 쌍에 대한 union 후보 생성.

    근접 판단: 세로 거리 < max_height * 2.0 AND 가로 거리 < max_width * 1.5
    상위 _MAX_UNION_PAIRS 쌍(합산 confidence 기준) 만 생성.
    """
    import numpy as np

    if len(product_raws) < 2:
        return []

    n = len(product_raws)
    pair_scores = []
    for i in range(n):
        for j in range(i + 1, n):
            di, dj = product_raws[i], product_raws[j]
            bi, bj = di["bbox"], dj["bbox"]
            ci_y = bi["y"] + bi["height"] / 2
            cj_y = bj["y"] + bj["height"] / 2
            ci_x = bi["x"] + bi["width"] / 2
            cj_x = bj["x"] + bj["width"] / 2
            max_h = max(bi["height"], bj["height"], 1)
            max_w = max(bi["width"],  bj["width"],  1)
            if (
                abs(ci_y - cj_y) < max_h * 2.0
                and abs(ci_x - cj_x) < max_w * 1.5
            ):
                pair_scores.append(
                    (di["detectionConfidence"] + dj["detectionConfidence"], i, j)
                )

    pair_scores.sort(reverse=True)
    candidates = []

    for _, i, j in pair_scores[:_MAX_UNION_PAIRS]:
        di, dj = product_raws[i], product_raws[j]
        bi, bj = di["bbox"], dj["bbox"]

        union_np = np.maximum(di["maskNp"], dj["maskNp"])
        union_np = _postprocess_mask(union_np, canvas_area)

        sub_area = float((union_np > 127).sum())
        if sub_area < canvas_area * 0.001:
            continue

        frags      = _count_fragments(union_np > 127)
        hand_ovl   = _pixel_overlap_ratio(union_np, hand_union)
        person_ovl = _pixel_overlap_ratio(union_np, person_union)

        ux1 = min(bi["x"], bj["x"])
        uy1 = min(bi["y"], bj["y"])
        ux2 = max(bi["x"] + bi["width"],  bj["x"] + bj["width"])
        uy2 = max(bi["y"] + bi["height"], bj["y"] + bj["height"])

        best_conf      = max(di["detectionConfidence"], dj["detectionConfidence"])
        best_mask_conf = max(di["maskConfidence"],      dj["maskConfidence"])

        candidates.append({
            "detectionId":         f"{di['detectionId']}_{dj['detectionId']}_union",
            "role":                "product",
            "prompt":              di["prompt"],
            "bbox":                {
                "x": ux1, "y": uy1,
                "width":  max(1, ux2 - ux1),
                "height": max(1, uy2 - uy1),
            },
            "detectionConfidence":  round(best_conf, 4),
            "maskConfidence":       round(best_mask_conf, 4),
            "maskNp":               union_np,
            "fragmentCount":        frags,
            "maskSource":           di["maskSource"],
            "handOverlapRatio":     round(hand_ovl, 4),
            "personOverlapRatio":   round(person_ovl, 4),
            "handSubtractApplied":  False,
        })

    return candidates


def _np_to_pil(mask_np):
    from PIL import Image
    return Image.fromarray(mask_np.astype("uint8"), mode="L")


# ─── 기존 유틸 (하위 호환) ────────────────────────────────────────────────────

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
    """PIL mask 반환 (하위 호환, _run_sam2 fallback용)."""
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


def _bbox_to_mask_np(image, box_xyxy: list) -> tuple:
    """numpy mask 반환 (bbox fallback)."""
    import numpy as np
    w, h = image.width, image.height
    x1, y1, x2, y2 = [int(float(v)) for v in box_xyxy]
    x1 = max(0, x1); y1 = max(0, y1)
    x2 = min(w, x2); y2 = min(h, y2)
    mask = np.zeros((h, w), dtype=np.uint8)
    if x2 > x1 and y2 > y1:
        mask[y1:y2, x1:x2] = 255
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
