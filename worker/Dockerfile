FROM python:3.11-slim

WORKDIR /app

# psd-tools / Pillow 네이티브 의존성
RUN apt-get update && apt-get install -y \
    libffi-dev \
    libjpeg-dev \
    zlib1g-dev \
    liblcms2-2 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5000
CMD ["python", "app.py"]
