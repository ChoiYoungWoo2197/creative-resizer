"""app.py /ready 엔드포인트 단위 테스트 (TC-14~TC-17).

Flask가 로컬에 설치되어 있지 않아도 동작하도록
Flask·PIL·model_loader 등 모든 의존성을 sys.modules mock으로 교체.
"""

from __future__ import annotations

import sys
import os
import types
import unittest
from unittest.mock import MagicMock


# ── Flask 전체 mock ──────────────────────────────────────────────────────────

class _FakeJsonResp:
    """jsonify() 반환값."""
    def __init__(self, data: dict):
        self._data = data


def _fake_jsonify(data: dict) -> _FakeJsonResp:
    return _FakeJsonResp(data)


class _FakeTestResponse:
    def __init__(self, data: dict, status_code: int):
        self.status_code = status_code
        self._data = data

    def get_json(self):
        return self._data


class _FakeTestClient:
    def __init__(self, app: "_FakeFlask"):
        self._app = app

    def get(self, path: str) -> _FakeTestResponse:
        fn = self._app._routes_get.get(path)
        if fn is None:
            return _FakeTestResponse({"error": "not_found"}, 404)
        result = fn()
        if isinstance(result, tuple):
            body_obj, code = result
        else:
            body_obj, code = result, 200
        data = body_obj._data if isinstance(body_obj, _FakeJsonResp) else {}
        return _FakeTestResponse(data, code)


class _FakeFlask:
    def __init__(self, name: str):
        self._routes_get: dict = {}
        self.config: dict = {}

    def get(self, path: str):
        def _deco(fn):
            self._routes_get[path] = fn
            return fn
        return _deco

    def post(self, path: str):
        def _deco(fn):
            return fn
        return _deco

    def test_client(self) -> _FakeTestClient:
        return _FakeTestClient(self)

    def run(self, *a, **kw):
        pass


# Flask 모듈 mock 객체
_flask_mock = types.ModuleType("flask")
_flask_mock.Flask   = _FakeFlask
_flask_mock.request = MagicMock()
_flask_mock.jsonify = _fake_jsonify

# PIL mock
_pil_image_mock = MagicMock()
_pil_mock = types.ModuleType("PIL")
_pil_mock.Image = _pil_image_mock
_pil_image_mock.open.return_value = MagicMock()


# ── 헬퍼: provider / model_loader mock 생성 ──────────────────────────────────

def _make_mock_provider(gdino_ok: bool, sam2_ok: bool):
    provider = MagicMock()
    provider.device = "cpu"
    provider.models_loaded = True
    provider.get_metadata.return_value = {
        "groundingDinoRealInference": gdino_ok,
        "sam2RealInference":          sam2_ok,
        "sam2CheckpointReady":        sam2_ok,
        "sam2ModelReady":             sam2_ok,
        "sam2PredictorReady":         sam2_ok,
        "sam2ConfigUsed":  "configs/sam2.1/sam2.1_hiera_t.yaml" if sam2_ok else "",
        "sam2LoadErrorType":    "" if sam2_ok else "HydraException",
        "sam2LoadErrorMessage": "" if sam2_ok else "config not found",
        "device":                     "cpu",
        "groundingDinoModelId":       "IDEA-Research/grounding-dino-tiny",
        "realInferenceAvailable":     gdino_ok and sam2_ok,
        "bboxFallbackEnabled":        True,
        "modelCachePath":             "/models",
        "groundingDinoCacheReady":    gdino_ok,
        "sam2CacheReady":             sam2_ok,
    }
    return provider


def _make_ml_mock(state: str, provider):
    ml = types.ModuleType("model_loader")
    ml.NOT_STARTED = "NOT_STARTED"
    ml.LOADING     = "LOADING"
    ml.READY       = "READY"
    ml.FAILED      = "FAILED"
    ml.get_state       = lambda: state
    ml.get_provider    = lambda: (provider if state == "READY" else None)
    ml.get_diagnostics = lambda: {
        "modelLoadState": state, "modelLoadAttempt": 1,
        "modelLoadStartedAt": 0.0, "modelLoadCompletedAt": 0.0,
        "concurrentLoadPrevented": 0, "modelLoadError": None, "modelLoadMs": 100,
    }
    ml.get_load_error  = lambda: None
    ml.preload         = lambda: None
    ml.is_loaded       = lambda: state == "READY"
    ml.ensure_ready    = lambda timeout=5.0: state == "READY"
    return ml


