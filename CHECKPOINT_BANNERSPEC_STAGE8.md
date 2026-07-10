# BannerSpec Stage 8 Checkpoint

- 작성 시각: 2026-07-10 (2차 세션 업데이트)
- 현재 브랜치: master
- 현재 HEAD: 98f49c5
- 작업 디렉터리: C:\company\source\creative-resizer
- 최종 상태: PARTIAL
- 중단 사유: 실행 환경(MongoDB, JDK17) 미구성 — 코드 구현 완료, 서버 기동 시 최종 검증 가능

---

## 작업 목표

### 이번 단계 목표 (8단계)
- [x] Naver 68개 지면 BannerSpec JSON seed 데이터 준비
- [x] BannerSpec 도메인 확장 (22개 optional 필드)
- [x] BannerSpec API 신규 구현 (`/api/banner-specs`)
- [x] Worker safe zone parseStatus 정책 구현 및 검증
- [x] debug overlay에 bannerSpec 메타 포함
- [ ] MongoDB 실제 seed 실행 및 멱등성 검증
- [ ] Spring Boot 실제 HTTP API smoke test
- [ ] JDK 17 공식 빌드 검증
- [ ] safe zone needsReview/confidence 정책 정비 (diagram_unreadable 65건 vs needsReview 1건 불일치)

### 이번 단계 범위 제외 (deferred)
- Kakao spec 실제 수집
- Meta spec 실제 수집
- Google spec 실제 수집

---

## 커밋 상태

### Committed (master 브랜치에 포함됨)

| Hash | Message | 주요 파일 |
|---|---|---|
| `98f49c5` | 8단계 BannerSpec seed/upsert 단위 테스트 추가 (13 PASS) | BannerSpecSeedServiceTest.java (10개), SpecMongoServiceUpsertTest.java (3개), build.gradle (junit-platform-launcher 추가) |
| `9c83fa9` | 8단계 BannerSpec DB화 보완: safe zone 정책 강화 + seed 응답 개선 | build.gradle, BannerSpecController.java, SpecMongoService.java, BannerSpecSeedService.java, safe_zone.py |
| `0030e11` | 8단계: Naver BannerSpec 독립 seed 스크립트 추가 | scripts/seed_naver_specs.py |
| `84463a9` | 8단계: BannerSpec DB 확장 레이어 추가 (Naver 68개 지면) | BannerSpecController.java, BannerSpec.java, SpecMongoService.java, BannerService.java, BannerSpecSeedService.java, WorkerRequest.java, naver.json, debug_overlay.py, resizer.py, safe_zone.py |

### Uncommitted

없음 — `git status --short` 출력 없음. 작업 트리 clean.

---

## 변경 파일 목록

