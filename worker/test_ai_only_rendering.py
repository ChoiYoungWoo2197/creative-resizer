"""Stage 20.3 AI-Only Rendering 단위 테스트.

검증 항목:
  T1: resize_mode='ai-auto' → _generate_ai_only() 라우팅
  T2: AI_ONLY_RENDERING=true env var → _generate_ai_only() 라우팅
  T3: Fail-closed — provider None → RuntimeError
  T4: renderProvenance 필드 정확성 (renderPolicy / effectiveRenderer / etc.)
  T5: 결과 이미지 크기가 spec과 일치
  T6: 복수 spec 처리 (7 입력 유형 × 3 규격)

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

print("\n=== [T3] Fail-closed: provider=None → RuntimeError ===")

tmp_img3 = make_tmp_png()
tmp_dir3 = tempfile.mkdtemp()

try:
    try:
        _generate_ai_only(
            psd_path=tmp_img3,
            specs=[SPECS[0]],
            resize_mode="ai-auto",
            output_format="png",
            output_dir=tmp_dir3,
            job_id="t3",
            _provider_override=None,   # override=None → factory 호출 → API key 없으면 None
        )
        check("provider=None → RuntimeError 발생", False)
    except RuntimeError as e:
        check(f"provider=None → RuntimeError (fail-closed): {str(e)[:60]}", True)
    except Exception as e:
        check(f"provider=None → RuntimeError (got {type(e).__name__})", isinstance(e, RuntimeError))
finally:
    shutil.rmtree(tmp_dir3, ignore_errors=True)

# ─── T4: renderProvenance 필드 정확성 ────────────────────────────────────────

print("\n=== [T4] renderProvenance 필드 정확성 ===")

tmp_img4 = make_tmp_png(1000, 1000)
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

# ─── 결과 ─────────────────────────────────────────────────────────────────────

total = PASS + FAIL
print(f"\n{'='*60}")
print(f"RESULT: {PASS}/{total} PASS  ({FAIL} FAIL)")
print(f"{'='*60}")
sys.exit(0 if FAIL == 0 else 1)
