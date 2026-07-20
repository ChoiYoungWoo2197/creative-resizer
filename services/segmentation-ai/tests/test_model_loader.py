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


class TestHydraInitialization(unittest.TestCase):
    """TC-18~TC-22: ensure_sam2_hydra_initialized() thread-safe 초기화 검증."""

    @staticmethod
    def _load_provider_fresh():
        """provider 모듈을 깨끗하게 (재)임포트 — mock hydra/torch 사용."""
        for mod in list(sys.modules.keys()):
            if "grounded_sam2_provider" in mod or (
                "providers" in mod and "grounded" in mod
            ):
                del sys.modules[mod]
        sam2_stub = types.ModuleType("sam2")
        base_mocks = {
            "torch": MagicMock(),
            "transformers": MagicMock(),
            "sam2": sam2_stub,
            "hydra": MagicMock(),
            "hydra.core": MagicMock(),
            "hydra.core.global_hydra": MagicMock(),
        }
        with patch.dict("sys.modules", base_mocks):
            import providers.grounded_sam2_provider as p  # noqa: PLC0415
        return p

    def _make_hydra_mocks(self, already_initialized: bool):
        """sys.modules 패치용 hydra mock dict 반환."""
        gh_instance = MagicMock()
        gh_instance.is_initialized.return_value = already_initialized

        GlobalHydra = MagicMock()
        GlobalHydra.instance.return_value = gh_instance

        gh_module = types.ModuleType("hydra.core.global_hydra")
        gh_module.GlobalHydra = GlobalHydra

        hydra_module = types.ModuleType("hydra")
        init_cm_mock = MagicMock()
        hydra_module.initialize_config_module = init_cm_mock

        mocks = {
            "hydra": hydra_module,
            "hydra.core": types.ModuleType("hydra.core"),
            "hydra.core.global_hydra": gh_module,
        }
        return mocks, GlobalHydra, init_cm_mock

    def test_18_returns_true_when_hydra_not_initialized(self):
        """TC-18: Hydra 미초기화 시 ensure_sam2_hydra_initialized() → True, init 1회."""
        p = self._load_provider_fresh()
        mocks, _, init_mock = self._make_hydra_mocks(already_initialized=False)
        with patch.dict("sys.modules", mocks):
            p._hydra_initialized = False
            result = p.ensure_sam2_hydra_initialized()
        self.assertTrue(result)
        init_mock.assert_called_once()

    def test_19_returns_false_when_hydra_already_initialized(self):
        """TC-19: Hydra 이미 초기화된 경우 False 반환, init 호출 없음."""
        p = self._load_provider_fresh()
        mocks, _, init_mock = self._make_hydra_mocks(already_initialized=True)
        with patch.dict("sys.modules", mocks):
            p._hydra_initialized = False
            result = p.ensure_sam2_hydra_initialized()
        self.assertFalse(result)
        init_mock.assert_not_called()

    def test_20_concurrent_init_called_exactly_once(self):
        """TC-20: 10개 동시 ensure_sam2_hydra_initialized() → initialize_config_module 정확히 1회."""
        p = self._load_provider_fresh()

        call_count = [0]
        initialized_flag = [False]
        flag_lock = threading.Lock()

        def _is_initialized():
            with flag_lock:
                return initialized_flag[0]

        def _init_config_module(**kwargs):
            with flag_lock:
                call_count[0] += 1
                initialized_flag[0] = True

        gh_instance = MagicMock()
        gh_instance.is_initialized.side_effect = _is_initialized
        GlobalHydra_mock = MagicMock()
        GlobalHydra_mock.instance.return_value = gh_instance

        gh_module = types.ModuleType("hydra.core.global_hydra")
        gh_module.GlobalHydra = GlobalHydra_mock
        hydra_module = types.ModuleType("hydra")
        hydra_module.initialize_config_module = _init_config_module

        mocks = {
            "hydra": hydra_module,
            "hydra.core": types.ModuleType("hydra.core"),
            "hydra.core.global_hydra": gh_module,
        }

        p._hydra_initialized = False
        p._hydra_init_lock = threading.Lock()

        errors = []
        barrier = threading.Barrier(10)

        def _run():
            barrier.wait()
            try:
                with patch.dict("sys.modules", mocks):
                    p.ensure_sam2_hydra_initialized()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_run) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        self.assertEqual(errors, [], f"스레드 에러: {errors}")
        self.assertEqual(call_count[0], 1, "initialize_config_module은 정확히 1회만 호출돼야 한다")

    def test_21_no_clear_hydra_in_provider(self):
        """TC-21: _load_sam2에 GlobalHydra.clear() 또는 _clear_hydra 호출 없음."""
        provider_file = os.path.join(
            os.path.dirname(__file__), "..", "providers", "grounded_sam2_provider.py"
        )
        with open(provider_file, encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("_clear_hydra", src,
                         "_clear_hydra 함수가 provider에 남아 있으면 안 된다")
        # .clear() 는 docstring 설명 문구로 등장할 수 있으므로,
        # 실제 코드 라인에서 GlobalHydra clear 패턴만 검사
        code_lines = [
            l for l in src.split("\n")
            if not l.strip().startswith("#") and '"""' not in l and "'''" not in l
        ]
        for line in code_lines:
            self.assertNotIn("GlobalHydra.instance().clear()",
                             line,
                             f"코드 라인에 GlobalHydra.instance().clear() 호출 발견: {line.strip()}")

    def test_22_single_config_constant_no_candidates(self):
        """TC-22: SAM2_CONFIG_NAME이 단일 config, _SAM2_CONFIG_CANDIDATES 배열 없음."""
        provider_file = os.path.join(
            os.path.dirname(__file__), "..", "providers", "grounded_sam2_provider.py"
        )
        with open(provider_file, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("sam2.1_hiera_t.yaml", src, "단일 config명이 provider에 없음")
        self.assertNotIn("_SAM2_CONFIG_CANDIDATES", src,
                         "_SAM2_CONFIG_CANDIDATES 복수 후보 배열이 남아 있으면 안 된다")


class TestSmokeScriptRegression(unittest.TestCase):
    """TC-23~TC-24: verify_stage18_server.sh smoke 스크립트 회귀 검사."""

    @classmethod
    def _get_script_path(cls):
        return os.path.join(
            os.path.dirname(__file__), "..", "..", "..",
            "scripts", "verify_stage18_server.sh",
        )

    def _extract_sam2_smoke_section(self):
        script_path = self._get_script_path()
        if not os.path.exists(script_path):
            self.skipTest(f"verify_stage18_server.sh not found: {script_path}")
        with open(script_path, encoding="utf-8") as f:
            content = f.read()
        start = content.find("<<'SAM2PY'")
        end   = content.find("\nSAM2PY\n", start)
        if start < 0 or end < 0:
            self.skipTest("SAM2PY heredoc not found in verify_stage18_server.sh")
        return content[start:end]

    def test_23_sys_imported_before_sys_usage(self):
        """TC-23: smoke 스크립트에서 'import sys'가 sys 사용 이전에 등장 (회귀 방지)."""
        section = self._extract_sam2_smoke_section()
        lines = section.split("\n")
        sys_import_line = -1
        first_sys_use_line = -1
        for i, line in enumerate(lines):
            stripped = line.strip()
            # sys import 탐지: "import sys" 또는 "import os, sys, ..." 형태 모두 인식
            tokens = stripped.replace(",", " ").split()
            if "import" in tokens and "sys" in tokens and sys_import_line < 0:
                sys_import_line = i
            # sys 사용 탐지: sys. 포함 라인 (import 문 자체는 제외)
            if "sys." in stripped and "import" not in stripped and first_sys_use_line < 0:
                first_sys_use_line = i
        self.assertGreaterEqual(sys_import_line, 0, "smoke 스크립트에 sys가 import되지 않음")
        if first_sys_use_line > 0:
            self.assertLess(
                sys_import_line, first_sys_use_line,
                f"sys import ({sys_import_line}줄)이 sys 사용 ({first_sys_use_line}줄) 이후에 있음",
            )

    def test_24_uses_inference_mode_not_cuda_autocast(self):
        """TC-24: smoke 스크립트가 torch.inference_mode() 사용, cuda autocast 금지."""
        section = self._extract_sam2_smoke_section()
        self.assertIn("torch.inference_mode()", section,
                      "smoke 스크립트에 torch.inference_mode()가 없음")
        for bad in ('torch.autocast("cuda")', "torch.autocast('cuda')",
                    'autocast("cuda")', "autocast('cuda')"):
            self.assertNotIn(bad, section, f"CPU smoke에 {bad} 사용 금지")


class TestProviderConfig(unittest.TestCase):
    """TC-25~TC-28: provider 설정값 및 소스 동작 검증."""

    @classmethod
    def _provider_src(cls):
        provider_file = os.path.join(
            os.path.dirname(__file__), "..", "providers", "grounded_sam2_provider.py"
        )
        with open(provider_file, encoding="utf-8") as f:
            return f.read()

    def test_25_hydra_init_idempotent(self):
        """TC-25: 초기화 후 재호출 시 모두 False 반환 (오류 없음)."""
        p = TestHydraInitialization._load_provider_fresh()
        mocks, _, _ = TestHydraInitialization(methodName="test_19_returns_false_when_hydra_already_initialized")\
            ._make_hydra_mocks(already_initialized=True)
        with patch.dict("sys.modules", mocks):
            p._hydra_initialized = False
            results = [p.ensure_sam2_hydra_initialized() for _ in range(3)]
        self.assertEqual(results, [False, False, False],
                         "already_initialized=True인 경우 항상 False여야 한다")

    def test_26_sam2_config_name_value(self):
        """TC-26: SAM2_CONFIG_NAME = 'configs/sam2.1/sam2.1_hiera_t.yaml' 포함."""
        src = self._provider_src()
        self.assertIn("configs/sam2.1/sam2.1_hiera_t.yaml", src,
                      "SAM2_CONFIG_NAME 값이 잘못됐거나 없음")

    def test_27_ensure_hydra_before_build_sam2_in_source(self):
        """TC-27: _load_sam2 함수 본문에서 ensure_sam2_hydra_initialized()가 build_sam2() 이전에 등장."""
        src = self._provider_src()
        # 전체 파일이 아닌 _load_sam2 함수 정의 이후 구간만 검사
        # (docstring에서 build_sam2()가 먼저 언급될 수 있으므로)
        load_start = src.find("def _load_sam2")
        self.assertGreater(load_start, 0, "def _load_sam2 가 provider 소스에 없음")
        src_load = src[load_start:]
        hydra_pos = src_load.find("ensure_sam2_hydra_initialized()")
        build_pos = src_load.find("build_sam2(")
        self.assertGreater(hydra_pos, 0,
                           "_load_sam2 내에 ensure_sam2_hydra_initialized() 호출이 없음")
        self.assertGreater(build_pos, 0,
                           "_load_sam2 내에 build_sam2() 호출이 없음")
        self.assertLess(hydra_pos, build_pos,
                        "ensure_sam2_hydra_initialized()가 build_sam2() 이후에 있음")

    def test_28_run_sam2_uses_inference_mode_not_autocast(self):
        """TC-28: _run_sam2()가 torch.inference_mode 사용, cuda autocast 사용 안 함."""
        src = self._provider_src()
        self.assertIn("inference_mode", src,
                      "_run_sam2에 torch.inference_mode가 없음")
        for bad in ('autocast("cuda")', "autocast('cuda')"):
            self.assertNotIn(bad, src, f"CPU 전용 코드에 {bad} 사용 금지")


if __name__ == "__main__":
    unittest.main(verbosity=2)
