# creative-segmentation-ai — 외부 AI 전용 이미지
# 기존 creative-worker 와 완전히 분리

# GPU 환경: FROM nvidia/cuda:12.1.0-cudnn8-runtime-ubuntu22.04
# CPU 환경 (기본):
FROM python:3.11-slim-bookworm

# CPU/GPU 빌드 분기 (기본: cpu)
# 운영서버 검증 스크립트는 반드시 --build-arg TORCH_VARIANT=cpu 전달
ARG TORCH_VARIANT=cpu

WORKDIR /app

# 빌드 의존성 (libgl1: Debian Trixie 호환)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 libgl1 \
    && rm -rf /var/lib/apt/lists/*

# SAM 2 CUDA extension 빌드 비활성화
ENV SAM2_BUILD_CUDA=0
ENV SAM2_BUILD_ALLOW_ERRORS=1
ENV PIP_NO_CACHE_DIR=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

# ── 1단계: PyTorch 설치 (CPU/GPU 분기) ──────────────────────────────────────
#   CPU: download.pytorch.org/whl/cpu → nvidia-* / cuda-toolkit 설치 안 됨
#   GPU: 기본 PyPI (향후 CUDA 버전 명시 필요)
RUN if [ "${TORCH_VARIANT}" = "cpu" ]; then \
      pip install \
        --index-url https://download.pytorch.org/whl/cpu \
        torch==2.5.1 \
        torchvision==0.20.1; \
    else \
      pip install \
        torch==2.5.1 \
        torchvision==0.20.1; \
    fi

# ── 2단계: SAM 2 비-torch 의존성 설치 ───────────────────────────────────────
COPY requirements-cpu.txt .
RUN pip install -r requirements-cpu.txt

# ── 3단계: SAM 2 설치 (--no-deps: GPU torch 교체 방지) ──────────────────────
RUN SAM2_BUILD_CUDA=0 pip install --no-deps sam2

# ── 4단계: 나머지 의존성 설치 (torch/torchvision/sam2 제외) ─────────────────
COPY requirements.txt .
RUN pip install -r requirements.txt

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

# Hugging Face 캐시 설정
# hf-xet 비활성화: 일부 네트워크에서 signed chunk URL 전송 중 연결 종료 발생
ENV HF_HUB_DISABLE_XET=1
ENV HF_HUB_DOWNLOAD_TIMEOUT=1200
ENV HF_HUB_ETAG_TIMEOUT=60
ENV HF_HUB_DISABLE_TELEMETRY=1
ENV HF_HOME=/models/huggingface
ENV HF_HUB_CACHE=/models/huggingface/hub
ENV TRANSFORMERS_CACHE=/models/huggingface/transformers

EXPOSE 8090

CMD ["gunicorn", "--bind", "0.0.0.0:8090", "--workers", "1", \
     "--timeout", "180", "--threads", "2", "app:app"]
