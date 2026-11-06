# DashLocal backend for Railway. Build context = repository root.
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

WORKDIR /app/backend
COPY deploy/railway/requirements.railway.txt /tmp/requirements.txt
RUN pip install --upgrade pip && pip install -r /tmp/requirements.txt

# Same /app/backend layout as today, so file paths in the code keep working
COPY backend/ /app/backend/
COPY store_screenshots/ /app/store_screenshots/
# Settings come from Railway Variables, never from a committed .env
RUN rm -f /app/backend/.env && rm -rf /app/backend/tests

EXPOSE 8001
# One process only: the background jobs (billing, emails, alerts) run inside it
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT:-8001} --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
