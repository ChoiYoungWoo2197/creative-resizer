# Creative Resizer

이미지(PSD, PNG, JPG, WebP 등)를 업로드하면 Google, Meta, Naver, Kakao 등 주요 광고 매체의 규격에 맞는 배너를 자동 생성하는 내부 툴.

서비스 URL: **https://creative.heeil.com**

---

## 기술 스택

| 영역 | 기술 |
|---|---|
| 프론트엔드 | Vue 3 + Element Plus + Vite |
| API 서버 | Spring Boot 3.3.5 (Java 17) |
| 이미지 처리 | Python 3.11 + psd-tools + Pillow |
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
│   ├── nginx.conf                      # client_max_body_size 200m
│   ├── vite.config.js                  # canvas stub alias (ag-psd 호환)
│   └── src/
│       ├── App.vue                     # 다크 헤더 레이아웃
│       ├── views/
│       │   ├── UploadView.vue          # 배너 생성 (2패널 레이아웃)
│       │   ├── JobListView.vue         # 작업 목록 (통계 카드 + 필터 + 테이블)
│       │   └── SpecView.vue            # 규격 관리
│       └── api/banner.js               # Axios API 클라이언트
│
├── src/main/java/com/h3/creative/
│   ├── api/BannerController.java       # REST 엔드포인트
│   ├── config/RabbitConfig.java        # Exchange/Queue 설정
│   ├── domain/
│   │   ├── BannerJob.java              # 작업 이력 도큐먼트 (specIds 포함)
│   │   └── BannerSpec.java             # 매체별 규격 도큐먼트
│   ├── mongo/
│   │   ├── BannerMongoService.java
│   │   └── SpecMongoService.java       # findByIds() 지원
│   ├── queue/
│   │   ├── message/BannerMessage.java  # specIds 포함
│   │   ├── producer/BannerProducer.java
│   │   └── consumer/BannerConsumer.java
│   └── service/BannerService.java
│
├── src/main/resources/application.yml  # 프로파일: default / local / prod
│
└── worker/
    ├── Dockerfile
    ├── requirements.txt
    ├── app.py                          # Flask (POST /generate, GET /health)
    └── resizer.py                      # PSD·이미지 로딩 + 리사이즈 로직
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
  │              POST /generate
  │              PSD → psd-tools, 이미지 → Pillow
  │              규격별 리사이즈 → ZIP 생성
  │
  ▼
공유 볼륨 (/opt/creative-resizer/storage ↔ /app/storage)
  ├── uploads/   원본 파일
  ├── outputs/   생성 이미지
  └── zips/      다운로드 ZIP
```

---

## 지원 입력 형식

| 형식 | 처리 방식 |
|---|---|
| `.psd` | psd-tools로 레이어 합성 후 Pillow 처리 |
| `.png` `.jpg` `.jpeg` `.webp` `.gif` `.tiff` `.bmp` | Pillow `Image.open()` 직접 처리 |

> CMYK·P·LAB 색상 모드는 자동으로 RGBA 변환 후 처리

---

## 리사이즈 모드

| 모드 | 설명 |
|---|---|
| `smart-fit` | 스마트 맞춤 — 원본 전체를 최대한 유지하고 남는 영역은 블러 배경으로 자연스럽게 확장 **(기본값)** |
| `cover` | 꽉 채우기 — 비율 유지, 넘치는 부분 잘림 |
| `contain` | 전체 보이기 — 비율 유지, 남는 영역 흰색 |
| `blur-bg` | 원본 비율 유지 + 남은 영역 블러 배경 |

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
| `resizeMode` | String | cover / contain / blur-bg / smart-fit |
| `outputFormat` | String | png / jpg / webp |
| `status` | String | pending → processing → done / fail |
| `psdPath` | String | 업로드 파일 경로 |
| `zipPath` | String | 완성 ZIP 경로 |
| `results` | List | 생성 이미지 목록 (media, name, slug, width, height, fileName, filePath) |
| `errorMessage` | String | 실패 시 오류 메시지 |
| `createdAt` | DateTime | |
| `updatedAt` | DateTime | |

### `banner_spec` — 매체별 규격

| 필드 | 타입 | 설명 |
|---|---|---|
| `id` | String | ObjectId |
| `media` | String | google / meta / naver / kakao / linkedin / tiktok |
| `placementName` | String | 한글 지면명 |
| `slug` | String | 영문 식별자 (파일명용, e.g. smartchannel_horizontal) |
| `width` | int | px |
| `height` | int | px |
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

### 배너 생성

```
POST /api/banner/upload
Content-Type: multipart/form-data

psdFile       이미지 파일 (PSD·PNG·JPG·WebP·GIF 등)
advertiser    광고주명
campaignName  캠페인명
specIds       규격 ID 목록 (복수 전송)
resizeMode    cover | contain | blur-bg | smart-fit  (기본: smart-fit)
outputFormat  png | jpg | webp          (기본: png)
```

### 작업 조회

```
GET /api/banner/job/{id}                       단건 조회
GET /api/banner/jobs                           전체 목록
GET /api/banner/job/{id}/preview/{filename}    이미지 미리보기 (image/png·jpeg·webp)
GET /api/banner/job/{id}/image/{filename}      개별 이미지 다운로드 (attachment)
GET /api/banner/job/{id}/download              ZIP 전체 다운로드
```

### 규격 관리

```
GET    /api/spec                  전체 규격 목록
GET    /api/spec?media=naver      매체별 필터
POST   /api/spec                  규격 등록
POST   /api/spec/init             기본 규격 일괄 삽입 (?reset=true 시 전체 초기화)
DELETE /api/spec/{id}             규격 삭제
```

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

# 프론트만 변경 시
docker compose build --no-cache creative-nginx && docker compose up -d creative-nginx

# 백엔드(Java) 변경 시
docker compose build --no-cache creative-api && docker compose up -d creative-api

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

`application.yml` `prod` 프로파일에 실제 값 포함 (`.env` 미사용).  
서버에서는 `SPRING_PROFILES_ACTIVE=prod` 환경변수만 설정.

주요 설정값:

| 항목 | 설명 |
|---|---|
| `spring.data.mongodb.uri` | MongoDB Atlas URI |
| `spring.rabbitmq.*` | RabbitMQ 연결 정보 |
| `spring.servlet.multipart.max-file-size` | 200MB |
| `creative.worker.url` | `http://creative-worker:5000` |
| `creative.storage.upload-dir` | `/app/storage/uploads` |

---

## Apache 리버스 프록시 설정

`/etc/httpd/conf.d/vhost.conf`에 추가:

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
