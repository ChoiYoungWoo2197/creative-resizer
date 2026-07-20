"""PSD -> PIL Image 변환 파이프라인 (Stage 18.4).

컨테이너 내부에서 PSD를 직접 처리. 호스트 변환 의존성 없음.

flattenMethod 우선순위:
  psd_tools_composite  : psd-tools 레이어 composite 성공
  psd_tools_merged     : psd-tools open 성공 + Pillow 병합 데이터
  psd_embedded_composite : psd-tools open 실패(내부 메타 오류) + 엄격 검증 Pillow composite
  pillow_psd_fallback  : 분류 불가 오류 또는 검증 실패 (PARTIAL)
  pillow_image         : 일반 이미지 (PNG/JPG 등)

Stage 18.4 추가:
  PsdHeaderInfo       : PSD 바이너리 헤더 파싱 결과
  inspect_psd_header  : 원시 bytes에서 헤더 추출
  _categorize_psd_open_error : psd-tools 실패 원인 분류
  _parse_psd_tools_warnings  : psd-tools 로그 경고 수집
  _validate_embedded_composite : Pillow composite 엄격 검증
  _open_psd           : 전체 traceback 및 실패 위치 기록
"""
from __future__ import annotations

import hashlib
import io
import logging
import logging as _stdlib_logging
import re
import struct
import time
import traceback as _traceback
from dataclasses import dataclass

from PIL import Image, ImageStat

log = logging.getLogger("segmentation.psd_flatten")

_PSD_MAGIC = b"8BPS"

# psd-tools open 실패 카테고리 중 psd_embedded_composite 허용 대상
_EMBEDDED_COMPOSITE_ALLOWED_CATEGORIES = frozenset({
    "unsupported_internal_descriptor_version",
    "unsupported_tagged_block",
    "malformed_image_resource",
})


# ── 헤더 파싱 ─────────────────────────────────────────────────────────────────

@dataclass
class PsdHeaderInfo:
    """PSD 바이너리 헤더(첫 26 bytes) 파싱 결과."""
    signature:      str  = ""
    version:        int  = 0
    channels:       int  = 0
    width:          int  = 0
    height:         int  = 0
    depth:          int  = 0
    color_mode:     int  = 0
    valid:          bool = False
    failure_reason: str | None = None


def inspect_psd_header(source: "bytes | str") -> PsdHeaderInfo:
    """PSD 바이너리 헤더를 파싱하여 PsdHeaderInfo 반환.

    source: bytes (메모리) 또는 str (파일 경로).
    파일 경로 시 첫 26 bytes만 읽음.

    PSD 헤더 구조 (26 bytes):
      0-3   : signature "8BPS"
      4-5   : version (1=PSD, 2=PSB)
      6-11  : reserved (6 bytes, 항상 0)
      12-13 : channels (1-56)
      14-17 : height (px)
      18-21 : width (px)
      22-23 : depth (bits per channel)
      24-25 : color mode (3=RGB, 4=CMYK, …)
    """
    info = PsdHeaderInfo()
    try:
        if isinstance(source, str):
            with open(source, "rb") as f:
                raw = f.read(26)
        else:
            raw = bytes(source[:26])

        if len(raw) < 26:
            info.failure_reason = "file_too_short"
            return info

        info.signature  = raw[0:4].decode("latin-1", errors="replace")
        (info.version,) = struct.unpack(">H", raw[4:6])
        # bytes 6-11: reserved
        (info.channels,)   = struct.unpack(">H", raw[12:14])
        (info.height,)     = struct.unpack(">I", raw[14:18])
        (info.width,)      = struct.unpack(">I", raw[18:22])
        (info.depth,)      = struct.unpack(">H", raw[22:24])
        (info.color_mode,) = struct.unpack(">H", raw[24:26])

        if info.signature != "8BPS":
            info.failure_reason = "invalid_signature"
        elif info.version not in (1, 2):
            info.failure_reason = f"invalid_header_version:{info.version}"
        elif info.width <= 0 or info.height <= 0:
            info.failure_reason = "invalid_dimensions"
        else:
            info.valid = True

    except Exception as exc:
        info.failure_reason = f"parse_error:{type(exc).__name__}:{exc}"

    return info


