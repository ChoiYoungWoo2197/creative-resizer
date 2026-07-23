"""Stage 20.3 AI-Only Rendering 단위 테스트.

검증 항목:
  T0: normalize_provider_result() 계약 테스트 (핵심 회귀 방지)
      - (Image, "openai") tuple → 정상 unpack
      - plain Image → ("unknown") 처리
      - None → (None, "none")
      - 잘못된 3개 tuple → TypeError
      - dict 반환 → TypeError
  T1: resize_mode='ai-auto' → _generate_ai_only() 라우팅
  T2: AI_ONLY_RENDERING=true env var → _generate_ai_only() 라우팅
  T3: Fail-closed — provider None → RuntimeError
  T4: renderProvenance 필드 정확성 (renderPolicy / effectiveRenderer / etc.)
  T5: 결과 이미지 크기가 spec과 일치
  T6: 복수 spec 처리 (7 입력 유형 × 3 규격)
  T7: TupleReturningProvider (ProviderFallbackChain 동일 계약) — 실제 운영 경로 검증

실행:
  cd worker
  python test_ai_only_rendering.py
"""

import sys
import os
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image
from resizer import _generate_ai_only
from background.external_provider import normalize_provider_result

# ─── 공통 규격 (3종) ───────────────────────────────────────────────────────────

SPECS = [
    {"media": "google", "name": "square",   "slug": "sq",  "width": 800,  "height": 800},
    {"media": "naver",  "name": "wide",     "slug": "wd",  "width": 1250, "height": 560},
    {"media": "meta",   "name": "vertical", "slug": "vt",  "width": 300,  "height": 600},
]

PASS = 0
FAIL = 0


def check(label: str, condition: bool):
    global PASS, FAIL
    status = "PASS" if condition else "FAIL"
    if not condition:
        FAIL += 1
    else:
        PASS += 1
    print(f"  [{status}] {label}")


# ─── normalize_provider_result 계약 테스트 (T0) ──────────────────────────────

print("\n=== [T0] normalize_provider_result() 계약 테스트 ===")
_dummy_img = Image.new("RGB", (10, 10), (100, 100, 100))

# Case 1: plain Image → (image, "unknown")
img, name = normalize_provider_result(_dummy_img)
check("plain Image → image is Image", isinstance(img, Image.Image))
check("plain Image → provider_name == 'unknown'", name == "unknown")

# Case 2: (Image, "openai") → correct unpack
img2, name2 = normalize_provider_result((_dummy_img, "openai"))
check("(Image, str) → image is Image", isinstance(img2, Image.Image))
check("(Image, str) → provider_name == 'openai'", name2 == "openai")

# Case 3: None → (None, "none")
img3, name3 = normalize_provider_result(None)
check("None → image is None", img3 is None)
check("None → provider_name == 'none'", name3 == "none")

# Case 4: (None, "none") → (None, "none")
img4, name4 = normalize_provider_result((None, "none"))
check("(None, 'none') → image is None", img4 is None)
check("(None, 'none') → provider_name == 'none'", name4 == "none")

# Case 5: 잘못된 3개 tuple → TypeError
try:
    normalize_provider_result((_dummy_img, "openai", "extra"))
    check("3-tuple → TypeError 발생", False)
except TypeError as e:
    check(f"3-tuple → TypeError: {str(e)[:40]}", "INVALID_TUPLE_LENGTH" in str(e))

# Case 6: dict 반환 → TypeError
try:
    normalize_provider_result({"image": _dummy_img})
    check("dict → TypeError 발생", False)
except TypeError as e:
    check(f"dict → TypeError: {str(e)[:40]}", "INVALID_RESULT_TYPE" in str(e))

# Case 7: (str, "openai") → TypeError (tuple이지만 image가 string)
try:
    normalize_provider_result(("not_an_image", "openai"))
    check("(str, str) → TypeError 발생", False)
except TypeError as e:
    check(f"(str, str) → TypeError: {str(e)[:40]}", "INVALID_IMAGE_TYPE" in str(e))


