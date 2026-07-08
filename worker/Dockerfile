FROM python:3.11-slim

WORKDIR /app

# psd-tools 의존 네이티브 라이브러리
RUN apt-get update && apt-get install -y \
    libffi-dev \
    libjpeg-dev \
    zlib1g-dev \
    liblcms2-2 \
    imagemagick \
    && rm -rf /var/lib/apt/lists/*

# ImageMagick 기본 정책에서 PSD 읽기 허용
RUN sed -i 's/<policy domain="coder" rights="none" pattern="PS" \/>/<policy domain="coder" rights="read|write" pattern="PS" \/>/' /etc/ImageMagick-6/policy.xml 2>/dev/null || true && \
    sed -i 's/<policy domain="coder" rights="none" pattern="PSD" \/>/<policy domain="coder" rights="read|write" pattern="PSD" \/>/' /etc/ImageMagick-6/policy.xml 2>/dev/null || true

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5000
CMD ["python", "app.py"]