| 파일 | 상태 | 변경 목적 | 커밋 여부 | 후속 확인 필요 |
|---|---|---|---|---|
| `src/main/resources/banner-specs/naver.json` | 신규 | Naver 68개 지면 seed 데이터 | YES (84463a9) | MongoDB 실제 upsert 확인 |
| `src/main/java/com/h3/creative/domain/BannerSpec.java` | 수정 | 22개 optional 필드 추가 (category, placementType, safeZoneParseStatus 등) | YES (84463a9) | 없음 |
| `src/main/java/com/h3/creative/worker/WorkerRequest.java` | 수정 | SpecItem에 safeZoneParseStatus, fileRules 필드 추가 | YES (84463a9) | 없음 |
| `src/main/java/com/h3/creative/mongo/SpecMongoService.java` | 수정 | findByMediaAndSlug/findBySlug/findByPlacementType 추가, upsertBySlug boolean 반환 | YES (9c83fa9) | MongoDB 실제 동작 확인 |
| `src/main/java/com/h3/creative/api/BannerSpecController.java` | 신규 | /api/banner-specs 신규 엔드포인트 (기존 /api/spec 무영향) | YES (9c83fa9) | HTTP API 실제 smoke test |
| `src/main/java/com/h3/creative/service/BannerSpecSeedService.java` | 신규 | classpath JSON → MongoDB upsert, inserted/updated/total 상세 카운트 반환 | YES (9c83fa9) | MongoDB 실제 seed 실행 확인 |
| `src/main/java/com/h3/creative/service/BannerService.java` | 수정 | SpecItem 빌드 시 safeZoneParseStatus + fileRules 전달 | YES (84463a9) | 없음 |
| `worker/safe_zone.py` | 수정 | 7종 parseStatus 정책 구현, parsed_diagram 추가, incomplete WARNING, doctest 추가 | YES (9c83fa9) | doctest PASS 확인됨 |
| `worker/debug_overlay.py` | 수정 | layout JSON에 bannerSpec 메타 추가, generate_debug_files spec_info 파라미터 | YES (84463a9) | E2E PASS 확인됨 |
| `worker/resizer.py` | 수정 | object-reflow 분기에서 _gen_debug 호출 시 spec_info=spec 추가 (1줄) | YES (84463a9) | 없음 |
| `scripts/seed_naver_specs.py` | 신규 | 서버 없이 독립 실행 가능한 MongoDB seed 스크립트 | YES (0030e11) | MONGODB_URI 환경변수 필요 |
| `build.gradle` | 수정 | UTF-8 BOM 제거 (JDK17 설정 유지, BOM이 Groovy 파서 오류 유발했음) | YES (9c83fa9) | JDK17 환경에서 compileJava 재검증 필요 |

**존재하지 않는 파일 (기록 안 함):**
- `service/BannerSpecService.java` — 이 이름의 파일 없음. 실제 파일명은 `BannerSpecSeedService.java`

---

## 현재 검증 결과

| 검증 항목 | 상태 | 실제 결과 | 근거 |
|---|---|---|---|
| JSON 68건 로딩 | PASS | 68건 | python -c json.load 직접 실행 |
| category 15종 | PASS | 15종 | python 집합 계산 |
| duplicate id | PASS | 0건 | python 중복 검사 |
| duplicate slug | PASS | 0건 | python 중복 검사 |
| duplicate sourceUrl | PASS | 0건 | python 중복 검사 |
| parsed_text 건수 | PASS | 3건 | python 필터 |
| diagram_unreadable 건수 | PASS | 65건 | python 필터 |
| needsReview 건수 | PASS | 1건 (shopping-search-ad-500x500) | python 필터 |
| safe zone: parsed_text + complete | PASS | spec hard constraint 적용 (top=50) | normalize_safe_zone 직접 실행 |
| safe zone: parsed_text + incomplete | PASS | WARNING 출력 후 fallback | normalize_safe_zone 직접 실행 |
| safe zone: parsed_diagram + complete | PASS | spec hard constraint 적용 (top=30) | normalize_safe_zone 직접 실행 |
| safe zone: diagram_unreadable fallback | PASS | 비율 기반 fallback (top=44) | normalize_safe_zone 직접 실행 |
| safe zone: no_safezone fallback | PASS | fallback | normalize_safe_zone 직접 실행 |
| safe zone: parse_failed fallback | PASS | fallback | normalize_safe_zone 직접 실행 |
| safe zone: safezone_size_only fallback | PASS | fallback | normalize_safe_zone 직접 실행 |
| doctest (safe_zone.py) | PASS | 0 failures | python -m doctest safe_zone.py |
| debug PNG 생성 (Case A 1250x560) | PASS | 7,893 bytes | generate_debug_files 직접 실행 |
| debug PNG 생성 (Case B 1200x1200) | PASS | 13,429 bytes | generate_debug_files 직접 실행 |
| layout JSON 생성 | PASS | 21개 필드, 필수 13개 포함 | json.load 검증 |
| safeZoneBox (Case A) | PASS | x=240, y=50, w=770, h=475 | layout JSON 값 비교 |
| ZIP debug 파일 제외 | PASS | CREATIVE_DEBUG_OVERLAY=true 조건에서만 생성 | is_debug_enabled() 확인 |
| BannerController 변경 없음 | PASS | 8단계 관련 코드 0건 | grep 결과 |
| WorkerResponse 호환성 | PASS | 8단계 관련 코드 0건 | grep 결과 |
| SpecController 변경 없음 | PASS | 8단계 관련 코드 0건 | grep 결과 |
| /api/spec 영향 없음 | PASS | 완전히 별도 경로 | BannerSpecController @RequestMapping 확인 |
| smart-fit 경로 유지 | PASS | objectReflow 조건 source_type=="psd" 유지 | resizer.py 1083번 줄 확인 |
| cover/contain/blur-bg 유지 | PASS | resizer.py 1줄만 추가, 기존 분기 무영향 | git diff 확인 |
| sourceType=image → object-reflow 미진입 | PASS | source_type=="psd" 조건으로 보호됨 | resizer.py 코드 확인 |
| Java 18 compileJava | PASS | BUILD SUCCESS | gradlew.bat compileJava (JDK18 임시 설정) |
| Java 21 compileJava | PASS | BUILD SUCCESS | gradlew.bat compileJava (JDK21 임시 설정, ~/.jdks/ms-21.0.11) |
| Java 17 compileJava | **미실행** | JDK17 미설치 | 코드 문제 아님, 환경 문제. JDK18/21 모두 SUCCESS |
| BannerSpecSeedServiceTest (10개) | PASS | 10/10 PASS | Mockito 단위 테스트 (MongoDB 없음) |
| SpecMongoServiceUpsertTest (3개) | PASS | 3/3 PASS | Mockito 단위 테스트 (MongoDB 없음) |
| MongoDB seed 실행 | **미실행** | Atlas URI 없음, 서버 미기동 | 실행 환경 없음 |
| MongoDB upsert 멱등성 | **미실행** | 미실행 | - |
| 실제 HTTP API | **미실행** | Spring Boot 미기동 | 실행 환경 없음 |
| seed 응답 포맷 | **미실행** | 코드상 {media, loaded, inserted, updated, total} | 실제 HTTP 응답 미확인 |