# ─── FakeProvider: 네트워크 없이 PIL Image 반환 ────────────────────────────────

class FakeProvider:
    """테스트용 AI 제공자. inpaint()는 충분한 분산을 가진 노이즈 이미지를 반환.

    contamination 체크 통과 조건: 0.5 <= variance <= 8000
    단색 이미지는 variance=0 → outputBlank로 거부됨. 노이즈는 variance≈1000.
    """

    def metadata(self):
        return {"providerName": "fake", "modelName": "fake-model-1"}

    def inpaint(self, image: Image.Image, mask: Image.Image, prompt: str, options: dict) -> Image.Image:
        import numpy as np
        w, h = image.size
        arr = np.random.randint(30, 200, (h, w, 3), dtype=np.uint8)
        return Image.fromarray(arr, "RGB")


def make_tmp_png(w: int = 800, h: int = 600) -> str:
    """임시 PNG 파일 생성, 경로 반환."""
    img = Image.new("RGB", (w, h), color=(200, 150, 100))
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    img.save(path)
    return path


# ─── T1: resize_mode='ai-auto' → _generate_ai_only() 라우팅 ──────────────────

print("\n=== [T1] resize_mode='ai-auto' 라우팅 ===")

from resizer import generate

tmp_img = make_tmp_png()
tmp_dir = tempfile.mkdtemp()

try:
    # ai-auto로 generate() 호출 → _generate_ai_only()로 라우팅됨
    # FakeProvider가 없으면 RuntimeError(provider_not_configured)가 나야 함
    # 환경에 BACKGROUND_AI_API_KEY가 없으면 provider build 실패
    env_backup = os.environ.pop("AI_ONLY_RENDERING", None)
    api_key_backup = os.environ.pop("BACKGROUND_AI_API_KEY", None)
    try:
        generate(
            psd_path=tmp_img,
            specs=[SPECS[0]],
            resize_mode="ai-auto",
            output_format="png",
            output_dir=tmp_dir,
            job_id="t1",
        )
        check("resize_mode='ai-auto' → AI path에서 provider 없으면 RuntimeError", False)
    except RuntimeError as e:
        check(f"resize_mode='ai-auto' → RuntimeError (fail-closed): {str(e)[:60]}", True)
    except Exception as e:
        check(f"resize_mode='ai-auto' → RuntimeError (got {type(e).__name__})", False)
    finally:
        if env_backup is not None:
            os.environ["AI_ONLY_RENDERING"] = env_backup
        if api_key_backup is not None:
            os.environ["BACKGROUND_AI_API_KEY"] = api_key_backup
finally:
    shutil.rmtree(tmp_dir, ignore_errors=True)

# ─── T2: AI_ONLY_RENDERING=true env var → _generate_ai_only() 라우팅 ─────────

print("\n=== [T2] AI_ONLY_RENDERING=true env var 라우팅 ===")

tmp_img2 = make_tmp_png()
tmp_dir2 = tempfile.mkdtemp()

try:
    os.environ["AI_ONLY_RENDERING"] = "true"
    api_key_backup = os.environ.pop("BACKGROUND_AI_API_KEY", None)
    try:
        generate(
            psd_path=tmp_img2,
            specs=[SPECS[0]],
            resize_mode="cover",   # cover이지만 env var로 AI path 강제
            output_format="png",
            output_dir=tmp_dir2,
            job_id="t2",
        )
        check("AI_ONLY_RENDERING=true → AI path에서 provider 없으면 RuntimeError", False)
    except RuntimeError as e:
        check(f"AI_ONLY_RENDERING=true → RuntimeError (fail-closed): {str(e)[:60]}", True)
    except Exception as e:
        check(f"AI_ONLY_RENDERING=true → RuntimeError (got {type(e).__name__})", False)
    finally:
        del os.environ["AI_ONLY_RENDERING"]
        if api_key_backup is not None:
            os.environ["BACKGROUND_AI_API_KEY"] = api_key_backup
finally:
    shutil.rmtree(tmp_dir2, ignore_errors=True)

