"""Worker timeout policy 및 중복 실행 방지 테스트.

검증 항목:
  T1: jobId 중복 추적 — active set에 추가/제거 로직
  T2: finally에서 active set 정리 (에러 시에도)
  T3: 서로 다른 jobId는 동시에 active 가능
  T4: 동일 jobId active 시 중복 감지
  T5: [AI_ONLY_START] 구조화 로그 출력 검증
  T6: [AI_SPEC_START/END] 로그 출력 검증
  T7: [AI_ONLY_END] 로그 + actualProviderRequestCount 검증
  T8: [AI_PROVIDER_START/END] 로그 출력 검증 (source_faithful_repair.py)

실행:
  cd worker
  python test_timeout_policy.py
"""

import sys
import os
import io
import threading
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image

PASS = 0
FAIL = 0


def check(label: str, condition: bool):
    global PASS, FAIL
    if condition:
        PASS += 1
    else:
        FAIL += 1
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}")


# ── Active Jobs 로직 직접 테스트 (Flask 없이) ─────────────────────────────────
# app.py의 _active_jobs 중복 방지 로직을 동일하게 구현하여 검증한다.

_active_jobs: set = set()
_active_jobs_lock = threading.Lock()


def simulate_generate_enter(job_id: str) -> bool:
    """app.py /generate 중복 체크 로직 동일 구현. True=진입 허용, False=중복 차단."""
    with _active_jobs_lock:
        if job_id in _active_jobs:
            return False
        _active_jobs.add(job_id)
        return True


def simulate_generate_exit(job_id: str):
    """app.py /generate finally 정리 로직."""
    with _active_jobs_lock:
        _active_jobs.discard(job_id)


def reset_active_jobs():
    with _active_jobs_lock:
        _active_jobs.clear()


# ── T1: 중복 jobId 차단 ───────────────────────────────────────────────────────

print("\n=== [T1] 중복 jobId 차단 ===")

reset_active_jobs()
allowed_first = simulate_generate_enter("job-dup-001")
check("첫 번째 요청 허용", allowed_first is True)
check("active_jobs에 추가됨", "job-dup-001" in _active_jobs)

allowed_dup = simulate_generate_enter("job-dup-001")
check("중복 요청 차단 (False)", allowed_dup is False)

simulate_generate_exit("job-dup-001")
check("완료 후 active_jobs에서 제거됨", "job-dup-001" not in _active_jobs)

reset_active_jobs()

# ── T2: 에러 시에도 finally에서 정리 ─────────────────────────────────────────

print("\n=== [T2] 에러 시에도 finally 정리 ===")

reset_active_jobs()


def simulate_with_error(job_id: str):
    if not simulate_generate_enter(job_id):
        return "duplicate"
    try:
        raise ValueError("simulated AI error")
    except Exception:
        pass
    finally:
        simulate_generate_exit(job_id)
    return "ok"


simulate_with_error("job-error-001")
check("에러 후 active_jobs 정리됨", "job-error-001" not in _active_jobs)

# 동일 jobId 재요청 가능
allowed_retry = simulate_generate_enter("job-error-001")
check("에러 후 동일 jobId 재요청 허용", allowed_retry is True)
simulate_generate_exit("job-error-001")

reset_active_jobs()

# ── T3: 서로 다른 jobId 동시 처리 가능 ───────────────────────────────────────

print("\n=== [T3] 서로 다른 jobId 동시 처리 ===")

reset_active_jobs()
simulate_generate_enter("job-A")
simulate_generate_enter("job-B")

allowed_C = simulate_generate_enter("job-C")
check("job-C 진입 허용", allowed_C is True)
check("job-A 여전히 active", "job-A" in _active_jobs)
check("job-B 여전히 active", "job-B" in _active_jobs)
check("job-C active", "job-C" in _active_jobs)

simulate_generate_exit("job-A")
simulate_generate_exit("job-B")
simulate_generate_exit("job-C")
check("모두 정리됨", len(_active_jobs) == 0)