---

## 현재 발견된 문제

### 문제 1: 실제 HTTP API 미검증
- **원인**: Spring Boot, MongoDB, RabbitMQ 미기동. `.env` 파일 없음 (`.env.example`만 존재)
- **영향**: API 응답은 코드 레벨 검증뿐, 실제 E2E 보장 불가
- **다음 조치**: `.env` 구성 → `docker-compose up` 또는 `gradlew bootRun` → curl smoke test

### 문제 2: MongoDB seed 미실행
- **원인**: MongoDB Atlas URI 미설정 (로컬 27017 미기동), Docker 미설치
- **영향**: 68건 실제 저장, upsert, 멱등성 미확인
- **다음 조치**: 로컬 MongoDB 또는 Atlas URI 구성 후 seed 실행. 또는 Docker 설치 후 `docker-compose up mongo` 실행

### 문제 3: JDK 17 미검증
- **원인**: 로컬 JDK 18 설치, JDK 17 미설치. build.gradle은 `languageVersion = JavaLanguageVersion.of(17)` 유지 중
- **영향**: 프로젝트 공식 런타임 기준 빌드 보장 불가. JDK18 컴파일은 성공 확인됨
- **다음 조치**: JDK 17 설치 후 `./gradlew clean compileJava`. 또는 Docker `gradle:7-jdk17` 이미지 사용

### 문제 4: needsReview 정책 불일치
- **현황**: diagram_unreadable 65건인데 needsReview=true는 1건뿐
- **영향**: fallback이 "가이드 미확인 지면"임이 데이터상 불분명함. 향후 분석 시 혼선 가능
- **다음 조치**: diagram_unreadable 전체를 needsReview=true로 설정하거나, 별도 `safeZoneConfidence` 필드로 구분하는 정책 결정 필요. 이번 단계에서는 변경 보류

