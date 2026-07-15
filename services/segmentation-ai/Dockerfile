# creative-segmentation-ai — 외부 AI 전용 이미지
# 기존 creative-worker 와 완전히 분리

# GPU 환경: FROM nvidia/cuda:12.1.0-cudnn8-runtime-ubuntu22.04
# CPU 환경 (기본):
FROM python:3.11-slim

WORKDIR /app

# 빌드 의존성
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 libgl1-mesa-glx \
    && rm -rf /var/lib/apt/lists/*

# 패키지 설치 (CPU 모드; GPU는 torch CUDA 버전으로 교체)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 소스 복사
COPY . .

# 모델 캐시 마운트 포인트
VOLUME ["/models"]

ENV PORT=8090
ENV FLASK_ENV=production
ENV CREATIVE_SEGMENTATION_DEVICE=auto
ENV MODELS_DIR=/models
# 서비스 시작 시 lazy load (첫 요청에 로드)
ENV PRELOAD_MODELS=false

EXPOSE 8090

CMD ["gunicorn", "--bind", "0.0.0.0:8090", "--workers", "1", \
     "--timeout", "180", "--threads", "2", "app:app"]
