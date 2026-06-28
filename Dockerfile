# ── Base image: official Playwright Python image (Chromium pre-installed) ──
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Chromium browser + system deps
RUN playwright install chromium --with-deps

# Copy application code
COPY . .

# Create local fallback dirs (used when Render disk is NOT mounted)
# When the disk IS mounted at /data, these are overridden by the mount.
RUN mkdir -p /app/sessions /app/debug /data/sessions /data/debug || true

EXPOSE 10000

# Single worker (Playwright sync API is not thread-safe)
# 300s timeout to allow long comment-loading jobs
CMD ["gunicorn", "app:app", \
     "--bind", "0.0.0.0:10000", \
     "--workers", "1", \
     "--timeout", "300", \
     "--log-level", "info", \
     "--access-logfile", "-"]
