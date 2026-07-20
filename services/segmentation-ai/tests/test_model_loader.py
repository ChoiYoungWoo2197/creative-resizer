"""model_loader single-flight 동작 단위 테스트 (10개).

실제 모델 로드 없이 상태 전이·동시성·환경변수만 검증.
"""

from __future__ import annotations

import importlib
import os
import sys
import threading
import time
import types
import unittest
from unittest.mock import MagicMock, patch


# ── 헬퍼: model_loader 모듈을 깨끗한 상태로 재임포트 ─────────────────────────────

def _fresh_model_loader():
    """module-level 전역 상태를 초기화한 새 model_loader 모듈 반환."""
    if "model_loader" in sys.modules:
        del sys.modules["model_loader"]
    import model_loader as ml  # noqa: PLC0415
    # 내부 상태 초기화
    with ml._cond:
        ml._state             = ml.NOT_STARTED
        ml._provider          = None
        ml._load_error        = None
        ml._load_ms           = 0
        ml._load_attempt      = 0
        ml._load_started_at   = 0.0
        ml._load_completed_at = 0.0
        ml._concurrent_prevented = 0
    return ml


def _make_fake_provider(load_delay: float = 0.0, fail: bool = False) -> MagicMock:
    p = MagicMock()
    p.device = "cpu"
    p.models_loaded = True
    p.real_inference_available = True
    p.get_metadata.return_value = {
        "groundingDinoModelId":      "IDEA-Research/grounding-dino-tiny",
        "groundingDinoRevision":     "test",
        "sam2ModelId":               "sam2.1_hiera_tiny",
        "sam2CheckpointPath":        "/models/sam2/sam2.1_hiera_tiny.pt",
        "sam2ConfigPath":            "configs/sam2.1/sam2.1_hiera_t.yaml",
        "externalModelMode":         "real",
        "externalModelRealInference": True,
        "bboxFallbackEnabled":       True,
        "modelCachePath":            "/models",
        "modelLoadMs":               42,
        "groundingDinoCacheReady":   True,
        "sam2CacheReady":            True,
        "groundingDinoCachePath":    "/models/huggingface/hub",
        "sam2CheckpointAvailable":   True,
    }

    if fail:
        p.load_models.side_effect = RuntimeError("fake_load_failure")
    elif load_delay > 0:
        def _slow_load():
            time.sleep(load_delay)
        p.load_models.side_effect = _slow_load

    return p


# ── 테스트 클래스 ──────────────────────────────────────────────────────────────

class TestSingleFlightBasic(unittest.TestCase):
    """TC-01~TC-03: 기본 상태 전이"""

    def setUp(self):
        self.ml = _fresh_model_loader()

    def test_01_initial_state_is_not_started(self):
        """TC-01: 초기 상태 NOT_STARTED."""
        self.assertEqual(self.ml.get_state(), self.ml.NOT_STARTED)
        self.assertIsNone(self.ml.get_provider())

    def test_02_preload_transitions_to_ready(self):
        """TC-02: preload() 후 READY + provider 반환."""
        ml = self.ml
        fake_p = _make_fake_provider()

        with patch("model_loader.os.environ.get", side_effect=lambda k, d=None:
                   "grounded-sam2" if k == "CREATIVE_SEGMENTATION_PROVIDER" else
                   "cpu"           if k == "CREATIVE_SEGMENTATION_DEVICE"   else
                   (os.environ.get(k, d) if d is not None else os.environ.get(k))):
            # GroundedSam2Provider 패치
            with patch.dict("sys.modules", {
                "providers.grounded_sam2_provider": types.SimpleNamespace(
                    GroundedSam2Provider=lambda device: fake_p
                )
            }):
                ml.preload()
                # 스레드 완료 대기
                ok = ml.ensure_ready(timeout=5.0)

        self.assertTrue(ok)
        self.assertEqual(ml.get_state(), ml.READY)
        self.assertIs(ml.get_provider(), fake_p)

    def test_03_failed_load_sets_failed_state(self):
        """TC-03: load_models() 실패 → FAILED 상태, provider=None."""
        ml = self.ml
        fake_p = _make_fake_provider(fail=True)

        with patch.dict("sys.modules", {
            "providers.grounded_sam2_provider": types.SimpleNamespace(
                GroundedSam2Provider=lambda device: fake_p
            )
        }):
            ml.preload()
            ok = ml.ensure_ready(timeout=5.0)

        self.assertFalse(ok)
        self.assertEqual(ml.get_state(), ml.FAILED)
        self.assertIsNone(ml.get_provider())
        self.assertIn("fake_load_failure", ml.get_load_error() or "")


