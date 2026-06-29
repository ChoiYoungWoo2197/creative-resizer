# Creative Resizer

PSD 파일 하나를 업로드하면 Google, Meta, Naver, Kakao 등 주요 광고 매체의 규격에 맞는 배너 이미지를 자동으로 생성해주는 내부 툴.

---

## 기술 스택

| 영역 | 기술 |
|---|---|
| API 서버 | Spring Boot 3.3.5 (Java 17) |
| 이미지 처리 | Python 3.11 + psd-tools + Pillow |
| 메시지 큐 | RabbitMQ (기존 서버 공유) |
| DB | MongoDB (기존 서버 공유) |
| 컨테이너 | Docker + docker-compose |

---

## 프로젝트 구조

```
creative-resizer/
├── Dockerfile                          # Spring Boot 멀티스테이지 빌드
├── docker-compose.yml                  # api + worker + 스토리지 볼륨
├── build.gradle
├── settings.gradle
│
├── src/main/java/com/h3/creative/
│   ├── CreativeResizerApplication.java
│   ├── api/
│   │   └── BannerController.java       # REST 엔드포인트
│   ├── config/
│   │   └── RabbitConfig.java           # RabbitMQ Exchange/Queue 설정
│   ├── domain/
│   │   ├── BannerJob.java              # 작업 이력 도큐먼트
│   │   └── BannerSpec.java             # 매체별 규격 도큐먼트
│   ├── mongo/
│   │   ├── BannerMongoService.java     # 작업 이력 CRUD
│   │   └── SpecMongoService.java       # 규격 CRUD
│   ├── queue/
│   │   ├── message/BannerMessage.java  # MQ 메시지 DTO
│   │   ├── producer/BannerProducer.java
│   │   └── consumer/BannerConsumer.java
│   └── service/
│       └── BannerService.java          # 업로드 → MQ 발행 → 상태 관리
│
├── src/main/resources/
│   └── application.yml                 # 프로파일: default / local / prod
│
└── worker/                             # Python 이미지 처리 서버
    ├── Dockerfile
    ├── requirements.txt                # flask, psd-tools, Pillow
    ├── app.py                          # Flask API (POST /generate)
    └── resizer.py                      # 리사이즈 로직 (cover/contain/blur-bg)
```

---

## 아키텍처

```
클라이언트
  │
  ▼
creative-api (Spring Boot :8081)
  │  PSD 파일 저장 → MongoDB에 작업 등록 → RabbitMQ 발행
  │
  ▼
creative.banner.queue (RabbitMQ)
  │
  ▼
BannerConsumer → creative-worker (Python Flask :5000)
  │              POST /generate → psd-tools 파싱 → 규격별 리사이즈 → ZIP
  │
  ▼
공유 볼륨 (creative-storage)
  ├── uploads/   원본 PSD
  ├── outputs/   생성 이미지
  └── zips/      다운로드용 ZIP
```

---

## 리사이즈 모드

| 모드 | 설명 |
|---|---|
| `cover` | 꽉 채우기. 비율 유지, 잘릴 수 있음 |
| `contain` | 전체 보이기. 여백(흰색) 생길 수 있음 |
| `blur-bg` | 원본 비율 유지 + 남은 영역 블러 배경으로 채움 |

---

## MongoDB 컬렉션

### `banner_job` — 작업 이력

| 필드 | 타입 | 설명 |
|---|---|---|
| `id` | String | 작업 ID |
| `advertiser` | String | 광고주명 |
| `campaignName` | String | 캠페인명 |
| `targetMedia` | List | 생성 대상 매체 |
| `resizeMode` | String | cover / contain / blur-bg |
| `outputFormat` | String | png / jpg / webp |
| `status` | String | pending → processing → done / fail |
| `psdPath` | String | 원본 PSD 경로 |
| `zipPath` | String | 완성 ZIP 경로 |
| `createdAt` | DateTime | |
| `updatedAt` | DateTime | |

### `banner_spec` — 매체별 규격

