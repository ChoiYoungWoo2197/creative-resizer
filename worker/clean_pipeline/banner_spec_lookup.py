"""BannerSpec DB lookup — MongoDB `creative_resizer.banner_spec` 컬렉션.

slug 기반으로 safe zone inset 값(safeTop/Right/Bottom/Left)을 조회한다.
컬렉션에 값이 없거나(null) MongoDB 연결 실패 시 None을 반환하며,
호출 측에서 fallback 처리한다.

연결은 최초 1회만 생성하고 프로세스 전체에서 재사용한다(MongoClient 권장 패턴).
"""
from __future__ import annotations

import os
import logging
from typing import Optional

_log = logging.getLogger(__name__)

# ── Singleton client ─────────────────────────────────────────────────────────

_client = None
_collection = None


def _get_collection():
    global _client, _collection
    if _collection is not None:
        return _collection

    uri = os.environ.get("MONGODB_URI", "")
    if not uri:
        _log.warning("[BANNER_SPEC] MONGODB_URI not set — safe zone lookup disabled")
        return None

    try:
        from pymongo import MongoClient
        _client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        _collection = _client["creative_resizer"]["banner_spec"]
        _log.info("[BANNER_SPEC] MongoDB connected")
    except Exception as exc:
        _log.warning(f"[BANNER_SPEC] MongoDB connect failed: {exc}")
        _collection = None

    return _collection


# ── Public API ────────────────────────────────────────────────────────────────


def lookup(slug: str) -> Optional[dict]:
    """slug로 banner_spec 문서를 조회하고 safe zone 딕셔너리를 반환한다.

    반환 형식:
        {"safe_top": int, "safe_right": int, "safe_bottom": int, "safe_left": int}

    safe zone 수치가 null이거나 문서가 없으면 None을 반환한다.
    """
    if not slug:
        return None

    col = _get_collection()
    if col is None:
        return None

    try:
        doc = col.find_one({"slug": slug}, {"safeTop": 1, "safeRight": 1, "safeBottom": 1, "safeLeft": 1, "_id": 0})
    except Exception as exc:
        _log.warning(f"[BANNER_SPEC] lookup failed slug={slug!r}: {exc}")
        return None

    if not doc:
        _log.debug(f"[BANNER_SPEC] slug={slug!r} not found in DB")
        return None

    top    = doc.get("safeTop")
    right  = doc.get("safeRight")
    bottom = doc.get("safeBottom")
    left   = doc.get("safeLeft")

    # 하나라도 null이면 수치 미확인 지면 — fallback으로 처리
    if any(v is None for v in (top, right, bottom, left)):
        _log.debug(f"[BANNER_SPEC] slug={slug!r} found but safe zone is null")
        return None

    result = {
        "safe_top":    int(top),
        "safe_right":  int(right),
        "safe_bottom": int(bottom),
        "safe_left":   int(left),
    }
    _log.info(
        f"[BANNER_SPEC] slug={slug!r} → "
        f"top={result['safe_top']} right={result['safe_right']} "
        f"bottom={result['safe_bottom']} left={result['safe_left']}"
    )
    return result
