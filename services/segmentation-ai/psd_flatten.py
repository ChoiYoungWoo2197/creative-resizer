"""PSD → PIL Image 변환 파이프라인 (Stage 18.3).

컨테이너 내부에서 PSD를 직접 처리. 호스트 변환 의존성 없음.

우선순위 전략:
  1. psd.composite()                              → psd_tools_composite
  2. psd.composite(apply_icc=False)               → psd_tools_composite
  3. psd.composite(force=True, apply_icc=False)   → psd_tools_composite
  4. psd.topil() (일부 버전)                      → psd_tools_composite
  5. psd-tools 열기 성공 + Pillow 병합 데이터     → psd_tools_merged
  6. Pure Pillow fallback                          → pillow_psd_fallback

flattenMethod 의미:
  psd_tools_composite : psd-tools 레이어 렌더링 성공
  psd_tools_merged    : psd-tools 검증 성공, Pillow로 병합 데이터 읽기
  pillow_psd_fallback : psd-tools 미설치/열기 실패, Pillow 단독 (PASS 불가)
  pillow_image        : 일반 이미지 (PNG/JPG 등)
"""
from __future__ import annotations

import io
import logging
import time

from PIL import Image

log = logging.getLogger("segmentation.psd_flatten")

_PSD_MAGIC = b"8BPS"


def flatten_input(
    image_bytes: bytes,
    original_filename: str = "input",
) -> tuple[Image.Image, str, dict]:
    """이미지 bytes → (PIL RGB, flatten_method, flatten_meta).

    PSD 처리 흐름:
      1. psd-tools 설치/import 진단
      2. PSDImage.open() — 레이어 구조 파악
      3. composite 전략 순차 시도 (default / no_icc / force / topil)
      4. 전부 실패 시 psd_tools_merged (Pillow 병합 데이터)
      5. psd-tools 미설치면 pillow_psd_fallback
    """
    t0 = time.time()
    meta: dict = {
        "sourceFilename":           original_filename,
        "flattenExecutionLocation": "segmentation_ai_container",
        "sourceFileSizeBytes":      len(image_bytes),
    }

    # ── 일반 이미지 경로 ──────────────────────────────────────────────────────
    if image_bytes[:4] != _PSD_MAGIC:
        try:
            img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            meta.update({
                "sourceType":   "image",
                "flattenMethod": "pillow_image",
                "width":         img.width,
                "height":        img.height,
                "flattenMs":     int((time.time() - t0) * 1000),
                "warnings":      [],
            })
            return img, "pillow_image", meta
        except Exception as e:
            raise ValueError(f"image_open_failed: {e}") from e

    # ── PSD 경로 ──────────────────────────────────────────────────────────────
    meta["sourceType"] = "psd"
    meta["sourceMimeType"] = "image/vnd.adobe.photoshop"
    warnings_list: list[str] = []

    _fill_psd_tools_meta(meta)

    psd_obj = None
    if meta.get("psdToolsImportSucceeded"):
        psd_obj = _open_psd(image_bytes, meta)

    if psd_obj is not None:
        result, strategy, strategy_errors = _try_composite_strategies(psd_obj)
        meta["compositeStrategyErrors"] = strategy_errors
        meta["compositeStrategiesAttempted"] = list(strategy_errors.keys())

        if result is not None:
            result_rgb = result.convert("RGB")
            meta.update({
                "psdCompositeSucceeded": True,
                "psdCompositeStrategy":  strategy,
                "flattenMethod":         "psd_tools_composite",
                "width":                 result_rgb.width,
                "height":                result_rgb.height,
                "flattenMs":             int((time.time() - t0) * 1000),
                "warnings":              warnings_list,
            })
            log.info("PSD flatten: psd_tools_composite strategy=%s", strategy)
            return result_rgb, "psd_tools_composite", meta

        # 모든 composite 전략 실패 → psd_tools_merged
        meta["psdCompositeSucceeded"] = False
        warnings_list.append("composite_all_strategies_failed")
        log.warning(
            "psd-tools composite 실패(strategy_errors=%s) → psd_tools_merged",
            list(strategy_errors.keys()),
        )
        try:
            merged = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            meta.update({
                "flattenMethod": "psd_tools_merged",
                "width":         merged.width,
                "height":        merged.height,
                "flattenMs":     int((time.time() - t0) * 1000),
                "warnings":      warnings_list + ["psd_tools_merged_pillow_read"],
            })
            log.info("PSD flatten: psd_tools_merged (pre-merged data via Pillow)")
            return merged, "psd_tools_merged", meta
        except Exception as em:
            meta["psdMergedError"] = str(em)[:200]
            warnings_list.append(f"psd_tools_merged_failed:{type(em).__name__}")
            log.warning("psd_tools_merged 실패: %s", em)
    else:
        if not meta.get("psdToolsImportSucceeded"):
            log.warning(
                "psd-tools import 실패(installed=%s) → pillow_psd_fallback",
                meta.get("psdToolsInstalled"),
            )

    # ── Pillow fallback ────────────────────────────────────────────────────────
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        meta.update({
            "psdCompositeSucceeded": False,
            "flattenMethod":         "pillow_psd_fallback",
            "width":                 img.width,
            "height":                img.height,
            "flattenMs":             int((time.time() - t0) * 1000),
            "warnings":              warnings_list + ["pillow_psd_fallback_used"],
        })
        return img, "pillow_psd_fallback", meta
    except Exception as e:
        raise ValueError(f"PSD image_parse_failed: {e}") from e


