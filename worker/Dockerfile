FROM python:3.11-slim

WORKDIR /app

# psd-tools 의존 네이티브 라이브러리
RUN apt-get update && apt-get install -y \
    libffi-dev \
    libjpeg-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5000
CMD ["python", "app.py"]
