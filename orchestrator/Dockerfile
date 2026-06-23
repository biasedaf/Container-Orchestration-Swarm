# Orchestrator Dockerfile
# Service Orchestrator (Algorithm 1) - monitors Swarm events and state
# Must be deployed on manager node with Docker socket access

FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY orchestrator.py .

EXPOSE 8081

# Health check
HEALTHCHECK --interval=15s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8081/health')" || exit 1

CMD ["python", "orchestrator.py"]
