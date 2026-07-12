# BannerSpec Stage 8 Checkpoint

- 작성 시각: 2026-07-12 (4차 세션 — 사전 검토 완료, 서버 실행 대기)
- 현재 브랜치: master
- 현재 HEAD: f1e00dd (chore: checkpoint stage 8 server smoke implementation)
- origin/master: f1e00dd ← 동기화 완료
- 작업 상태: **PRE-EXECUTION REVIEW COMPLETE**
- 현재 작업: Server-side isolated Docker Smoke Test (Stage 8)

---

## 1. 현재 목표

BannerSpec Stage 8의 아래 항목을 운영서버 Docker 환경에서 실행 (로컬 환경 손상으로 서버 대체).

| 검증 항목 |
|---|
| Docker 내부 Java 17 compileJava |
| Docker 내부 Java 17 전체 test |
| Smoke 전용 MongoDB seed (Naver 68건) |
| seed 2회 멱등성 확인 (unchanged=68) |
| 실제 Spring Boot HTTP API Smoke Test (12단계) |
| Python Worker WorkerResponse 역직렬화 계약 검증 |
| 운영 컨테이너 무영향 확인 |
| Smoke 종료 후 전용 컨테이너·볼륨만 정리 |

---

## 2. 완료된 작업

### 2-A. WorkerResponse 역직렬화 계약 수정 — commit `b9c911b`

**근본 원인**: `layout_compositor.py` L218-221이 `safeZoneViolations`를 `hardFailReasons`에서
필터링한 `List[str]`로 반환. Java `List<Map<String,Object>>`와 타입 불일치.
빈 `[]`는 Jackson이 통과시키지만 실제 위반 문자열 포함 시 `MismatchedInputException` 발생.

| 항목 | 상태 | 근거 |
|---|---|---|
| `WorkerResponse.ResultItem.safeZoneViolations` `List<Map>` → `List<String>` | DONE | commit b9c911b |
| `BannerJob.BannerResult.safeZoneViolations` `List<Map>` → `List<String>` | DONE | commit b9c911b |
| `WorkerClient.generate()` raw body 수신 + 수동 파싱 + 진단 로그 | DONE | commit b9c911b |
| `WorkerResponseDeserializationTest` 16건 추가 | DONE | commit b9c911b |
| 전체 단위 테스트 33/33 PASS | DONE | 로컬 JDK21 실행 확인 |
| `fallbackErrors` `List<Map<String,Object>>` 유지 | DONE | Python은 dict 반환 — 변경 불필요 |

### 2-B. 운영서버용 격리 Smoke 환경 구성 — commit `defa527`

| 파일 | 상태 | 내용 |
|---|---|---|
| `build.gradle` | DONE | `toolchain { languageVersion=17 }` 복원 (Docker JDK17 필수) |
| `Dockerfile.worker.smoke` | DONE | python:3.11-slim + 300×250 JPEG fixture 자동 생성 |
| `docker-compose.smoke.yml` | DONE | project=`creative-resizer-stage8-smoke`, volume=`stage8-mongo-data`, DB=`creative_resizer_stage8_smoke`, Worker 추가, 리소스 제한, `127.0.0.1:18082` |
| `src/main/resources/application-smoke.yml` | DONE | `SMOKE_WORKER_URL`, DB=`creative_resizer_stage8_smoke` |
| `src/main/java/com/h3/creative/api/SmokeController.java` | DONE | `@Profile("smoke")` — `/api/smoke/worker-health`, `/api/smoke/worker-generate-test` |
| `scripts/worker_contract_smoke_test.py` | DONE | Worker 직접 HTTP 계약 검증 (safeZoneViolations `List<String>`) |
| `scripts/http_smoke_test.py` | DONE | Step 11-12 추가 (Worker health + E2E 역직렬화 via Spring Boot) |
| `scripts/run_all_smoke.sh` | DONE | BannerSpec API + Worker Contract 순차 실행 |
| `scripts/Dockerfile.smoke-runner` | DONE | 두 스크립트 + run_all_smoke.sh 포함 |
| `.gitignore` | DONE | `artifacts/` 제외 |

### 2-C. Smoke 아티팩트 초기화 순서 수정 — commit `1da5e57`

