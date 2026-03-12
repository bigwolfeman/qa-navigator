FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 libx11-6 libxext6 libxrender1 libxkbcommon0 \
    libfreetype6 libnss3 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libgbm1 libpango-1.0-0 libcairo2 libasound2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY src/ src/

RUN pip install --no-cache-dir -e ".[server]" \
    && playwright install chromium

RUN mkdir -p /app/recordings /app/reports

ENV QA_NAV_HEADLESS=true \
    QA_NAV_SCREEN_WIDTH=1280 \
    QA_NAV_SCREEN_HEIGHT=900 \
    PYTHONUNBUFFERED=1

EXPOSE 8080

CMD ["uvicorn", "qa_navigator.server.app:app", "--host", "0.0.0.0", "--port", "8080"]