### 문제 5: 외부 매체 미수집
- Kakao/Meta/Google spec: 이번 단계 deferred
- Naver 완료 후 별도 작업으로 진행

### 문제 6: build.gradle BOM 이력
- **현황**: 이전 세션에서 PowerShell `Set-Content -Encoding utf8`이 UTF-8 BOM을 추가해 Groovy 파서 오류 발생
- **조치 완료**: `[System.Text.UTF8Encoding]::new($false)` + `WriteAllBytes`로 BOM 제거 후 커밋됨 (9c83fa9)
- **다음 세션 주의**: build.gradle 수정 시 반드시 BOM 없는 방식으로 저장

---

## 다음 세션 작업 순서

1. 이 파일(`CHECKPOINT_BANNERSPEC_STAGE8.md`) 읽기
2. `git status`와 `git log -3 --oneline`으로 HEAD가 `9c83fa9`인지 확인
3. `.env` 파일 존재 여부 확인 (없으면 Atlas URI로 생성)
4. MongoDB 연결 가능 여부 확인
5. `./gradlew bootRun` 또는 `docker-compose up` 으로 서버 기동
6. seed 1차 실행 → inserted=68 확인
7. seed 2차 실행 → updated=68, inserted=0 (멱등성) 확인
8. HTTP API smoke test (목록/상세/404) 실행
9. `GET /api/banner-specs?media=naver` count=68 확인
10. `GET /api/banner-specs/naver/naver-gfa-mobile-da-image-banner-1250x560` safeZone 값 확인
11. JDK 17 환경에서 `./gradlew clean compileJava` 실행
12. needsReview 정책 결정 (사용자 확인 필요)
13. 최종 PASS/PARTIAL/FAIL 보고서 작성

---

## 다음 세션용 실행 명령

```bash
# Git 상태 확인
git status
git log -3 --oneline

# 환경 확인
java -version
python --version
```

```powershell
# Gradle 버전 확인
.\gradlew.bat --version
```

```bash
# .env 파일 확인 (없으면 Atlas URI로 생성 필요)
ls .env

# MongoDB 로컬 기동 (Docker가 있는 경우)
docker-compose up -d mongo

# Spring Boot 실행 (MongoDB + RabbitMQ 기동 후)
# Windows PowerShell
.\gradlew.bat bootRun

# Seed 1차 실행
curl -X POST "http://localhost:8081/api/banner-specs/seed?media=naver"
# 기대: {"media":"naver","loaded":68,"inserted":68,"updated":0,"total":68}

# Seed 2차 실행 (멱등성 확인)
curl -X POST "http://localhost:8081/api/banner-specs/seed?media=naver"
# 기대: {"media":"naver","loaded":68,"inserted":0,"updated":68,"total":68}

# 목록 조회 (68건 확인)
curl "http://localhost:8081/api/banner-specs?media=naver"

# 상세 조회 (parsed_text 지면 safeZone 확인)
curl "http://localhost:8081/api/banner-specs/naver/naver-gfa-mobile-da-image-banner-1250x560"
# 기대: safeZone.top=50, safeZone.right=240, safeZone.bottom=35, safeZone.left=240

# 404 검증
curl -i "http://localhost:8081/api/banner-specs/naver/not-exists"
# 기대: HTTP 404

# Python seed 스크립트 (서버 없이 직접 MongoDB 접근)
# MONGODB_URI=mongodb+srv://... python scripts/seed_naver_specs.py
# python scripts/seed_naver_specs.py --verify
```

```bash
# Worker doctest 재확인
cd worker && python -m doctest safe_zone.py && echo "PASS"

# debug overlay E2E 재실행 (CREATIVE_DEBUG_OVERLAY=true 필요)
```

```bash
# JDK 17 컴파일 검증 (JDK17 설치 후)
java -version  # 17.x 확인
.\gradlew.bat clean compileJava
```

---

## 환경 정보