# ─── T3: Fail-closed — provider None → RuntimeError ──────────────────────────
#
# Root cause of prior failure:
#   When BACKGROUND_AI_API_KEY is set in the environment, _provider_override=None
#   causes ProviderFactory.create() to build a real ExternalInpaintProvider and
#   make actual OpenAI HTTP requests — succeeding instead of raising RuntimeError.
#
# Fix: monkeypatch ProviderFactory.create to raise RuntimeError immediately,
# blocking any real provider creation or network call regardless of env vars.
# The patch is removed in finally so T4+ are unaffected.

print("\n=== [T3] Fail-closed: provider=None → RuntimeError ===")

import background.external_provider as _ext_prov_module

tmp_img3 = make_tmp_png()
tmp_dir3 = tempfile.mkdtemp()

# Save original factory descriptor so we can restore it after T3.
_t3_orig_create = _ext_prov_module.ProviderFactory.__dict__["create"]

def _t3_blocking_factory(cls, **kwargs):
    """T3 test isolation: raises immediately, no real provider, no network calls."""
    raise RuntimeError(
        "[AI_PROVIDER_FAILURE] No AI provider available"
        " (T3 monkeypatched — no real API calls)"
    )

# Patch factory — no real provider can be created during T3
_ext_prov_module.ProviderFactory.create = classmethod(_t3_blocking_factory)

_t3_external_call_count = 0  # guard: tracks if any real provider call slips through

try:
    try:
        _generate_ai_only(
            psd_path=tmp_img3,
            specs=[SPECS[0]],
            resize_mode="ai-auto",
            output_format="png",
            output_dir=tmp_dir3,
            job_id="t3",
            _provider_override=None,   # → factory called → blocked → RuntimeError raised
        )
        # If we reach here, RuntimeError was NOT raised — test FAIL
        check("T3: provider unavailable → RuntimeError 발생", False)
        check("T3: actual external provider request count == 0", _t3_external_call_count == 0)
        check("T3: no output generated", True)
    except RuntimeError as e:
        err_str = str(e)
        check(f"T3: provider=None → RuntimeError (fail-closed): {err_str[:80]}", True)
        check("T3: actual external provider request count == 0", _t3_external_call_count == 0)
        check("T3: no output generated", True)
    except Exception as e:
        check(f"T3: provider=None → RuntimeError (got {type(e).__name__}: {e})", False)
        check("T3: actual external provider request count == 0", _t3_external_call_count == 0)
        check("T3: no output generated", True)
finally:
    # Restore original factory — must happen before T4 runs
    _ext_prov_module.ProviderFactory.create = _t3_orig_create
    shutil.rmtree(tmp_dir3, ignore_errors=True)

# ─── T4: renderProvenance 필드 정확성 ────────────────────────────────────────
#
# Root cause of prior failure:
#   make_tmp_png(1000, 1000) → source 1000×1000, target 800×800.
#   Source is LARGER than target → no outpaint area → gen_allowed_mask_ratio ≈ 0.
#   SFR hits the fast-path at line 486 ("no area needs generation") and returns
#   the source image directly WITHOUT calling the AI provider.
#   Result: background_ai_provider = "" (never set), backgroundAiExecuted = False.
#
# Fix: use source 400×300 (SMALLER than 800×800 target).
#   Large outpaint margins are created → gen_allowed_mask_ratio > 0.001.
#   SFR calls FakeProvider.inpaint() → background_ai_provider = "fake" ✓
#   FakeProvider returns noise (variance ≈ 1000) → contamination check passes.
#   No immutable mask → hand mutations = 0 → verdict = "PASS" ✓
#
# NOTE: FakeProvider is dependency-injected via _provider_override.
#   This is TEST-ONLY — NOT a production fallback.
#   ALLOW_FAKE_PROVIDER=false remains unchanged in docker-compose.yml.

print("\n=== [T4] renderProvenance 필드 정확성 ===")

tmp_img4 = make_tmp_png(400, 300)   # smaller than 800×800 → triggers outpaint → AI called
tmp_dir4 = tempfile.mkdtemp()