서버에서 첫 실행 시 `tee: artifacts/.../smoke.log: No such file or directory` 오류 발생.
원인: 로그 함수(`tee -a smoke.log`)가 `mkdir -p` 실행 전에 호출되는 초기화 역순.

| 항목 | 상태 |
|---|---|
| `BASH_SOURCE[0]` 기반 `SCRIPT_DIR`/`PROJECT_ROOT` 계산 | DONE |
| `mkdir -p ARTIFACT_DIR` + `touch SMOKE_LOG` 로그 함수 정의 전 실행 | DONE |
| 생성 실패 시 `exit 1` + 명확한 오류 메시지 | DONE |
| `bash -n` 문법 검사 통과 | DONE |
| `mkdir + touch` 시뮬레이션 정상 확인 | DONE |

### 2-D. 사전 코드 검토 완료 — 4차 세션

4차 세션에서 모든 Smoke 환경 파일을 전수 검토하여 서버 실행 전 잠재적 문제를 확인.

| 검토 항목 | 결과 |
|---|---|
| `Dockerfile.smoke` JDK17 2-stage 빌드 | OK — eclipse-temurin:17-jdk-jammy, toolchain 호환 |
| `Dockerfile.worker.smoke` Python 3.11 + gunicorn + fixture | OK — `--no-install-recommends` 영향 없음 (JPEG 처리만) |
| `docker-compose.smoke.yml` 네트워크·볼륨·헬스체크 | OK |
| `application-smoke.yml` RabbitMQ/MongoDB/Worker URL | OK |
| `SmokeController.java` 두 엔드포인트 | OK — `@Profile("smoke")`, isHealthy()/isSuccess() 존재 확인 |
| `WorkerClient.isHealthy()` | OK — 존재, GET /health |
| `WorkerResponse.isSuccess()` | OK — `error == null \|\| error.isBlank()` |
| `WorkerRequest` `@Builder` | OK |
| Spring Security | 없음 — `build.gradle`에 security 의존성 없음, 인증 차단 없음 |
| `resizer.py` sourceType="image" 경로 | OK — PIL 직접 처리 → `renderSource: "pillow_image"` |
| `safeZoneViolations: []` 역직렬화 | OK — `List<String>`에 빈 리스트 정상 매핑 |
| `RestTemplate` timeout vs gunicorn timeout | OK — 120s / 120s 일치 |
| `os.makedirs(output_dir, exist_ok=True)` | OK — 런타임에 디렉토리 생성 |
| `gradlew` + `gradlew.bat` git 추적 여부 | OK — `git ls-files` 확인 |

**추가 수정 필요 항목: 없음.** 모든 파일이 올바르게 구성되어 있음.

---

## 3. 미완료 항목

### 3-A. 운영서버 실제 Smoke Test — PENDING

코드 검토 완료. 서버에서 `git pull` 후 실행 필요.

```bash
cd /opt/creative-resizer
git pull origin master
bash scripts/run-stage8-smoke-server.sh
```

결과 확인: `artifacts/stage8-smoke/<timestamp>/smoke.log`

### 3-B. 서버 실행 필요 검증 항목

| 검증 항목 | 상태 |
|---|---|
| Java 17 compileJava (Docker 내부) | NOT_STARTED |
| Java 17 전체 test (Docker 내부) | NOT_STARTED |
| MongoDB healthy (stage8-smoke) | NOT_STARTED |
| Worker healthy (Dockerfile.worker.smoke 빌드) | NOT_STARTED |
| Spring Boot healthy (smoke profile, SmokeController 포함) | NOT_STARTED |
| seed 1차 — Naver 68건 INSERT | NOT_STARTED |
| 목록 API — count=68 확인 | NOT_STARTED |
| 상세 API — safeZone 필드 확인 | NOT_STARTED |
| 404 응답 확인 | NOT_STARTED |
| 405 응답 확인 | NOT_STARTED |
| Worker health via Spring Boot `/api/smoke/worker-health` | NOT_STARTED |
| WorkerResponse E2E 역직렬화 via `/api/smoke/worker-generate-test` | NOT_STARTED |
| Worker 직접 HTTP 계약 검증 (`worker_contract_smoke_test.py`) | NOT_STARTED |
| seed 2차 멱등성 — unchanged=68 | NOT_STARTED |
| 운영 컨테이너 무영향 확인 | NOT_STARTED |
| Smoke 리소스 정리 (컨테이너·볼륨) | NOT_STARTED |

