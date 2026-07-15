"""간단한 in-memory 결과 캐시 (image hash + prompt → detections).

환경변수 CREATIVE_SEGMENTATION_CACHE_ENABLED=true 일 때만 활성화.
TTL: 1시간 (LRU 미구현 — 메모리 급증 방지 위해 최대 500개).
"""

from __future__ import annotations
import os
import hashlib
import time
import threading
from typing import Any

_ENABLED = os.environ.get("CREATIVE_SEGMENTATION_CACHE_ENABLED", "true").lower() == "true"
_MAX_ENTRIES = 500
_TTL = 3600

_cache: dict[str, tuple[Any, float]] = {}
_lock = threading.Lock()


def compute_cache_key(image_bytes: bytes, prompts_json: str, min_confidence: float) -> str:
    h = hashlib.sha256(image_bytes + prompts_json.encode() + str(min_confidence).encode())
    return h.hexdigest()[:32]


def get(key: str) -> Any | None:
    if not _ENABLED:
        return None
    with _lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        val, ts = entry
        if time.time() - ts > _TTL:
            del _cache[key]
            return None
        return val


def put(key: str, value: Any) -> None:
    if not _ENABLED:
        return
    with _lock:
        if len(_cache) >= _MAX_ENTRIES:
            # 가장 오래된 50개 제거
            oldest = sorted(_cache.items(), key=lambda x: x[1][1])[:50]
            for k, _ in oldest:
                del _cache[k]
        _cache[key] = (value, time.time())


def stats() -> dict:
    with _lock:
        return {"size": len(_cache), "enabled": _ENABLED, "ttl": _TTL}