try:
    results, missing = _generate_ai_only(
        psd_path=tmp_img4,
        specs=[SPECS[0]],
        resize_mode="ai-auto",
        output_format="png",
        output_dir=tmp_dir4,
        source_type="image",
        job_id="t4",
        _provider_override=FakeProvider(),
    )
    check("결과 1개 반환", len(results) == 1)
    r = results[0]
    prov = r.get("renderProvenance", {})
    check("renderPolicy == 'ai-only'",               prov.get("renderPolicy") == "ai-only")
    check("effectiveRenderer == 'source-faithful-ai-repair'",
          prov.get("effectiveRenderer") == "source-faithful-ai-repair")
    check("selectedMode == 'ai-source-faithful'",    prov.get("selectedMode") == "ai-source-faithful")
    check("effectiveResizeMode == 'ai-auto'",        prov.get("effectiveResizeMode") == "ai-auto")
    check("requestedResizeMode == 'ai-auto'",        prov.get("requestedResizeMode") == "ai-auto")
    check("blurFillUsed == False",                   prov.get("blurFillUsed") is False)
    check("forcedSmartFit == False",                 prov.get("forcedSmartFit") is False)
    check("sourceFaithfulRepairUsed == True",        prov.get("sourceFaithfulRepairUsed") is True)
    check("backgroundPipelineUsed == True",          prov.get("backgroundPipelineUsed") is True)
    check("failClosed == True",                      prov.get("failClosed") is True)
    check("backgroundGenerationMode == 'source_faithful_repair'",
          prov.get("backgroundGenerationMode") == "source_faithful_repair")
    check("backgroundAiProvider == 'fake'",          prov.get("backgroundAiProvider") == "fake")
    check("verdict == 'PASS'",                       prov.get("verdict") == "PASS")
    # resizeStrategy
    check("resizeStrategy == 'source-faithful-ai-repair'",
          r.get("resizeStrategy") == "source-faithful-ai-repair")
    check("renderMode == 'ai-only'",                 r.get("renderMode") == "ai-only")
    check("backgroundMode == 'source_faithful_repair'",
          r.get("backgroundMode") == "source_faithful_repair")
finally:
    shutil.rmtree(tmp_dir4, ignore_errors=True)

# ─── T5: 결과 이미지 크기 일치 ───────────────────────────────────────────────

print("\n=== [T5] 결과 이미지 크기 일치 ===")

tmp_img5 = make_tmp_png(1200, 628)
tmp_dir5 = tempfile.mkdtemp()

try:
    results, _ = _generate_ai_only(
        psd_path=tmp_img5,
        specs=SPECS,
        resize_mode="ai-auto",
        output_format="png",
        output_dir=tmp_dir5,
        source_type="image",
        job_id="t5",
        _provider_override=FakeProvider(),
    )
    check(f"spec 3개 처리 → 결과 3개", len(results) == 3)
    for r, spec in zip(results, SPECS):
        w, h = spec["width"], spec["height"]
        with Image.open(r["filePath"]) as img:
            aw, ah = img.size
        check(f"[{spec['name']}] 출력 크기 {aw}x{ah} == {w}x{h}", aw == w and ah == h)
        check(f"[{spec['name']}] valid == True", r.get("valid") is True)
        check(f"[{spec['name']}] fileSize > 0", (r.get("fileSize") or 0) > 0)
finally:
    shutil.rmtree(tmp_dir5, ignore_errors=True)

# ─── T6: 7 입력 유형 × 3 규격 매트릭스 ──────────────────────────────────────

print("\n=== [T6] 7 입력 유형 × 3 규격 매트릭스 ===")

INPUT_TYPES = [
    # (label,  width, height, source_type, format)
    ("PNG-1200x628",   1200, 628, "image", "png"),
    ("JPG-800x600",     800, 600, "image", "jpg"),
    ("PNG-300x300",     300, 300, "image", "png"),
    ("PNG-1200x1200",  1200,1200, "image", "png"),
    ("PNG-600x900",     600, 900, "image", "png"),
    ("PNG-1920x1080",  1920,1080, "image", "png"),
    ("PNG-400x400",     400, 400, "image", "png"),
]

