# Creative Resizer

이미지(PSD, PNG, JPG, JPEG)를 업로드하면 Google, Meta, Naver, Kakao 등 주요 광고 매체의 규격에 맞는 배너를 자동 생성하는 내부 툴.

서비스 URL: **https://creative.heeil.com**

---

## 기술 스택

| 영역 | 기술 |
|---|---|
| 프론트엔드 | Vue 3 + Element Plus + Vite |
| API 서버 | Spring Boot 3.3.5 (Java 17) |
| 이미지 처리 | Python 3.11 + psd-tools + Pillow + OpenAI |
| 메시지 큐 | RabbitMQ (`heeil.h3` vhost, 192.168.100.12) |
| DB | MongoDB Atlas (`creative_resizer`) |
| 웹서버 | Nginx (프론트 서빙 + API 프록시) |
| 컨테이너 | Docker + docker compose v2 |
| 리버스 프록시 | Apache (`creative.heeil.com` → 127.0.0.1:3001) |

---

## 프로젝트 구조

```
creative-resizer/
├── Dockerfile                          # Spring Boot 멀티스테이지 빌드
├── docker-compose.yml
├── build.gradle
│
├── frontend/                           # Vue 3 프론트엔드
│   ├── Dockerfile                      # Nginx + 빌드 결과물
│   └── src/
│       ├── views/
│       │   ├── UploadView.vue          # 배너 생성 (2패널 레이아웃)
│       │   ├── JobListView.vue         # 작업 목록 + 우측 결과 패널
│       │   ├── P5BannerEditor.vue      # Konva 기반 레이어 편집기 (TYPE G 전용)
│       │   └── SpecView.vue            # 규격 관리
│       └── api/banner.js               # Axios API 클라이언트
│
├── src/main/java/com/h3/creative/
│   ├── api/BannerController.java       # REST 엔드포인트 (/upload, /analyze, /job/*)
│   ├── domain/BannerJob.java           # 작업 이력 도큐먼트
│   ├── domain/BannerSpec.java          # 매체별 규격 도큐먼트
│   ├── queue/                          # RabbitMQ producer / consumer
│   ├── service/BannerService.java      # 작업 제출 · 처리 로직
│   └── worker/WorkerClient.java        # Python Worker HTTP 클라이언트
│
└── worker/                             # Python 이미지 처리 워커
    ├── Dockerfile
    ├── requirements.txt
    ├── app.py                          # Flask (POST /generate, GET /health)
    └── clean_pipeline/                 # clean_v1 파이프라인
        ├── pipeline_type_selector.py   # TYPE 라우팅 (A / G)
        ├── orchestrator.py             # 파이프라인 단계 조율
        ├── contracts.py               # 공통 타입 (TargetSpec, StageResult …)
        ├── bridge/                    # request_adapter / response_adapter
        ├── psd/                       # TYPE G PSD 처리
        │   ├── psd_layer_reader.py    # P2: PSD 레이어 구조 분석
        │   ├── bg_extractor.py        # P3: 배경 추출 + Flux outpaint
        │   └── element_compositor.py  # P4: 레이어 letterbox 합성 + layout_result 생성
        ├── typed/                     # TYPE별 파이프라인 구현
        │   ├── type_g_pipeline.py     # TYPE G 진입점 (PSD 레이어 재배치)
        │   ├── flux_outpainter.py     # Flux API 배경 생성
        │   └── smart_resizer.py       # smart resize 유틸
        ├── source/                    # P1: 원본 소스 정규화
        ├── analysis/                  # GPT-4o 객체 분석 (TYPE C 등)
        ├── extraction/                # 객체 좌표 추출
        ├── scene/                     # OpenAI 배경 생성 (TYPE B/D/E/F)
        ├── validation/                # 장면 검증
        ├── layout/                    # Safe-zone 배치 검증
        └── render/                    # 최종 합성
```

---

## 아키텍처

