FROM python:3.11-slim

# Install system dependencies for FFmpeg and voice
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libffi-dev \
    libnacl-dev \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Environment variables should be set in the cloud provider's dashboard
ENV PORT=8080

CMD ["python", "bot.py"]