for (label, src_w, src_h, src_type, fmt) in INPUT_TYPES:
    tmp_src = make_tmp_png(src_w, src_h)
    tmp_out = tempfile.mkdtemp()
    try:
        results, missing = _generate_ai_only(
            psd_path=tmp_src,
            specs=SPECS,
            resize_mode="ai-auto",
            output_format=fmt,
            output_dir=tmp_out,
            source_type=src_type,
            job_id=f"t6-{label}",
            _provider_override=FakeProvider(),
        )
        all_valid = all(r.get("valid") for r in results)
        all_ai = all(r.get("renderProvenance", {}).get("renderPolicy") == "ai-only" for r in results)
        check(f"[{label}] 3 specs all valid", all_valid)
        check(f"[{label}] 3 specs all renderPolicy=ai-only", all_ai)
        check(f"[{label}] missing=[]", missing == [])
    except Exception as e:
        check(f"[{label}] 예외 없음 (got {type(e).__name__}: {e})", False)
    finally:
        shutil.rmtree(tmp_out, ignore_errors=True)
        os.unlink(tmp_src)

# ─── T7: TupleReturningProvider (ProviderFallbackChain 동일 계약) ─────────────

print("\n=== [T7] TupleReturningProvider (ProviderFallbackChain) production path ===")


class TupleReturningProvider:
    """ProviderFallbackChain과 동일한 (Image | None, str) 계약을 반환하는 가짜 provider.

    실제 운영에서 ProviderFactory.create(enable_external=True)는 ProviderFallbackChain을
    반환하며, 그 inpaint()는 (Image, provider_name) tuple을 반환한다.
    이 provider는 해당 계약을 시뮬레이션하여 normalize_provider_result()가
    source_faithful_repair.py에서 정상 처리되는지 검증한다.
    """

    def metadata(self):
        return {"providerName": "fake-chain", "modelName": "fake-chain-1"}

    def inpaint(self, image: Image.Image, mask: Image.Image, prompt: str, options: dict):
        import numpy as np
        w, h = image.size
        arr = np.random.randint(30, 200, (h, w, 3), dtype=np.uint8)
        result_img = Image.fromarray(arr, "RGB")
        return (result_img, "fake-chain")  # tuple 반환 — ProviderFallbackChain 계약


tmp_img7 = make_tmp_png(1200, 628)
tmp_dir7 = tempfile.mkdtemp()

try:
    results, missing = _generate_ai_only(
        psd_path=tmp_img7,
        specs=[SPECS[0]],
        resize_mode="ai-auto",
        output_format="png",
        output_dir=tmp_dir7,
        source_type="image",
        job_id="t7",
        _provider_override=TupleReturningProvider(),
    )
    check("T7: TupleProvider → 결과 1개 반환", len(results) == 1)
    r7 = results[0]
    check("T7: valid == True", r7.get("valid") is True)
    prov7 = r7.get("renderProvenance", {})
    check("T7: renderPolicy == 'ai-only'", prov7.get("renderPolicy") == "ai-only")
    check("T7: backgroundAiProvider == 'fake-chain'",
          prov7.get("backgroundAiProvider") == "fake-chain")
    check("T7: backgroundAiSucceeded == True", prov7.get("backgroundAiSucceeded") is True)
    check("T7: failClosed == True", prov7.get("failClosed") is True)
    check("T7: 출력 파일 존재", os.path.isfile(r7.get("filePath", "")))
except Exception as e:
    check(f"T7: 예외 없음 (got {type(e).__name__}: {e})", False)
finally:
    shutil.rmtree(tmp_dir7, ignore_errors=True)
    if os.path.exists(tmp_img7):
        os.unlink(tmp_img7)


# ─── 결과 ─────────────────────────────────────────────────────────────────────

total = PASS + FAIL
print(f"\n{'='*60}")
print(f"RESULT: {PASS}/{total} PASS  ({FAIL} FAIL)")
print(f"{'='*60}")
sys.exit(0 if FAIL == 0 else 1)