reset_active_jobs()

# ── T4: 동일 jobId active 시 중복 감지 ──────────────────────────────────────

print("\n=== [T4] 동일 jobId 중복 감지 ===")

reset_active_jobs()
results = []


def try_enter(jid, out):
    out.append(simulate_generate_enter(jid))


threads = [threading.Thread(target=try_enter, args=("job-concurrent", results)) for _ in range(5)]
for t in threads:
    t.start()
for t in threads:
    t.join()

allowed_count = sum(1 for r in results if r)
check("5개 동시 요청 중 1개만 허용", allowed_count == 1)
simulate_generate_exit("job-concurrent")

reset_active_jobs()

# ── T5~T8: 구조화 로그 출력 검증 ────────────────────────────────────────────

print("\n=== [T5-T8] 구조화 로그 출력 검증 ===")


class FakeProvider:
    def metadata(self):
        return {"providerName": "fake", "modelName": "fake-1"}

    def inpaint(self, image, mask, prompt, options):
        import numpy as np
        w, h = image.size
        arr = np.random.randint(30, 200, (h, w, 3), dtype=np.uint8)
        return Image.fromarray(arr, "RGB")


from resizer import _generate_ai_only

SPECS = [
    {"media": "naver", "name": "wide", "slug": "wd", "width": 800, "height": 400},
]


def make_tmp_png(w=800, h=600):
    img = Image.new("RGB", (w, h), color=(180, 120, 80))
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    img.save(path)
    return path


tmp_img = make_tmp_png()
tmp_dir = tempfile.mkdtemp()

captured = io.StringIO()
old_stdout = sys.stdout
sys.stdout = captured

try:
    results, _ = _generate_ai_only(
        psd_path=tmp_img,
        specs=SPECS,
        resize_mode="ai-auto",
        output_format="png",
        output_dir=tmp_dir,
        source_type="image",
        job_id="log-test-001",
        _provider_override=FakeProvider(),
    )
finally:
    sys.stdout = old_stdout
    shutil.rmtree(tmp_dir, ignore_errors=True)
    if os.path.exists(tmp_img):
        os.unlink(tmp_img)

log_output = captured.getvalue()

# T5: AI_ONLY_START
check("T5: [AI_ONLY_START] 로그 출력", "[AI_ONLY_START]" in log_output)
check("T5: jobId=log-test-001 포함", "jobId=log-test-001" in log_output)
check("T5: specCount=1 포함", "specCount=1" in log_output)
check("T5: resizeMode=ai-auto 포함", "resizeMode=ai-auto" in log_output)

# T6: AI_SPEC_START / AI_SPEC_END
check("T6: [AI_SPEC_START] 로그 출력", "[AI_SPEC_START]" in log_output)
check("T6: [AI_SPEC_END] 로그 출력", "[AI_SPEC_END]" in log_output)
check("T6: 800x400 size 포함", "800x400" in log_output)
check("T6: verdict= 포함", "verdict=" in log_output)
check("T6: elapsedMs= 포함", "elapsedMs=" in log_output)

# T7: AI_ONLY_END
check("T7: [AI_ONLY_END] 로그 출력", "[AI_ONLY_END]" in log_output)
check("T7: successCount=1 포함", "successCount=1" in log_output)
check("T7: actualProviderRequestCount 포함", "actualProviderRequestCount" in log_output)

# T8: AI_PROVIDER_START / AI_PROVIDER_END (source_faithful_repair.py)
check("T8: [AI_PROVIDER_START] 로그 출력", "[AI_PROVIDER_START]" in log_output)
check("T8: [AI_PROVIDER_END] 로그 출력", "[AI_PROVIDER_END]" in log_output)

# ── 결과 ─────────────────────────────────────────────────────────────────────

total = PASS + FAIL
print(f"\n{'='*60}")
print(f"RESULT: {PASS}/{total} PASS  ({FAIL} FAIL)")
print(f"{'='*60}")
sys.exit(0 if FAIL == 0 else 1)