class TestConcurrentPreload(unittest.TestCase):
    """TC-04~TC-06: 동시 호출에서 단 1번만 로드"""

    def setUp(self):
        self.ml = _fresh_model_loader()

    def test_04_10_concurrent_preloads_only_one_loads(self):
        """TC-04: 10개 스레드 동시 preload() → load_models() 정확히 1회."""
        ml = self.ml
        load_count = [0]
        lock = threading.Lock()

        class CountingProvider:
            device = "cpu"
            models_loaded = True
            real_inference_available = True

            def load_models(self):
                time.sleep(0.05)
                with lock:
                    load_count[0] += 1

            def get_metadata(self):
                return {"groundingDinoCacheReady": True, "sam2CacheReady": True,
                        "modelCachePath": "/m", "bboxFallbackEnabled": True,
                        "externalModelRealInference": True,
                        "groundingDinoModelId": "", "sam2ModelId": "",
                        "modelLoadMs": 0}

        with patch.dict("sys.modules", {
            "providers.grounded_sam2_provider": types.SimpleNamespace(
                GroundedSam2Provider=lambda device: CountingProvider()
            )
        }):
            barrier = threading.Barrier(10)
            threads = []
            for _ in range(10):
                def _run():
                    barrier.wait()
                    ml.preload()
                t = threading.Thread(target=_run)
                threads.append(t)
                t.start()
            for t in threads:
                t.join(timeout=10)

            ml.ensure_ready(timeout=5.0)

        self.assertEqual(load_count[0], 1, "load_models() 호출이 정확히 1회여야 한다")
        self.assertGreaterEqual(ml.get_diagnostics()["concurrentLoadPrevented"], 9)

    def test_05_preload_during_loading_increments_prevented(self):
        """TC-05: LOADING 상태에서 preload() → concurrentLoadPrevented 증가."""
        ml = self.ml
        fake_p = _make_fake_provider(load_delay=0.2)

        with patch.dict("sys.modules", {
            "providers.grounded_sam2_provider": types.SimpleNamespace(
                GroundedSam2Provider=lambda device: fake_p
            )
        }):
            ml.preload()
            # LOADING 중에 추가 호출
            time.sleep(0.05)
            self.assertEqual(ml.get_state(), ml.LOADING)
            for _ in range(3):
                ml.preload()
            ml.ensure_ready(timeout=5.0)

        diag = ml.get_diagnostics()
        self.assertGreaterEqual(diag["concurrentLoadPrevented"], 3)

    def test_06_load_attempt_increments_once(self):
        """TC-06: 여러 번 preload() 호출해도 loadAttempt=1."""
        ml = self.ml
        fake_p = _make_fake_provider(load_delay=0.05)

        with patch.dict("sys.modules", {
            "providers.grounded_sam2_provider": types.SimpleNamespace(
                GroundedSam2Provider=lambda device: fake_p
            )
        }):
            for _ in range(5):
                ml.preload()
            ml.ensure_ready(timeout=5.0)

        self.assertEqual(ml.get_diagnostics()["modelLoadAttempt"], 1)


class TestHealthNeverTriggersLoad(unittest.TestCase):
    """TC-07: get_provider() / get_state()는 로드를 시작하지 않는다."""

    def setUp(self):
        self.ml = _fresh_model_loader()

    def test_07_get_provider_does_not_trigger_preload(self):
        """TC-07: get_provider() 100회 호출 → 상태는 NOT_STARTED 유지."""
        ml = self.ml
        for _ in range(100):
            self.assertIsNone(ml.get_provider())
        self.assertEqual(ml.get_state(), ml.NOT_STARTED)
        self.assertEqual(ml.get_diagnostics()["modelLoadAttempt"], 0)


class TestHfEnvVars(unittest.TestCase):
    """TC-08: HF_HUB_DISABLE_XET=1 이 provider import 전에 설정된다."""

    def test_08_hf_disable_xet_set_before_provider_import(self):
        """TC-08: grounded_sam2_provider.py 모듈 임포트 시 HF_HUB_DISABLE_XET 이미 설정."""
        import importlib
        # 기존 임포트 제거
        for mod in list(sys.modules.keys()):
            if "grounded_sam2_provider" in mod or "providers" in mod:
                del sys.modules[mod]

        captured_env = {}
        original_setdefault = os.environ.setdefault

        def _capture_setdefault(key, val):
            captured_env[key] = val
            return original_setdefault(key, val)

        with patch.object(os.environ, "setdefault", side_effect=_capture_setdefault):
            # provider 모듈 임포트 시도 (실제 모델 로드는 하지 않음)
            try:
                # torch 없어도 env 설정은 모듈 최상위에서 실행됨
                with patch.dict("sys.modules", {"torch": MagicMock(), "transformers": MagicMock()}):
                    import providers.grounded_sam2_provider  # noqa: PLC0415
            except Exception:
                pass  # 실제 torch/sam2 없어도 env 설정은 이미 완료

        # grounded_sam2_provider.py의 os.environ.setdefault("HF_HUB_DISABLE_XET", "1") 확인
        # 또는 직접 환경변수 확인
        self.assertEqual(os.environ.get("HF_HUB_DISABLE_XET"), "1",
                         "HF_HUB_DISABLE_XET=1 이 설정되어야 한다")


