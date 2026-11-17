FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Utilisateur sans privilèges
RUN useradd --create-home tdsi
USER tdsi

# Cloud Run fournit le port dans $PORT ; les en-têtes du proxy Firebase sont acceptés.
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips="*"
