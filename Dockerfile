FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY auction_bot/ ./auction_bot/
COPY run.py main.py ./
COPY .env.example .gitignore .dockerignore Dockerfile README.md RAILWAY.md railway.json requirements.txt ./

# Telegram long polling worker; no HTTP port or web healthcheck required.
CMD ["python", "-u", "run.py"]