| 필드 | 타입 | 설명 |
|---|---|---|
| `id` | String | |
| `media` | String | google / meta / naver / kakao / linkedin / tiktok |
| `placementName` | String | 지면명 (디스플레이, 피드 등) |
| `width` | int | px |
| `height` | int | px |
| `aspectRatio` | String | 1:1, 16:9 등 |
| `active` | boolean | 활성 여부 |

---

## RabbitMQ

| 항목 | 값 |
|---|---|
| Exchange | `creative.banner` (Direct) |
| Queue | `creative.banner.queue` |
| Routing Key | `banner.generate` |

---

## REST API

### 배너 생성

```
POST /api/banner/upload
Content-Type: multipart/form-data

psdFile      파일
advertiser   광고주명
campaignName 캠페인명
targetMedia  매체 목록 (google, meta, naver, kakao ...)
resizeMode   cover | contain | blur-bg  (기본: cover)
outputFormat png | jpg | webp           (기본: png)
```

### 작업 조회

```
GET /api/banner/job/{id}     단건 조회
GET /api/banner/jobs         전체 목록
```

### 규격 관리

```
GET    /api/banner/spec           전체 규격 목록
GET    /api/banner/spec?media=google  매체별 필터
POST   /api/banner/spec           규격 등록
DELETE /api/banner/spec/{id}      규격 삭제
```

### Worker 헬스체크

```
GET http://creative-worker:5000/health
```

---

## 로컬 실행

### 사전 요건

- Java 17
- Docker Desktop
- MongoDB, RabbitMQ 실행 중

### Spring Boot 실행

```bash
./gradlew bootRun --args='--spring.profiles.active=local'
```

### Python Worker 단독 실행

```bash
cd worker
pip install -r requirements.txt
OUTPUT_DIR=C:/company/storage/creative/outputs \
ZIP_DIR=C:/company/storage/creative/zips \
python app.py
```

---

## 서버 배포

```bash
# 빌드 & 실행
docker-compose up -d --build

# 로그 확인
docker logs -f creative-api
docker logs -f creative-worker

# 재시작
docker-compose restart
```

### 포트

| 서비스 | 외부 포트 | 내부 포트 |
|---|---|---|
| creative-api | 18081 | 8081 |
| creative-worker | 내부 전용 | 5000 |

---

## 스토리지 볼륨 경로

Docker 볼륨 `creative-storage` → 컨테이너 내 `/app/storage/`

```
/app/storage/
├── uploads/    원본 PSD (UUID_파일명.psd)
├── outputs/    생성 이미지 ({jobId}/media_WxH.png)
└── zips/       다운로드 ZIP ({jobId}.zip)
```

---

## 환경변수 (prod)

| 변수 | 설명 |
|---|---|
| `SPRING_DATA_MONGODB_URI` | MongoDB 연결 URI |
| `SPRING_RABBITMQ_HOST` | RabbitMQ 호스트 |
| `CREATIVE_STORAGE_UPLOAD_DIR` | PSD 업로드 경로 |
| `CREATIVE_STORAGE_OUTPUT_DIR` | 이미지 출력 경로 |
| `CREATIVE_STORAGE_ZIP_DIR` | ZIP 저장 경로 |
| `CREATIVE_WORKER_URL` | Python Worker URL |

---

## 지원 매체 규격 (MVP)

| 매체 | 규격 |
|---|---|
| Google | 1200×628, 1200×1200, 900×1600, 300×250, 728×90, 160×600, 320×100 |
| Meta | 1080×1080, 1080×1350, 1080×1920 |
| Naver | 사내 규격표 DB 등록 |
| Kakao | 사내 규격표 DB 등록 |
| LinkedIn | 1200×628, 1080×1080, 1080×1350 |
| TikTok | 1080×1920 |

---

## 개발 로드맵

- [x] MVP 1차 — 프로젝트 초기 구성 (API + Worker + Docker)
- [ ] Worker HTTP 연동 — BannerService → Python /generate 호출
- [ ] 규격 초기 데이터 삽입 — Google/Meta 기본 규격 스크립트
- [ ] 프론트엔드 — Vue 업로드/결과 화면
- [ ] MVP 2차 — PSD 레이어 기반 자동 재배치 (레이어명 규칙 적용)
