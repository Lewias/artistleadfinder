# Cloud parser: the app's Lead Scout with a real Chromium on a virtual screen (Xvfb), the
# same browser the desktop app opens. Build context: this folder's Dockerfile,
# requirements.txt, start.sh and cloud_worker/, plus artist_lead_finder/ and the page
# scripts in scripts/ (see the README).
FROM mcr.microsoft.com/playwright/python:v1.57.0-noble
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 CLOUD_DATA=/data CLOUD_SCRIPTS=/app/scripts
RUN apt-get update -q && apt-get install -yq --no-install-recommends xvfb \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt
COPY artist_lead_finder ./artist_lead_finder
COPY cloud_worker ./cloud_worker
COPY scripts ./scripts
COPY start.sh ./start.sh
RUN mkdir -p /data && chown pwuser /data && chmod 755 /app/start.sh
USER pwuser
CMD ["/app/start.sh"]
