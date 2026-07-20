"""Singleton model loader — single-flight, double-checked locking.

상태 전이:
  NOT_STARTED → LOADING → READY
                        → FAILED

LOADING 상태에서 다른 요청이 오면 중복 로드를 방지한다.
health endpoint는 모델 다운로드를 새로 시작하지 않는다.
"""

from __future__ import annotations
import os
import logging
import threading
import time

log = logging.getLogger("segmentation.model_loader")

# ── 상태 상수 ─────────────────────────────────────────────────────────────────
NOT_STARTED = "NOT_STARTED"
LOADING     = "LOADING"
READY       = "READY"
FAILED      = "FAILED"

# ── 전역 상태 (Condition으로 보호) ────────────────────────────────────────────
_cond     = threading.Condition()
_state    = NOT_STARTED
_provider = None
_load_error: str | None = None
_load_ms:   int = 0

# 진단 메타데이터
_load_attempt:          int   = 0
_load_started_at:       float = 0.0
_load_completed_at:     float = 0.0
_concurrent_prevented:  int   = 0


# ── Public API ────────────────────────────────────────────────────────────────

def get_provider():
    """READY 상태면 provider 반환, 그 외엔 None. 모델 로드를 새로 시작하지 않는다."""
    with _cond:
        return _provider if _state == READY else None


def get_state() -> str:
    with _cond:
        return _state


def get_load_error() -> str | None:
    with _cond:
        return _load_error


def get_load_ms() -> int:
    with _cond:
        return _load_ms


def get_diagnostics() -> dict:
    """health endpoint용 로더 진단 정보."""
    with _cond:
        return {
            "modelLoadState":          _state,
            "modelLoadAttempt":        _load_attempt,
            "modelLoadStartedAt":      _load_started_at or None,
            "modelLoadCompletedAt":    _load_completed_at or None,
            "concurrentLoadPrevented": _concurrent_prevented,
            "modelLoadError":          _load_error,
            "modelLoadMs":             _load_ms,
        }


def preload() -> None:
    """서비스 시작 시 비동기 모델 로드.

    - 이미 LOADING / READY 상태면 즉시 반환 (중복 방지).
    - NOT_STARTED 일 때만 LOADING으로 전이하고 스레드 시작.
    """
    global _state, _load_attempt, _load_started_at, _concurrent_prevented
    with _cond:
        if _state in (LOADING, READY):
            _concurrent_prevented += 1
            log.debug("preload() 중복 호출 무시: state=%s prevented=%d",
                      _state, _concurrent_prevented)
            return
        # NOT_STARTED or FAILED → LOADING
        _state = LOADING
        _load_attempt += 1
        _load_started_at = time.time()
        log.info("모델 로드 시작 (attempt=%d)", _load_attempt)

    t = threading.Thread(target=_do_load, daemon=True, name="model-loader")
    t.start()


def ensure_ready(timeout: float = 0.0) -> bool:
    """READY 상태이면 True. timeout > 0이면 최대 해당 초 대기."""
    with _cond:
        if _state == READY:
            return True
        if timeout > 0:
            _cond.wait_for(lambda: _state in (READY, FAILED), timeout=timeout)
        return _state == READY


# ── 내부 ─────────────────────────────────────────────────────────────────────

def _do_load() -> None:
    """실제 모델 로드 — 반드시 LOADING 상태에서만 진입."""
    global _state, _provider, _load_error, _load_ms, _load_completed_at

    provider_name = os.environ.get("CREATIVE_SEGMENTATION_PROVIDER", "grounded-sam2")
    device        = os.environ.get("CREATIVE_SEGMENTATION_DEVICE", "auto")

    t0 = time.time()
    try:
        if provider_name == "grounded-sam2":
            from providers.grounded_sam2_provider import GroundedSam2Provider
            p = GroundedSam2Provider(device=device)
            p.load_models()
            elapsed = int((time.time() - t0) * 1000)
            with _cond:
                _provider = p
                _state    = READY
                _load_ms  = elapsed
                _load_completed_at = time.time()
                _load_error = None
                _cond.notify_all()
            log.info("모델 로드 완료: provider=%s device=%s ms=%d",
                     provider_name, p.device, elapsed)
        else:
            raise ValueError(f"Unknown provider: {provider_name}")

    except Exception as e:
        elapsed = int((time.time() - t0) * 1000)
        with _cond:
            _state  = FAILED
            _load_error = str(e)
            _load_ms = elapsed
            _load_completed_at = time.time()
            _cond.notify_all()
        log.error("모델 로드 실패 (ms=%d): %s", elapsed, e)


# ── 하위 호환 alias (기존 코드에서 is_loaded / get_load_error 사용) ────────────

def is_loaded() -> bool:
    return get_state() == READY