# ── 내부 헬퍼 ─────────────────────────────────────────────────────────────────

def _fill_psd_tools_meta(meta: dict) -> None:
    """psd-tools 설치 + import 상태를 meta dict에 기록."""
    meta["psdToolsInstalled"]       = False
    meta["psdToolsVersion"]         = None
    meta["psdToolsImportSucceeded"] = False

    try:
        import importlib.metadata
        meta["psdToolsVersion"]   = importlib.metadata.version("psd-tools")
        meta["psdToolsInstalled"] = True
    except Exception as e:
        meta["psdToolsVersionErrorType"] = type(e).__name__
        return

    try:
        from psd_tools import PSDImage  # noqa: F401
        meta["psdToolsImportSucceeded"] = True
    except ImportError as e:
        meta["psdToolsImportError"] = str(e)[:200]


def _open_psd(image_bytes: bytes, meta: dict):
    """PSDImage.open() 실행. 실패 시 None 반환."""
    try:
        from psd_tools import PSDImage
        psd = PSDImage.open(io.BytesIO(image_bytes))
        meta["psdImageOpenSucceeded"] = True
        meta["psdWidth"]     = psd.width
        meta["psdHeight"]    = psd.height
        meta["psdColorMode"] = str(psd.color_mode) if hasattr(psd, "color_mode") else "N/A"
        meta["psdLayerCount"] = _count_layers(psd)
        return psd
    except Exception as e:
        meta["psdImageOpenSucceeded"] = False
        meta["psdOpenErrorType"]      = type(e).__name__
        meta["psdOpenError"]          = str(e)[:200]
        log.warning("PSDImage.open 실패: %s", e)
        return None


def _try_composite_strategies(psd) -> tuple:
    """composite 전략을 우선순위 순서로 시도.

    Returns: (PIL Image | None, strategy_name, {strategy_name: error_msg})
    """
    strategies: list[tuple[str, dict]] = [
        ("composite_default",  {}),
        ("composite_no_icc",   {"apply_icc": False}),
        ("composite_force",    {"force": True, "apply_icc": False}),
    ]

    errors: dict[str, str] = {}

    for name, kwargs in strategies:
        try:
            log.debug("composite 시도: strategy=%s kwargs=%s", name, kwargs)
            result = psd.composite(**kwargs)
            if result is not None:
                log.info("composite 성공: strategy=%s size=%s mode=%s", name, result.size, result.mode)
                return result, name, errors
            errors[name] = "returned_none"
            log.debug("composite None 반환: strategy=%s", name)
        except Exception as e:
            err_msg = f"{type(e).__name__}: {str(e)[:120]}"
            errors[name] = err_msg
            log.debug("composite 실패: strategy=%s err=%s", name, err_msg)

    # topil() — 병합 데이터 직접 접근 (일부 psd-tools 버전)
    if hasattr(psd, "topil"):
        try:
            result = psd.topil()
            if result is not None:
                log.info("composite 성공: strategy=topil size=%s", result.size)
                return result, "topil", errors
            errors["topil"] = "returned_none"
        except Exception as e:
            errors["topil"] = f"{type(e).__name__}: {str(e)[:120]}"
            log.debug("topil 실패: %s", errors["topil"])

    return None, "", errors


def _count_layers(psd) -> int:
    """레이어 수를 안전하게 셈 (실패 시 -1)."""
    try:
        return len(list(psd))
    except Exception:
        return -1
