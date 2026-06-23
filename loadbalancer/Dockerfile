# Load Balancer Dockerfile
# Memory-Based Load Balancer (Algorithm 2)
# Routes requests to backend container with lowest memory usage
# Requires Docker socket mount for stats access

FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY loadbalancer.py .

EXPOSE 8080

# Health check
HEALTHCHECK --interval=15s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/lb-health')" || exit 1

CMD ["python", "loadbalancer.py"]