| 항목 | 값 |
|---|---|
| OS | Windows 11 Home 10.0.26200 |
| Shell | PowerShell 5.1 (primary), Bash (Git Bash) |
| Java | 18.0.2.1 (JDK17 미설치) |
| Gradle | 9.4.1 |
| Python | 3.12.0 |
| Docker | 미설치 |
| Docker Compose | 미설치 |
| Spring Boot 포트 | 8081 |
| RabbitMQ 포트 | 5672 |
| MongoDB URI | not configured (.env 없음, .env.example만 존재) |
| MongoDB Atlas | Studio 3T로 접속 가능 (URI 별도 확인 필요) |
| RabbitMQ 연결 | not configured |
| OpenAI API Key | not configured (세션 내 갱신 이력 있음) |

> 민감정보(URI, password, token, key)는 이 파일에 기록하지 않음.

---

## TODO

### 완료된 항목
- [x] Naver 68개 지면 JSON seed 데이터 준비 (naver.json)
- [x] BannerSpec 도메인 22개 optional 필드 확장
- [x] SpecMongoService 신규 메서드 추가 (findByMediaAndSlug, upsertBySlug 등)
- [x] BannerSpecController 신규 구현 (/api/banner-specs)
- [x] BannerSpecSeedService 신규 구현 (classpath JSON → MongoDB upsert)
- [x] WorkerRequest.SpecItem safeZoneParseStatus/fileRules 추가
- [x] BannerService SpecItem 빌드 시 8단계 필드 전달
- [x] safe_zone.py 7종 parseStatus 정책 구현 (parsed_diagram 포함)
- [x] safe_zone.py parsed_text incomplete → WARNING + fallback
- [x] safe_zone.py doctest 전체 PASS
- [x] debug_overlay.py bannerSpec 메타 layout JSON 포함
- [x] resizer.py spec_info 전달 (object-reflow 분기)
- [x] scripts/seed_naver_specs.py 독립 seed 스크립트
- [x] build.gradle BOM 제거 (JDK17 설정 유지)
- [x] Worker debug overlay E2E (4 cases: parsed_text / diagram_unreadable / incomplete / parsed_diagram)
- [x] JSON 68건 무결성 검증 (dup id 0, dup slug 0, dup sourceUrl 0)
- [x] Java 18 compileJava BUILD SUCCESS
- [x] Java 21 compileJava BUILD SUCCESS (JDK21 ms-21.0.11 사용)
- [x] BannerSpecSeedServiceTest 10개 단위 테스트 PASS (Mockito, MongoDB 없음)
- [x] SpecMongoServiceUpsertTest 3개 단위 테스트 PASS (Mockito, MongoDB 없음)
- [x] 기존 기능 보호 검증 (BannerController/WorkerResponse/SpecController 무변경)

### 미완료 항목
- [ ] Java 17 compileJava 실행 (JDK17 환경 필요 — JDK18/21은 BUILD SUCCESS 확인됨)
- [ ] Java 17 test 실행
- [ ] MongoDB 실행 환경 구성
- [ ] seed 1차 실행 (inserted=68 확인)
- [ ] seed 2차 실행 (updated=68, inserted=0 멱등성 확인)
- [ ] 68건 DB 저장 확인
- [ ] duplicate slug 0 DB 확인
- [ ] HTTP 목록 API 실제 실행 (count=68)
- [ ] HTTP 상세 API 실제 실행 (safeZone 값 확인)
- [ ] HTTP 404 검증 (not-exists slug)
- [ ] seed 응답 포맷 실제 확인
- [ ] diagram_unreadable 65건 needsReview 정책 결정 (needsReview=true vs 별도 필드)
- [ ] Worker debug E2E 실제 서버 환경에서 재검증
- [ ] 기존 Banner 생성 흐름 회귀 검증 (실제 PSD 업로드)
- [ ] 최종 8단계 PASS 판정 보고서 작성
- [ ] Kakao spec 수집 (deferred)
- [ ] Meta spec 수집 (deferred)
- [ ] Google spec 수집 (deferred)