```
브라우저 (https://creative.heeil.com)
  │
  ▼
Apache (포트 80) — LimitRequestBody 200MB
  │
  ▼
creative-nginx (포트 3001) — Vue 정적 파일 서빙
  │  /api/* → creative-api:8081 프록시
  │
  ▼
creative-api (Spring Boot :8081 / 외부 :18081)
  │  파일 저장 (/app/storage/uploads/)
  │  MongoDB에 작업(BannerJob) 등록
  │  RabbitMQ 발행
  │
  ▼
creative.banner.queue (RabbitMQ)
  │
  ▼
BannerConsumer → creative-worker (Python Flask :5000)
  │              POST /generate  (pipelineVersion=clean_v1 고정)
  │              P1~P8 clean_pipeline 실행
  │              ZIP 생성
  │
  ▼
공유 볼륨 (/opt/creative-resizer/storage ↔ /app/storage)
  ├── uploads/   원본 파일
  ├── outputs/   생성 이미지
  └── zips/      다운로드 ZIP
```

---

## clean_v1 파이프라인

모든 생성 요청은 `pipelineVersion=clean_v1` 파이프라인으로 처리된다.
실패 시 legacy fallback은 없다. 어느 단계에서든 FAIL이면 그 결과를 그대로 반환한다.

### 파이프라인 TYPE 라우팅 (`pipeline_type_selector.py`)

원본 크기와 타겟 규격을 비교해 TYPE을 자동 선택한다. 환경변수 `CLEAN_PIPELINE_TYPE`으로 강제 지정 가능.

| TYPE | 조건 | 처리 방식 |
|---|---|---|
| **A** | 원본 크기 == 타겟 규격 | Pass-through — canonical 그대로 출력 |
| **G** | 그 외 (기본값) | PSD 레이어 분해 → 배경 생성 → 재합성 |

### TYPE G 흐름 (PSD 레이어 재배치)

```
P1  SOURCE_PREPARATION   — PSD 정규화 → RGBA canonical.png
P2  PSD_LAYER_READ       — 레이어 구조 파악 (role 분류: title/product/cta/bg …)
P3  BG_EXTRACT_OUTPAINT  — bg 레이어 추출 → Flux API로 타겟 규격 배경 생성
P4  ELEMENT_COMPOSITE    — 비-bg 레이어를 letterbox 좌표로 배경 위에 합성
                           → result.png + layout_result_{w}x{h}.json 저장
```

`layout_result_{w}x{h}.json`: P5 에디터가 레이어별 위치/크기를 로드하는 파일.

---

## 지원 입력 형식

| 형식 | 처리 방식 (P1) |
|---|---|
| `.psd` | psd-tools로 레이어 합성 → RGBA canonical 생성 |
| `.png` `.jpg` `.jpeg` | Pillow `Image.open()` → RGBA canonical 생성 |

> 형식은 P1 입력 처리 방식만 결정한다. P2~P8 처리 흐름은 형식에 무관하게 동일하다.
>
> WebP, GIF, TIFF, BMP는 지원하지 않는다 (P1에서 UNSUPPORTED_SOURCE_TYPE 반환).

---

## MongoDB 컬렉션

### `banner_job` — 작업 이력

| 필드 | 타입 | 설명 |
|---|---|---|
| `id` | String | ObjectId |
| `advertiser` | String | 광고주명 |
| `campaignName` | String | 캠페인명 |
| `specIds` | List\<String\> | 선택된 규격 ID 목록 |
| `targetMedia` | List\<String\> | specIds로부터 도출된 매체 목록 |
| `outputFormat` | String | png / jpg |
| `pipelineVersion` | String | `clean_v1` (현재 유일한 파이프라인) |
| `status` | String | pending → processing → done / fail |
| `psdPath` | String | 업로드 파일 경로 |
| `zipPath` | String | 완성 ZIP 경로 |
| `results` | List | 생성 이미지 목록 |
| `errorMessage` | String | 실패 시 오류 메시지 |
| `createdAt` | DateTime | |
| `updatedAt` | DateTime | |

### `banner_ai_analysis` — AI 분석 이력

소재 업로드 시 선택적으로 실행되는 OpenAI Vision 분석 결과.

| 필드 | 타입 | 설명 |
|---|---|---|
| `creativeType` | String | `text_heavy` / `product_focused` / `balanced_mix` |
| `mainSubjectDescription` | String | 주요 피사체 한국어 설명 |
| `reason` | String | 분석 이유 (한국어) |
| `warnings` | List\<String\> | 주의사항 목록 |
| `confidence` | Double | 신뢰도 (0.0 ~ 1.0) |
| `createdAt` | DateTime | |

### `banner_spec` — 매체별 규격