# ── 로그 캡처 ─────────────────────────────────────────────────────────────────

class _LogCapture(_stdlib_logging.Handler):
    """psd-tools 로그 메시지 수집 핸들러."""
    def __init__(self) -> None:
        super().__init__(level=_stdlib_logging.DEBUG)
        self.messages: list[str] = []

    def emit(self, record: _stdlib_logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


# ── 진입점 ────────────────────────────────────────────────────────────────────

def flatten_input(
    image_bytes: bytes,
    original_filename: str = "input",
) -> tuple[Image.Image, str, dict]:
    """이미지 bytes -> (PIL RGB, flatten_method, flatten_meta).

    PSD 처리 흐름:
      1. inspect_psd_header() - raw bytes에서 헤더 검증
      2. psd-tools 설치/import 진단
      3. PSDImage.open() — 레이어 구조 파악 (traceback 포함)
      4. composite 전략 순차 시도 (default / no_icc / force / topil)
      5. composite 실패 시 psd_tools_merged (Pillow 병합 데이터)
      6. open 실패 + 헤더 유효 + 분류된 내부 오류 -> psd_embedded_composite
      7. 그 외 -> pillow_psd_fallback
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
                "sourceType":    "image",
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
    meta["sourceType"]     = "psd"
    meta["sourceMimeType"] = "image/vnd.adobe.photoshop"
    warnings_list: list[str] = []

    # Stage 18.4: 원시 헤더 검사 (psd-tools 미설치/미실행 상태에서도 동작)
    header_info = inspect_psd_header(image_bytes)
    meta.update({
        "psdHeaderSignature": header_info.signature,
        "psdHeaderVersion":   header_info.version,
        "psdHeaderValid":     header_info.valid,
        "psdHeaderChannels":  header_info.channels,
        "psdHeaderWidth":     header_info.width,
        "psdHeaderHeight":    header_info.height,
        "psdHeaderDepth":     header_info.depth,
        "psdHeaderColorMode": header_info.color_mode,
    })
    if header_info.failure_reason:
        meta["psdHeaderFailureReason"] = header_info.failure_reason
        warnings_list.append(f"psd_header_issue:{header_info.failure_reason}")

    _fill_psd_tools_meta(meta)

    psd_obj = None
    if meta.get("psdToolsImportSucceeded"):
        psd_obj = _open_psd(image_bytes, meta)

    if psd_obj is not None:
        result, strategy, strategy_errors = _try_composite_strategies(psd_obj)
        meta["compositeStrategyErrors"]      = strategy_errors
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

        # 모든 composite 전략 실패 -> psd_tools_merged
        meta["psdCompositeSucceeded"] = False
        warnings_list.append("composite_all_strategies_failed")
        log.warning(
            "psd-tools composite 실패(strategies=%s) -> psd_tools_merged",
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

    else:
        # psd-tools open 실패 (또는 import 실패)
        failure_cat = meta.get("psdOpenFailureCategory", "unknown_parser_error")
        import_ok   = meta.get("psdToolsImportSucceeded", False)

        # Stage 18.4: psd_embedded_composite 경로
        # 조건: 헤더 유효 + import 성공 + 분류된 내부 오류
        if (
            header_info.valid
            and import_ok
            and failure_cat in _EMBEDDED_COMPOSITE_ALLOWED_CATEGORIES
        ):
            log.info(
                "psd_embedded_composite 시도: header_valid=%s failure_cat=%s",
                header_info.valid, failure_cat,
            )
            embedded_img, embedded_val = _validate_embedded_composite(
                image_bytes, header_info
            )
            meta.update(embedded_val)

            if embedded_img is not None:
                meta.update({
                    "psdCompositeSucceeded":    False,
                    "flattenMethod":            "psd_embedded_composite",
                    "flattenCompatibilityMode": True,
                    "width":                    embedded_img.width,
                    "height":                   embedded_img.height,
                    "flattenMs":                int((time.time() - t0) * 1000),
                    "warnings":                 warnings_list + ["psd_tools_parser_compatibility_mode"],
                })
                log.info(
                    "PSD flatten: psd_embedded_composite (%dx%d validated)",
                    embedded_img.width, embedded_img.height,
                )
                return embedded_img, "psd_embedded_composite", meta

            warnings_list.append("embedded_composite_validation_failed")
            log.warning("embedded composite 검증 실패 -> pillow_psd_fallback")

        if not import_ok:
            log.warning(
                "psd-tools import 실패(installed=%s) -> pillow_psd_fallback",
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
    """PSDImage.open() 실행. 실패 시 None 반환.

    Stage 18.4: 전체 traceback + 실패 위치 + psd-tools 로그 경고 기록.
    """
    _capture = _LogCapture()
    _psd_logger = _stdlib_logging.getLogger("psd_tools")
    _psd_logger.addHandler(_capture)

    try:
        from psd_tools import PSDImage
        psd = PSDImage.open(io.BytesIO(image_bytes))
        meta["psdImageOpenSucceeded"] = True
        meta["psdWidth"]      = psd.width
        meta["psdHeight"]     = psd.height
        meta["psdColorMode"]  = str(psd.color_mode) if hasattr(psd, "color_mode") else "N/A"
        meta["psdLayerCount"] = _count_layers(psd)
        return psd

    except Exception as exc:
        tb_str = _traceback.format_exc()
        frames = _traceback.extract_tb(exc.__traceback__)
        failure_cat = _categorize_psd_open_error(exc)

        meta["psdImageOpenSucceeded"]  = False
        meta["psdOpenErrorType"]       = type(exc).__name__
        meta["psdOpenError"]           = str(exc)[:200]
        meta["psdOpenFailureCategory"] = failure_cat

        if frames:
            last = frames[-1]
            meta["psdOpenFailureModule"]   = last.filename
            meta["psdOpenFailureFunction"] = last.name
            meta["psdOpenFailureLine"]     = last.lineno

        # traceback은 artifact 생성용 (API 응답 body에 직접 노출 안 함)
        meta["psdOpenTraceback"] = tb_str[:3000]

        log.warning(
            "PSDImage.open 실패: (%s) %s\n%s",
            type(exc).__name__, exc, tb_str,
        )
        return None

    finally:
        _psd_logger.removeHandler(_capture)
        _parse_psd_tools_warnings(_capture.messages, meta)


def _parse_psd_tools_warnings(messages: list[str], meta: dict) -> None:
    """psd-tools 로그에서 unknown resource/block 경고를 파싱하여 meta에 기록."""
    unknown_resources: list[int] = []
    unsupported_keys: list[str] = []

    for msg in messages:
        if "Unknown image resource" in msg:
            # "Unknown image resource 1092"
            parts = msg.split()
            if parts and parts[-1].isdigit():
                rid = int(parts[-1])
                if rid not in unknown_resources:
                    unknown_resources.append(rid)
        elif "Unknown tagged block" in msg or "Unknown key" in msg:
            # "Unknown key: b'CAI '" / "Unknown tagged block: b'CAI '"
            m = re.search(r"b'([^']+)'", msg)
            if m:
                key = m.group(1).strip()
                if key and key not in unsupported_keys:
                    unsupported_keys.append(key)

    if unknown_resources or unsupported_keys:
        meta["unknownImageResources"]    = unknown_resources
        meta["unsupportedMetadataKeys"]  = unsupported_keys
        meta["parserCompatibilityWarnings"] = True


def _categorize_psd_open_error(exc: Exception) -> str:
    """psd-tools open 실패 원인을 분류.

    Returns one of:
      unsupported_internal_descriptor_version
      unsupported_tagged_block
      invalid_psd_header
      truncated_file
      composite_decode_error
      malformed_image_resource
      unknown_parser_error
    """
    msg       = str(exc)
    msg_lower = msg.lower()

    if isinstance(exc, AssertionError):
        if "version" in msg_lower:
            return "unsupported_internal_descriptor_version"
        if any(k in msg_lower for k in ("key", "block", "tag", "unsupported", "unknown")):
            return "unsupported_tagged_block"

    if "signature" in msg_lower and any(
        k in msg_lower for k in ("invalid", "wrong", "expected", "bad")
    ):
        return "invalid_psd_header"

    if any(k in msg_lower for k in ("truncated", "unexpected end", "eof", "end of file")):
        return "truncated_file"

    if "composite" in msg_lower or "decode" in msg_lower:
        return "composite_decode_error"

    if isinstance(exc, (struct.error, ValueError)):
        return "malformed_image_resource"

    return "unknown_parser_error"


def _validate_embedded_composite(
    image_bytes: bytes,
    header: PsdHeaderInfo,
) -> tuple:
    """Pillow로 PSD embedded composite를 읽고 엄격하게 검증.

    Returns: (PIL RGB Image | None, validation_dict)

    검증 조건 (모두 만족해야 embeddedCompositeValidated=True):
      - width/height가 PSD 헤더와 일치
      - 빈 이미지가 아님 (variance >= 1.0)
      - 단색이 아님 (stddev >= 0.5 in at least one channel)
      - PNG reopen 성공
    """
    val: dict = {
        "embeddedCompositeAvailable":  False,
        "embeddedCompositeValidated":  False,
        "pillowFormat":                None,
        "outputWidthMatchesHeader":    False,
        "outputHeightMatchesHeader":   False,
        "outputMode":                  None,
        "outputHasAlpha":              False,
        "outputBlankDetected":         True,
        "outputSingleColorDetected":   False,
        "outputReopenSucceeded":       False,
        "outputVariance":              0.0,
        "outputEntropy":               0.0,
    }

    try:
        pil_img = Image.open(io.BytesIO(image_bytes))
        val["embeddedCompositeAvailable"] = True
        val["pillowFormat"]   = pil_img.format
        val["outputMode"]     = pil_img.mode
        val["outputHasAlpha"] = pil_img.mode in ("RGBA", "LA", "PA")

        pil_rgb = pil_img.convert("RGB")

        # Size match with PSD header
        val["outputWidthMatchesHeader"]  = (pil_rgb.width  == header.width)
        val["outputHeightMatchesHeader"] = (pil_rgb.height == header.height)

        # Variance / blank check (PIL ImageStat — no numpy needed)
        stat    = ImageStat.Stat(pil_rgb)
        variance = sum(stat.var) / max(len(stat.var), 1)
        val["outputVariance"]    = round(variance, 2)
        val["outputBlankDetected"] = variance < 1.0

        # Single-color check
        std_devs = stat.stddev
        val["outputSingleColorDetected"] = all(s < 0.5 for s in std_devs)

        # Entropy
        try:
            val["outputEntropy"] = round(pil_rgb.convert("L").entropy(), 4)
        except Exception:
            val["outputEntropy"] = 0.0

        # Reopen check (PNG round-trip)
        buf = io.BytesIO()
        pil_rgb.save(buf, format="PNG")
        buf.seek(0)
        reopened = Image.open(buf)
        reopened.load()
        val["outputReopenSucceeded"] = True

        # SHA256 of the PNG
        try:
            buf2 = io.BytesIO()
            pil_rgb.save(buf2, format="PNG")
            val["flattenedPngSha256"] = hashlib.sha256(buf2.getvalue()).hexdigest()
        except Exception:
            pass

        # All PASS conditions
        all_pass = (
            val["outputWidthMatchesHeader"]
            and val["outputHeightMatchesHeader"]
            and not val["outputBlankDetected"]
            and not val["outputSingleColorDetected"]
            and val["outputReopenSucceeded"]
        )
        val["embeddedCompositeValidated"] = all_pass

        if all_pass:
            return pil_rgb, val

        log.warning(
            "embedded composite 검증 실패: "
            "width_ok=%s height_ok=%s blank=%s single_color=%s reopen=%s",
            val["outputWidthMatchesHeader"],
            val["outputHeightMatchesHeader"],
            val["outputBlankDetected"],
            val["outputSingleColorDetected"],
            val["outputReopenSucceeded"],
        )
        return None, val

    except Exception as exc:
        val["embeddedCompositeError"] = str(exc)[:200]
        log.warning("embedded composite open 실패: %s", exc)
        return None, val


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
                log.info(
                    "composite 성공: strategy=%s size=%s mode=%s",
                    name, result.size, result.mode,
                )
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