def _build_client(state: str, gdino_ok: bool, sam2_ok: bool) -> _FakeTestClient:
    """지정된 상태/모델 상태로 Flask test client 반환."""
    provider = _make_mock_provider(gdino_ok, sam2_ok)
    ml_mock  = _make_ml_mock(state, provider)

    cache_mock = types.ModuleType("cache")
    cache_mock.get = lambda k: None
    cache_mock.put = lambda k, v: None
    cache_mock.compute_cache_key = lambda *a: "k"

    mq_mock = types.ModuleType("mask_quality")
    mq_mock.score_external_mask = lambda **kw: {
        "overallMaskScore": 80.0, "edgeSharpness": 0.5,
        "leakRisk": 0.1, "hardFail": False, "hardFailReason": "", "fragmentCount": 1,
    }

    # app 모듈 캐시 제거 후 재임포트
    for mod in list(sys.modules.keys()):
        if mod in ("app", "model_loader", "cache", "mask_quality"):
            del sys.modules[mod]

    patched = {
        "flask":        _flask_mock,
        "PIL":          _pil_mock,
        "PIL.Image":    _pil_image_mock,
        "model_loader": ml_mock,
        "cache":        cache_mock,
        "mask_quality": mq_mock,
    }
    original = {}
    for k, v in patched.items():
        original[k] = sys.modules.get(k)
        sys.modules[k] = v

    try:
        import app as _app   # noqa: PLC0415
        flask_app = _app.app
        flask_app.config["TESTING"] = True
        return flask_app.test_client()
    finally:
        for k, v in original.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
        sys.modules.pop("app", None)


# ── 테스트 ───────────────────────────────────────────────────────────────────

class TestReadyEndpoint(unittest.TestCase):
    """TC-14~TC-17: /ready strict endpoint."""

    def test_14_ready_200_when_both_ok(self):
        """TC-14: GDINO AND SAM2 모두 준비 시 HTTP 200, ready=true."""
        client = _build_client(state="READY", gdino_ok=True, sam2_ok=True)
        resp = client.get("/ready")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ready"])
        self.assertTrue(data["groundingDinoOk"])
        self.assertTrue(data["sam2Ok"])

    def test_15_ready_503_when_sam2_fails(self):
        """TC-15: GDINO 정상, SAM2 실패 시 HTTP 503, ready=false."""
        client = _build_client(state="READY", gdino_ok=True, sam2_ok=False)
        resp = client.get("/ready")
        self.assertEqual(resp.status_code, 503)
        data = resp.get_json()
        self.assertFalse(data["ready"])
        self.assertTrue(data["groundingDinoOk"])
        self.assertFalse(data["sam2Ok"])
        self.assertIn("sam2LoadErrorType", data)

    def test_16_ready_503_when_provider_not_loaded(self):
        """TC-16: 모델 로드 전(LOADING 상태) /ready → HTTP 503."""
        client = _build_client(state="LOADING", gdino_ok=False, sam2_ok=False)
        resp = client.get("/ready")
        self.assertEqual(resp.status_code, 503)
        data = resp.get_json()
        self.assertFalse(data["ready"])
        self.assertIn("modelState", data)

    def test_17_ready_503_when_gdino_fails(self):
        """TC-17: GDINO 실패 시 sam2=true여도 HTTP 503."""
        client = _build_client(state="READY", gdino_ok=False, sam2_ok=True)
        resp = client.get("/ready")
        self.assertEqual(resp.status_code, 503)
        data = resp.get_json()
        self.assertFalse(data["ready"])
        self.assertFalse(data["groundingDinoOk"])
        self.assertTrue(data["sam2Ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