| 필드 | 타입 | 설명 |
|---|---|---|
| `media` | String | google / meta / naver / kakao / linkedin / tiktok |
| `placementName` | String | 한글 지면명 |
| `slug` | String | 영문 식별자 (파일명용) |
| `width` / `height` | int | px |
| `active` | boolean | 활성 여부 |

---

## RabbitMQ

| 항목 | 값 |
|---|---|
| Exchange | `creative.banner` (Direct) |
| Queue | `creative.banner.queue` |
| Routing Key | `banner.generate` |
| VHost | `heeil.h3` |

---

## REST API

### 배너 생성 (사이트 → Spring Boot)

```
POST /api/banner/upload
Content-Type: multipart/form-data

psdFile         이미지 파일 (PSD · PNG · JPG · JPEG)
advertiser      광고주명
campaignName    캠페인명
specIds         규격 ID 목록 (복수 전송)
outputFormat    png | jpg  (기본: png)
pipelineVersion clean_v1  (기본값 — 생략 가능)
```

> `resizeMode`, `smartFitStrength`, `focalPosition`, `objectReflowEnabled` 등은 더 이상 사용하지 않는다.
> Worker가 clean_v1 파이프라인을 실행하므로 무시된다.

### 작업 조회 / 다운로드

```
GET    /api/banner/job/{id}                       단건 조회
GET    /api/banner/jobs                           전체 목록
DELETE /api/banner/job/{id}                       단건 삭제
DELETE /api/banner/jobs                           다건 삭제 (body: {"ids": [...]})
GET    /api/banner/job/{id}/preview/{filename}    이미지 미리보기
GET    /api/banner/job/{id}/image/{filename}      개별 이미지 다운로드
GET    /api/banner/job/{id}/download              ZIP 전체 다운로드
```

### P5 에디터 (TYPE G 전용)

```
GET  /api/banner/job/{id}/layout-result?fileName=  layout_result_{w}x{h}.json 반환
GET  /api/banner/job/{id}/layers-merged?fileName=  layers_merged_{w}x{h}.json 반환
GET  /api/banner/job/{id}/layer-file?path=         레이어 개별 PNG 반환
POST /api/banner/job/{id}/recomposite              레이어 위치 수정 후 재합성
```

### AI 소재 분석 (선택)

```
POST /api/banner/analyze
Content-Type: multipart/form-data

file    이미지 파일 (PNG · JPG)
```

- OpenAI `gpt-4.1-mini` Vision 사용
- 분석 결과는 생성에 자동 적용되지 않는다 (참고용 표시)

### 규격 관리

```
GET    /api/spec               전체 규격 목록
GET    /api/spec?media=naver   매체별 필터
POST   /api/spec               규격 등록
POST   /api/spec/init          기본 규격 일괄 삽입
DELETE /api/spec/{id}          규격 삭제
```

---

## Worker API (내부 전용)

Worker(`/generate`)는 Java Consumer가 MQ 처리 중 직접 호출한다.
사이트 UI가 직접 호출하지 않는다.

### POST /generate 요청 (Java → Worker)

```json
{
  "jobId":           "uuid",
  "psdPath":         "/app/storage/uploads/uuid_file.psd",
  "pipelineVersion": "clean_v1",
  "specs": [
    {
      "media":    "naver",
      "name":     "GFA 정사각형",
      "slug":     "gfa_square",
      "width":    1080,
      "height":   1080,
      "safeZone": { "left": 40, "top": 40, "right": 40, "bottom": 40 }
    }
  ]
}
```

### POST /generate 응답 — PASS

```json
{
  "jobId":   "uuid",
  "zipPath": "/app/storage/zips/uuid.zip",
  "count":   1,
  "results": [
    {
      "media":           "naver",
      "name":            "GFA 정사각형",
      "slug":            "gfa_square",
      "width":           1080,
      "height":          1080,
      "fileName":        "gfa_square.png",
      "filePath":        "/app/storage/outputs/uuid/gfa_square.png",
      "fileSize":        204800,
      "valid":           true,
      "renderSource":    "clean_pipeline",
      "fallbackUsed":    false,
      "pipelineVersion": "clean_v1"
    }
  ]
}
```

### POST /generate 응답 — FAIL

```json
{
  "jobId":  "uuid",
  "count":  1,
  "results": [
    {
      "filePath":        "",
      "fileName":        "",
      "valid":           false,
      "failedStage":     "SOURCE_PREPARATION",
      "failureCode":     "SOURCE_NOT_FOUND",
      "error":           "Source file not found: /app/storage/uploads/uuid_file.psd",
      "renderSource":    "clean_pipeline",
      "fallbackUsed":    false,
      "pipelineVersion": "clean_v1"
    }
  ]
}
```

