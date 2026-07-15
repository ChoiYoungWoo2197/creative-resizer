"""Singleton model loader — 서비스 시작 시 1회 초기화 후 메모리 유지.

두 가지 모드:
  lazy=True (기본): 첫 요청 시 로드 (startup 빠름)
  lazy=False: 서비스 시작 시 즉시 로드
"""

from __future__ import annotations
import os
import logging
import threading
import time

log = logging.getLogger("segmentation.model_loader")

_lock = threading.Lock()
_provider = None
_load_started = False
_load_complete = False
_load_error: str | None = None
_load_ms: int = 0


def get_provider():
    """싱글톤 provider 반환. 아직 로드 안 됐으면 즉시 로드."""
    global _load_started
    with _lock:
        if _provider is not None:
            return _provider
        if not _load_started:
            _load_started = True
        else:
            return None  # 로드 중

    _do_load()
    return _provider


def is_loaded() -> bool:
    return _load_complete


def get_load_error() -> str | None:
    return _load_error


def get_load_ms() -> int:
    return _load_ms


def preload() -> None:
    """서비스 시작 시 미리 로드 (비동기)."""
    t = threading.Thread(target=_do_load, daemon=True)
    t.start()


# ── 내부 ─────────────────────────────────────────────────────────────────────

def _do_load() -> None:
    global _provider, _load_complete, _load_error, _load_ms

    provider_name = os.environ.get("CREATIVE_SEGMENTATION_PROVIDER", "grounded-sam2")
    device = os.environ.get("CREATIVE_SEGMENTATION_DEVICE", "auto")

    t0 = time.time()
    try:
        if provider_name == "grounded-sam2":
            from providers.grounded_sam2_provider import GroundedSam2Provider
            p = GroundedSam2Provider(device=device)
            p.load_models()
            with _lock:
                _provider = p
                _load_complete = True
                _load_ms = int((time.time() - t0) * 1000)
                log.info("모델 로드 완료: provider=%s device=%s ms=%d",
                         provider_name, p.device, _load_ms)
        else:
            raise ValueError(f"Unknown provider: {provider_name}")
    except Exception as e:
        _load_error = str(e)
        _load_ms = int((time.time() - t0) * 1000)
        log.error("모델 로드 실패: %s (ms=%d)", e, _load_ms)
