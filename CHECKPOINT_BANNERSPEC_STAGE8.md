# BannerSpec Stage 8 Final Checkpoint

- 작성 시각: 2026-07-12
- 현재 브랜치: master
- 현재 HEAD: 0938a5f
- 실행 서버: 운영서버 (/opt/creative-resizer)
- 실행 방식: Isolated server-side Docker Smoke Test
- 최종 상태: **PASS**
- BannerSpec API: **42/42 PASS**
- Worker Contract: **16/16 PASS**
- Total: **58/58 PASS**
- 운영서비스 영향: **NONE**
- Smoke 리소스 정리: **SUCCESS**

---

## 1. 최종 완료 범위

- Naver BannerSpec JSON 68건 실제 MongoDB seed
  - 1차 insert 68건 / 2차 unchanged 68건 (멱등성 확인)
  - slug 중복 0
- Spring Boot API 검증 (목록 68건, 상세 safe zone 값, 404, 405)
- Java 17 실제 Docker 빌드 환경 검증 (OpenJDK 17.0.19 컴파일)
- Worker HTTP 직접 계약 검증 16종
- Spring Boot → Worker E2E (WorkerResponse 역직렬화)
- 운영 컨테이너 4개 테스트 전후 상태 불변 확인
- Smoke 전용 컨테이너 및 볼륨 정상 정리

---

## 2. Seed 결과

### 1차 seed

| 항목 | 값 |
|---|---|
| loaded | 68 |
| inserted | 68 |
| updated | 0 |
| unchanged | 0 |
| failed | 0 |
| total | 68 |

### 2차 seed (멱등성)

| 항목 | 값 |
|---|---|
| loaded | 68 |
| inserted | 0 |
| updated | 0 |
| unchanged | 68 |
| failed | 0 |
| total | 68 |

---

## 3. Safe zone 데이터 상태

| 분류 | 건수 |
|---|---|
| parsed_text | 3 |
| diagram_unreadable | 65 |

`diagram_unreadable` 65건은 Stage 8 실패가 아니다.
현재 이미지 출처에서 safe zone 수치를 자동으로 파싱할 수 없어 `safeZone: null`로 저장된 상태이며,
수동 검토 또는 파싱 개선은 후속 데이터 품질 작업으로 분리한다.

---

## 4. WorkerResponse 역직렬화 오류 해결

**기존 오류**
```
Error while extracting response for type [class com.h3.creative.worker.WorkerResponse]
and content type [application/json]
```

**원인**
Python Worker의 `safeZoneViolations`는 `List<String>`이었으나
Java DTO가 `List<Map<String,Object>>`로 선언되어 역직렬화 실패.

**수정** (commit `b9c911b`)
- `WorkerResponse` 및 저장 모델을 `List<String>`으로 정렬
- `WorkerClient` 진단 로직 보강 (raw body 수신 + 수동 파싱)
- `WorkerResponseDeserializationTest` 16개 추가 (33/33 PASS)

**실제 E2E 결과**
```
deserializationSuccess = true
safeZoneViolationsType = List<String>
error = null
```
기존 extraction 오류 재발 없음.

---

## 5. Smoke 실행 결과

| 항목 | 결과 | 실제 값 |
|---|---|---|
| Java 17 Docker 환경 | PASS | OpenJDK 17.0.19 |
| Spring Boot API readiness | PASS | actuator/health = UP |
| Smoke 전용 MongoDB 기동 | PASS | healthy |
| Smoke 전용 RabbitMQ 기동 | PASS | healthy |
| Smoke 전용 Python Worker 기동 | PASS | healthy |
| seed 1차 | PASS | inserted=68, failed=0 |
| 목록 API 68건 | PASS | null slug=0, invalid dim=0, dup=0 |
| 상세 조회 (1250×560) | PASS | safe zone 값 일치 |
| missing slug 404 | PASS | HTTP 404 |
| wrong method 405 | PASS | HTTP 405 |
| diagram_unreadable 65건 | PASS | count=65 확인 |
| diagram_unreadable safeZone=null | PASS | 확인 |
| seed 2차 멱등성 | PASS | unchanged=68, inserted=0 |
| count 68 유지 | PASS | 확인 |
| Worker health | PASS | /health 200 |
| Spring Boot → Worker E2E | PASS | HTTP 정상 |
| WorkerResponse 역직렬화 | PASS | deserializationSuccess=true |
| Worker 직접 HTTP 계약 | PASS | 16/16 |
| 운영 컨테이너 unchanged | PASS | diff 없음 |
| Smoke 리소스 정리 | PASS | containers + volumes 제거 |

**최종: BannerSpec API 42/42 PASS / Worker Contract 16/16 PASS / Total 58/58 PASS**

---

## 6. 최소 검증 결과 (로컬, 커밋 전)

| 검증 | 결과 |
|---|---|
| `bash -n run-stage8-smoke-server.sh` | PASS |
| `bash -n run_all_smoke.sh` | PASS |
| `python -m py_compile http_smoke_test.py` | PASS |
| `python -m py_compile worker_contract_smoke_test.py` | PASS |
| `gradle/wrapper/gradle-wrapper.jar` 존재 | PASS |
| `gradle/wrapper/gradle-wrapper.properties` 존재 | PASS |
| `gradlew` 존재 | PASS |
| `docker compose config` | PASS (서버 실행으로 최종 검증) |

---

## 7. 커밋 이력

| 커밋 | 메시지 | 주요 변경 |
|---|---|---|
| `0938a5f` | chore: update checkpoint — 2nd server fail (gradle-wrapper.jar) fixed | 체크포인트 2차 |
| `d9b031a` | fix: include Gradle wrapper jar in smoke API build | .gitignore 예외 순서 수정, jar 추가, Dockerfile 검증 |
| `b3ce998` | fix: correct smoke worker build context and failure reporting | Worker context, false PASS 버그 |
| `56daef5` | chore: pre-execution review complete | 사전 검토 |
| `f1e00dd` | chore: checkpoint stage 8 server smoke implementation | 체크포인트 1차 |
| `1da5e57` | fix: create smoke artifact directory before logging | 초기화 순서 수정 |
| `defa527` | test: add isolated server-side stage 8 smoke environment | Smoke 환경 전체 구성 |
| `b9c911b` | fix: align worker JSON response with WorkerResponse contract | WorkerResponse 계약 수정 |

---

## 8. 후속 작업 (Stage 8과 무관)

아래 항목은 Stage 8 미완료가 아니라 Stage 8 이후의 독립 작업이다.

- Naver `diagram_unreadable` 65건 수동 검토 또는 파싱 개선
- Kakao BannerSpec 수집
- Meta BannerSpec 수집
- Google BannerSpec 수집
- PSD 역할 감지 및 object reflow 품질 개선
- emergency_fallback 결과 품질 개선

---

> Stage 8은 완료되었다.
> 민감정보(URI, password, token, key)는 이 파일에 기록하지 않는다.