---

## 4. 발견된 오류 및 조치 이력

| 오류 | 원인 | 조치 | 커밋 |
|---|---|---|---|
| `tee: artifacts/.../smoke.log: No such file or directory` | 초기화 역순: 로그 함수 → `mkdir -p` | `mkdir-p` + `touch SMOKE_LOG` 선행 실행 | `1da5e57` |
| `WorkerResponse MismatchedInputException` | `safeZoneViolations` 타입 불일치 (`List<Map>` vs Python `List[str]`) | `List<String>` 으로 수정 | `b9c911b` |
| 로컬 JDK17 없음 (`toolchain` 빌드 실패) | `sourceCompatibility` 로 임시 변경했다가 복원 | `Dockerfile.smoke`는 JDK17 Docker → toolchain 복원 | `defa527` |

---

## 5. 변경 파일 전체 목록 (이번 세션들)

| 파일 | 커밋 | 변경 내용 | 문법 검증 |
|---|---|---|---|
| `build.gradle` | b9c911b→defa527 | toolchain JDK17 복원 | — |
| `src/main/java/.../worker/WorkerResponse.java` | b9c911b | `safeZoneViolations` `List<Map>` → `List<String>` | 단위 테스트 PASS |
| `src/main/java/.../domain/BannerJob.java` | b9c911b | 동일 | 단위 테스트 PASS |
| `src/main/java/.../worker/WorkerClient.java` | b9c911b | raw body 수신·수동 파싱·진단 로그 | — |
| `src/test/.../WorkerResponseDeserializationTest.java` | b9c911b | 16개 단위 테스트 (33/33 PASS) | PASS |
| `Dockerfile.worker.smoke` | defa527 | Python Worker + fixture JPEG | — |
| `docker-compose.smoke.yml` | defa527 | Stage8 전용 Compose 구성 | — |
| `src/main/resources/application-smoke.yml` | defa527 | Smoke profile 설정 | — |
| `src/main/java/.../api/SmokeController.java` | defa527 | smoke profile 전용 엔드포인트 | — |
| `scripts/worker_contract_smoke_test.py` | defa527 | Worker 직접 HTTP 계약 검증 | `py_compile` PASS |
| `scripts/http_smoke_test.py` | defa527 | Step 11-12 추가 | `py_compile` PASS |
| `scripts/run_all_smoke.sh` | defa527 | 순차 실행 wrapper | `bash -n` PASS |
| `scripts/Dockerfile.smoke-runner` | defa527 | 두 스크립트 포함 | — |
| `scripts/run-stage8-smoke-server.sh` | defa527, 1da5e57 | 26단계 서버 오케스트레이터, 초기화 순서 수정 | `bash -n` PASS |
| `.gitignore` | defa527 | `artifacts/` 제외 | — |

---

## 6. 최소 검증 결과

| 검증 항목 | 결과 |
|---|---|
| `bash -n scripts/run-stage8-smoke-server.sh` | **PASS** |
| `bash -n scripts/run_all_smoke.sh` | **PASS** |
| `python -m py_compile scripts/http_smoke_test.py` | **PASS** |
| `python -m py_compile scripts/worker_contract_smoke_test.py` | **PASS** |
| 민감정보 패턴 스캔 (mongodb+srv, password, api_key 등) | **NO SENSITIVE DATA** |
| `WorkerResponseDeserializationTest` 33/33 | PASS (b9c911b 커밋 시 로컬 JDK21 확인) |
| `mkdir + touch` 시뮬레이션 (artifacts 디렉토리 생성) | **PASS** |
| 코드 전수 검토 (4차 세션) | **PASS — 추가 수정 불필요** |

---

## 7. Smoke 환경 구조