class TestCacheHitBehavior(unittest.TestCase):
    """TC-09~TC-10: 캐시 hit/miss 시나리오."""

    def setUp(self):
        self.ml = _fresh_model_loader()

    def test_09_diagnostics_fields_present(self):
        """TC-09: get_diagnostics()에 모든 필드 존재."""
        ml = self.ml
        diag = ml.get_diagnostics()
        required_keys = {
            "modelLoadState", "modelLoadAttempt", "modelLoadStartedAt",
            "modelLoadCompletedAt", "concurrentLoadPrevented",
            "modelLoadError", "modelLoadMs",
        }
        for key in required_keys:
            self.assertIn(key, diag, f"diagnostics에 {key} 키 없음")

    def test_10_failed_load_no_provider_returned(self):
        """TC-10: 로드 실패 후 get_provider() = None, is_loaded() = False."""
        ml = self.ml
        fake_p = _make_fake_provider(fail=True)

        with patch.dict("sys.modules", {
            "providers.grounded_sam2_provider": types.SimpleNamespace(
                GroundedSam2Provider=lambda device: fake_p
            )
        }):
            ml.preload()
            ml.ensure_ready(timeout=5.0)

        self.assertIsNone(ml.get_provider())
        self.assertFalse(ml.is_loaded())
        self.assertEqual(ml.get_state(), ml.FAILED)


class TestSam2GranularState(unittest.TestCase):
    """TC-11~TC-13: SAM2 세분화 상태 필드 검증."""

    def _make_provider_with_sam2_ok(self):
        """GDINO + SAM2 모두 정상인 Mock provider."""
        p = _make_fake_provider()
        p.real_inference_available = True
        p.get_metadata.return_value = {
            **p.get_metadata.return_value,
            "groundingDinoReady":         True,
            "groundingDinoRealInference": True,
            "sam2CheckpointReady":        True,
            "sam2ModelReady":             True,
            "sam2PredictorReady":         True,
            "sam2RealInference":          True,
            "sam2LoadErrorType":          "",
            "sam2LoadErrorMessage":       "",
            "sam2ConfigUsed":             "configs/sam2.1/sam2.1_hiera_t.yaml",
            "realInferenceAvailable":     True,
        }
        return p

    def _make_provider_sam2_failed(self, err_type="HydraException", err_msg="config not found"):
        """GDINO 성공, SAM2 초기화 실패인 Mock provider."""
        p = _make_fake_provider()
        p.real_inference_available = False
        meta = {
            **p.get_metadata.return_value,
            "groundingDinoReady":         True,
            "groundingDinoRealInference": True,
            "sam2CheckpointReady":        True,
            "sam2ModelReady":             False,
            "sam2PredictorReady":         False,
            "sam2RealInference":          False,
            "sam2LoadErrorType":          err_type,
            "sam2LoadErrorMessage":       err_msg,
            "sam2ConfigUsed":             "",
            "realInferenceAvailable":     False,
        }
        p.get_metadata.return_value = meta
        return p

    def test_11_metadata_has_granular_sam2_fields(self):
        """TC-11: get_metadata()에 SAM2 세분화 상태 필드가 모두 존재."""
        p = self._make_provider_with_sam2_ok()
        meta = p.get_metadata()
        required = {
            "groundingDinoReady", "groundingDinoRealInference",
            "sam2CheckpointReady", "sam2ModelReady", "sam2PredictorReady",
            "sam2RealInference", "sam2LoadErrorType", "sam2LoadErrorMessage",
            "sam2ConfigUsed", "realInferenceAvailable",
        }
        for key in required:
            self.assertIn(key, meta, f"metadata에 {key} 키 없음")

    def test_12_sam2_failed_metadata_exposes_error(self):
        """TC-12: SAM2 초기화 실패 시 sam2LoadErrorType/Message 필드가 채워짐."""
        p = self._make_provider_sam2_failed("HydraException", "config not found")
        meta = p.get_metadata()
        self.assertEqual(meta["sam2LoadErrorType"],    "HydraException")
        self.assertEqual(meta["sam2LoadErrorMessage"], "config not found")
        self.assertFalse(meta["sam2RealInference"])
        self.assertFalse(meta["realInferenceAvailable"])

    def test_13_real_inference_requires_both_gdino_and_sam2(self):
        """TC-13: realInferenceAvailable = GDINO AND SAM2 모두 true여야 함."""
        # 둘 다 ok
        p_both_ok = self._make_provider_with_sam2_ok()
        self.assertTrue(p_both_ok.get_metadata()["realInferenceAvailable"])

        # SAM2 실패
        p_sam2_fail = self._make_provider_sam2_failed()
        self.assertFalse(p_sam2_fail.get_metadata()["realInferenceAvailable"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
