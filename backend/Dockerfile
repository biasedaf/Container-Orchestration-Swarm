# Backend Dockerfile
# Builds a lightweight Python container for the Flask microservice
# This image will be replicated to 4 instances by Docker Swarm

FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install dependencies first (Docker layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app.py .

# Expose the Flask port
EXPOSE 5000

# Health check - Swarm uses this to monitor container health
# If this fails 3 times, Swarm will replace the container (self-healing)
HEALTHCHECK --interval=10s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/health')" || exit 1

# Run with Gunicorn for production performance
# 4 workers to handle concurrent requests efficiently
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--threads", "2", "app:app"]