```
docker-compose.smoke.yml (project: creative-resizer-stage8-smoke)
├── mongo      : mongo:7, DB=creative_resizer_stage8_smoke, volume=stage8-mongo-data
├── rabbitmq   : rabbitmq:3-alpine, auto-startup=false (listener 비활성)
├── worker     : Dockerfile.worker.smoke (Python 3.11 + fixture /app/fixtures/test_banner.jpg)
├── api        : Dockerfile.smoke (JDK17 compileJava + test → smoke profile 기동)
│               SMOKE_WORKER_URL=http://worker:5000, 127.0.0.1:18082:8081
└── smoke      : scripts/Dockerfile.smoke-runner (http_smoke_test.py + worker_contract_smoke_test.py)
```

**Smoke API 단계**:
- Step 1-10 : BannerSpec HTTP API (seed/list/detail/404/405/멱등성)
- Step 11   : Worker health via Spring Boot `/api/smoke/worker-health`
- Step 12   : WorkerResponse E2E via Spring Boot `/api/smoke/worker-generate-test`
- Part 2    : Worker 직접 HTTP 계약 (`worker_contract_smoke_test.py`)

---

## 8. 다음 세션 시작 순서

1. 이 파일(`CHECKPOINT_BANNERSPEC_STAGE8.md`) 읽기
2. 운영서버에서 실행 (4차 세션에서 코드 검토 완료, 추가 수정 불필요):
   ```bash
   cd /opt/creative-resizer
   git pull origin master
   bash scripts/run-stage8-smoke-server.sh
   ```
3. `artifacts/stage8-smoke/<timestamp>/smoke.log` 내용 확인
4. 실패 단계가 있으면 오류 원인 분석 후 최소 수정
5. 전체 Smoke Test PASS 확인
6. 최종 완료 커밋: `test: stage 8 smoke all pass`

**진행 방침**: 다음 세션에서도 사용자에게 진행 여부를 묻지 않고,
체크포인트와 현재 Git 상태를 대조하여 합리적으로 진행한다.

---

## 9. 커밋 이력

| 커밋 | 메시지 | 주요 변경 |
|---|---|---|
| `f1e00dd` | chore: checkpoint stage 8 server smoke implementation | 체크포인트 문서 |
| `1da5e57` | fix: create smoke artifact directory before logging | 초기화 순서 수정 |
| `defa527` | test: add isolated server-side stage 8 smoke environment | Smoke 환경 전체 구성 |
| `b9c911b` | fix: align worker JSON response with WorkerResponse contract | WorkerResponse 계약 수정 |
| `1639b73` | test: add reproducible BannerSpec stage 8 smoke environment | 이전 Smoke 환경 (대체됨) |
| `98f49c5` | 8단계 BannerSpec seed/upsert 단위 테스트 추가 (13 PASS) | BannerSpecSeedServiceTest 등 |

---

## 10. TODO 체크리스트

- [x] `WorkerResponse.safeZoneViolations` `List<String>` 수정
- [x] `WorkerClient` raw body 진단 로그 추가
- [x] `WorkerResponseDeserializationTest` 16건 (33/33 PASS)
- [x] Smoke 환경 파일 전체 구성 (Dockerfile.worker.smoke, docker-compose.smoke.yml 등)
- [x] `SmokeController.java` (`@Profile("smoke")`) 구현
- [x] `run-stage8-smoke-server.sh` 26단계 작성
- [x] artifact 초기화 순서 수정 (commit 1da5e57)
- [x] `bash -n` 통과
- [x] `py_compile` 통과
- [x] **코드 전수 검토 완료 (4차 세션) — 추가 수정 불필요**
- [ ] 서버에서 `git pull` 후 `smoke.log` 정상 생성 확인
- [ ] Java 17 compileJava (Docker 내부)
- [ ] Java 17 전체 test (Docker 내부)
- [ ] MongoDB healthy
- [ ] Worker healthy
- [ ] Spring Boot healthy
- [ ] seed 1차 (INSERT=68)
- [ ] 목록 API (count=68)
- [ ] 상세 API
- [ ] 404 / 405
- [ ] WorkerResponse E2E (Spring Boot → Worker)
- [ ] Worker 직접 HTTP 계약 검증
- [ ] seed 2차 멱등성 (unchanged=68)
- [ ] 운영 컨테이너 무영향 확인
- [ ] Smoke 리소스 정리 확인
- [ ] 최종 완료 커밋

---

> 민감정보(URI, password, token, key)는 이 파일에 기록하지 않음.