FAIL 시 `valid=false`, `failedStage`, `failureCode`, `error` 필드로 원인을 파악한다.
`fallbackUsed`는 항상 `false` — legacy 경로로 재시도하지 않는다.

---

## 테스트

### clean_pipeline 핵심 테스트 (개발 품질 보증 기준)

```bash
# 프로젝트 루트에서 실행
python -m pytest tests/clean_pipeline/ -v

# 빠른 실행
python -m pytest tests/clean_pipeline/ -q
```

현재 범위: **182 tests** — P1~P8 각 단계, routing, 격리, fail-closed, E2E 계약 포함.

### Worker 직접 호출 (배포 후 보조 검증)

```bash
# Worker가 실행 중일 때 (컨테이너 또는 로컬 Flask)
WORKER_URL=http://localhost:5000 python scripts/worker_contract_smoke_test.py
```

> Worker 직접 호출은 배포 확인용 보조 수단이다.
> 개발 단계 품질 보증은 `tests/clean_pipeline/` 기준으로 한다.

---

## 서버 배포

### 초기 세팅

```bash
cd /opt/creative-resizer
git clone https://github.com/ChoiYoungWoo2197/creative-resizer.git .
docker compose build
docker compose up -d
```

### 업데이트

```bash
cd /opt/creative-resizer
git pull

# 워커(Python) 변경 시
docker compose build --no-cache creative-worker && docker compose up -d creative-worker

# 전체
docker compose build --no-cache && docker compose up -d
```

### 로그 확인

```bash
docker logs -f creative-api
docker logs -f creative-worker
docker logs -f creative-nginx
```

### 포트

| 서비스 | 호스트 포트 | 컨테이너 포트 |
|---|---|---|
| creative-nginx | 3001 | 80 |
| creative-api | 18081 | 8081 |
| creative-worker | 내부 전용 | 5000 |

---

## 스토리지

호스트 `/opt/creative-resizer/storage` ↔ 컨테이너 `/app/storage`

```
/app/storage/
├── uploads/    원본 파일 (UUID_파일명.확장자)
├── outputs/    생성 이미지 ({jobId}/매체_WxH.포맷)
└── zips/       다운로드 ZIP ({jobId}.zip)
```

---

## 환경 설정

접속 정보는 서버의 `/opt/creative-resizer/.env`에 저장. `docker compose`가 자동으로 읽어 컨테이너에 주입.

`.env.example` 참고:

```env
OPENAI_API_KEY=sk-proj-...
MONGODB_URI=mongodb+srv://<user>:<password>@cluster0.xxxx.mongodb.net/creative_resizer?...
RABBITMQ_HOST=192.168.x.x
RABBITMQ_PORT=5672
RABBITMQ_USERNAME=your_username
RABBITMQ_PASSWORD=your_password
RABBITMQ_VHOST=your.vhost
```

| 항목 | 설명 |
|---|---|
| `OPENAI_API_KEY` | OpenAI API 키 (Worker P2/P5/P6 파이프라인 + AI 분석 기능) |
| `MONGODB_URI` | MongoDB Atlas 연결 URI |
| `RABBITMQ_*` | RabbitMQ 연결 정보 |

---

## Apache 리버스 프록시 설정

```apache
<VirtualHost *:80>
    ServerName creative.heeil.com
    LimitRequestBody 209715200

    ProxyPass        / http://127.0.0.1:3001/
    ProxyPassReverse / http://127.0.0.1:3001/
</VirtualHost>
```

---

## 지원 매체 및 규격

`POST /api/spec/init` 으로 기본 규격 삽입. 이후 규격 관리 화면에서 추가/삭제 가능.

| 매체 | 주요 규격 |
|---|---|
| Google | 1200×628, 300×250, 728×90, 160×600, 320×50, 320×100 |
| Meta | 1080×1080, 1080×1350, 1080×1920 |
| Naver | 스마트채널 가로형(1200×628), PC 디스플레이(300×250), GFA 등 |
| Kakao | 피드, 배너, 네이티브 등 |
| LinkedIn | 1200×628, 1080×1080 |
| TikTok | 1080×1920 |
