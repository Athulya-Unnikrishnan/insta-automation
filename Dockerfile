# ── Base image: official Playwright Python image with Chromium pre-installed ──
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

# Set working directory
WORKDIR /app

# Install Python dependencies first (layer-cache friendly)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install only Chromium (already included in base image, but run to be safe)
RUN playwright install chromium --with-deps

# Copy application code
COPY . .

# Create sessions and debug directories (will be overridden by Render disk mount)
RUN mkdir -p /data/sessions /data/debug

# Render uses port 10000 by default
EXPOSE 10000

# Use gunicorn with a single worker (Playwright is not thread-safe)
# Timeout set high because comment scraping can take 60-120 seconds
CMD ["gunicorn", "app:app", \
     "--bind", "0.0.0.0:10000", \
     "--workers", "1", \
     "--timeout", "300", \
     "--log-level", "info"]
