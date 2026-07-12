# BannerSpec Stage 8 Checkpoint

- 작성 시각: 2026-07-12 (5차 세션 — 서버 실행 오류 수정 완료)
- 현재 브랜치: master
- 작업 상태: **FIXES APPLIED — 서버 재실행 대기**

---

## 1. 현재 목표

운영서버 Docker 환경에서 Stage 8 전체 Smoke Test 통과.

---

## 2. 서버 실행 결과 (1차 — FAIL)

**실행 일시**: 2026-07-12  
**실패 단계**: Step 7 (Docker 이미지 빌드)

| 항목 | 결과 |
|---|---|
| artifact 디렉터리 생성 | OK (1da5e57 수정 효과) |
| git pull | OK |
| 운영 컨테이너 4개 정상 실행 중 | OK |
| 사용 가능 메모리 | 56 GB |
| 디스크 사용률 | 78% |
| 포트 18082 충돌 | 없음 |
| stale smoke 컨테이너 | 없음 |
| Step 7 Worker Docker 빌드 | **FAIL** |
| Step 8 이후 | 미실행 |
| 최종 스크립트 출력 | `ALL PASS` (false-positive 버그) |

### 실패 원인 1: Worker build context 오류

```
load build context
transferring context: 2B

COPY worker/requirements.txt .
ERROR: "/worker/requirements.txt" not found

COPY worker/ .
ERROR: "/worker" not found
```

**원인**: `.dockerignore`가 `worker/`를 명시적으로 제외 (`# Java 빌드에 불필요한 디렉토리 제외`).
`context: .` 사용 시 `worker/` 디렉토리가 build context에서 제외되어 2B만 전송됨.

**수정**:
- `docker-compose.smoke.yml`: worker 서비스 `context: .` → `context: ./worker`, `dockerfile: ../Dockerfile.worker.smoke`
- `Dockerfile.worker.smoke`: `COPY worker/requirements.txt .` → `COPY requirements.txt .`, `COPY worker/ .` → `COPY . .`
- `.dockerignore` 수정 불필요 — context를 `./worker`로 변경하면 `./worker/.dockerignore`를 탐색하므로 project root `.dockerignore`의 `worker/` 제외가 worker 빌드에 영향 없음

### 실패 원인 2: `cleanup()` false PASS 버그

**원인**: `cleanup()`이 `local code=${SMOKE_RUNNER_EXIT}` (초기값=0)을 참조.
`set -e`로 Docker 빌드 실패 시 EXIT trap이 실제 `$?`(=비정상 코드)를 전달하지만,
`SMOKE_RUNNER_EXIT`는 0으로 초기화된 채 업데이트 없이 ALL PASS 출력.

**수정**:
- `set -euo pipefail` → `set -Eeuo pipefail`
- `section()`: `CURRENT_STEP` 자동 추적 추가
- `cleanup()`: `local exit_code=$?` + `set +e` + `FINAL_RESULT` 조건부 ALL PASS 출력
- `SMOKE_RUNNER_EXIT=0` 외 `FINAL_RESULT="UNKNOWN"`, `CURRENT_STEP="(init)"` 추가
- Step 7: `if ! ${COMPOSE_CMD} build --no-cache 2>&1 | tee -a "${SMOKE_LOG}"; then exit 1; fi`
- 마지막(Step 25c): `SMOKE_RUNNER_EXIT=0` 일 때만 `FINAL_RESULT="PASS"` 설정

---

## 3. 수정된 파일

| 파일 | 수정 내용 |
|---|---|
| `docker-compose.smoke.yml` | worker build context `./worker`, dockerfile `../Dockerfile.worker.smoke` |
| `Dockerfile.worker.smoke` | COPY 경로 context 기준으로 변경 (`requirements.txt`, `. .`) |
| `scripts/run-stage8-smoke-server.sh` | `set -Eeuo pipefail`, `FINAL_RESULT`/`CURRENT_STEP` 추적, cleanup 버그 수정, Step 7 `if !` 패턴, Step 25c |

---

## 4. 최소 검증 결과

| 검증 항목 | 결과 |
|---|---|
| `bash -n scripts/run-stage8-smoke-server.sh` | **PASS** |
| `bash -n scripts/run_all_smoke.sh` | **PASS** |
| `python -m py_compile scripts/http_smoke_test.py` | **PASS** |
| `python -m py_compile scripts/worker_contract_smoke_test.py` | **PASS** |
| `docker compose config` | Docker 없음 — 목시 파일 검증 완료 |
| 운영 컨테이너 미영향 | 수정 파일 없음 (docker-compose.smoke.yml, Dockerfile.worker.smoke, 스크립트만) |

---

## 5. 종료 코드 검증 시나리오

| 시나리오 | exit_code | FINAL_RESULT | SMOKE_RUNNER_EXIT | 출력 |
|---|---|---|---|---|
| Docker build 실패 | non-zero | UNKNOWN | 0 | FAIL ✓ |
| smoke runner 실패 | 0 | UNKNOWN | non-zero | FAIL ✓ |
| 전체 통과 | 0 | PASS | 0 | ALL PASS ✓ |
| MongoDB 타임아웃 | non-zero | UNKNOWN | 0 | FAIL ✓ |

---

## 6. 다음 세션 시작 순서

1. 이 파일(`CHECKPOINT_BANNERSPEC_STAGE8.md`) 읽기
2. 운영서버에서 재실행:
   ```bash
   cd /opt/creative-resizer
   git pull origin master
   bash scripts/run-stage8-smoke-server.sh
   ```
3. `artifacts/stage8-smoke/<timestamp>/smoke.log` 확인
4. 실패 단계가 있으면 오류 내용 붙여넣기
5. 전체 PASS 후 최종 완료 커밋: `test: stage 8 smoke all pass`

---

## 7. 커밋 이력

| 커밋 | 메시지 | 주요 변경 |
|---|---|---|
| `(이번 커밋)` | fix: correct smoke worker build context and failure reporting | 이번 세션 수정 |
| `56daef5` | chore: pre-execution review complete | 사전 검토 |
| `f1e00dd` | chore: checkpoint stage 8 server smoke implementation | 체크포인트 |
| `1da5e57` | fix: create smoke artifact directory before logging | 초기화 순서 수정 |
| `defa527` | test: add isolated server-side stage 8 smoke environment | Smoke 환경 전체 구성 |
| `b9c911b` | fix: align worker JSON response with WorkerResponse contract | WorkerResponse 계약 수정 |

---

## 8. TODO 체크리스트

- [x] WorkerResponse 계약 수정 (b9c911b)
- [x] Smoke 환경 구성 (defa527)
- [x] artifact 초기화 순서 수정 (1da5e57)
- [x] 코드 전수 검토 완료 (56daef5)
- [x] **Worker build context 수정 — `.dockerignore` + context `./worker` (이번 커밋)**
- [x] **cleanup() false PASS 버그 수정 (이번 커밋)**
- [ ] 서버 재실행 후 Step 7 빌드 통과 확인
- [ ] MongoDB/Worker/API healthy
- [ ] seed 1차 (INSERT=68)
- [ ] 목록/상세 API
- [ ] 404 / 405
- [ ] WorkerResponse E2E
- [ ] seed 2차 멱등성
- [ ] 운영 컨테이너 무영향 확인
- [ ] 최종 완료 커밋

---

> 민감정보(URI, password, token, key)는 이 파일에 기록하지 않음.
