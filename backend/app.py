"""
Backend Microservice for Docker Swarm Load Balancing Research
=============================================================
This Flask application simulates the "linkextractor" service from the paper.
It demonstrates a 3-service microservice architecture:
  - Nginx (frontend/load balancer)
  - Flask API (this service - scaled to 4 replicas)
  - Redis (cache/data store)

The Flask API communicates with Redis to demonstrate:
  1. Inter-service communication via Docker Swarm DNS (Service Discovery)
  2. Round-robin load balancing across 4 replicas
  3. Shared state across microservices via Redis cache

Reference Paper: "Load balancing and service discovery using Docker Swarm 
for microservice based big data applications" - Neelam Singh et al.
"""

import os
import re
import socket
import time
import math
import hashlib
from datetime import datetime
from urllib.request import urlopen
from urllib.error import URLError
from flask import Flask, jsonify, request
import redis

app = Flask(__name__)

# Container identification
CONTAINER_ID = socket.gethostname()
START_TIME = datetime.utcnow().isoformat()
REQUEST_COUNT = 0

# Redis connection - uses Docker Swarm DNS to discover the Redis service
# This is SERVICE DISCOVERY in action: we use the service name "redis",
# not a hardcoded IP address. Docker Swarm resolves it automatically.
redis_client = redis.Redis(host='redis', port=6379, decode_responses=True)


def get_redis_status():
    """Check if Redis is reachable (proves service discovery works)."""
    try:
        redis_client.ping()
        return True
    except Exception:
        return False


@app.route('/')
def home():
    """
    Main endpoint - Returns container identity and Redis status.
    Used to prove that:
    1. Load balancing distributes requests across containers
    2. Service discovery allows Flask to reach Redis by name
    """
    global REQUEST_COUNT
    REQUEST_COUNT += 1
    
    # Record this request in Redis (shared state across all 4 replicas)
    redis_available = get_redis_status()
    if redis_available:
        redis_client.incr('total_requests')
        redis_client.hincrby('requests_per_container', CONTAINER_ID, 1)
        total_across_all = redis_client.get('total_requests')
    else:
        total_across_all = "N/A (Redis unavailable)"
    
    return jsonify({
        "message": "Hello from Backend Service!",
        "container_id": CONTAINER_ID,
        "request_number": REQUEST_COUNT,
        "total_requests_all_replicas": total_across_all,
        "redis_connected": redis_available,
        "timestamp": datetime.utcnow().isoformat(),
        "uptime_since": START_TIME
    })


@app.route('/health')
def health():
    """
    Health check endpoint for Docker Swarm.
    Swarm uses this to determine if the container is healthy.
    If this fails, Swarm will replace the container (self-healing).
    """
    return jsonify({
        "status": "healthy",
        "container_id": CONTAINER_ID,
        "redis_connected": get_redis_status(),
        "uptime_since": START_TIME
    }), 200


@app.route('/extract')
def extract_links():
    """
    Link Extractor endpoint - Simulates the paper's 'linkextractor' service.
    Extracts links from a given URL and caches results in Redis.
    
    This demonstrates the full microservice flow:
    Client -> Nginx (LB) -> Flask API (1 of 4 replicas) -> Redis (cache)
    
    Query params:
        - url: The URL to extract links from (default: example page)
    """
    global REQUEST_COUNT
    REQUEST_COUNT += 1
    
    target_url = request.args.get('url', 'http://example.com')
    cache_key = f"links:{hashlib.md5(target_url.encode()).hexdigest()}"
    
    # Check Redis cache first (demonstrates inter-service communication)
    redis_available = get_redis_status()
    cached = False
    links = []
    
    if redis_available:
        cached_data = redis_client.get(cache_key)
        if cached_data:
            links = cached_data.split(',')
            cached = True
    
    if not cached:
        try:
            # Fetch the page and extract links
            response = urlopen(target_url, timeout=5)
            html = response.read().decode('utf-8', errors='ignore')
            # Simple regex to find href links
            links = re.findall(r'href=["\']([^"\']+)["\']', html)
            links = links[:20]  # Limit to 20 links
            
            # Cache in Redis for future requests (any replica can use this cache)
            if redis_available and links:
                redis_client.setex(cache_key, 300, ','.join(links))  # Cache for 5 min
        except (URLError, Exception) as e:
            links = [f"Error fetching {target_url}: {str(e)}"]
    
    return jsonify({
        "url": target_url,
        "links_found": len(links),
        "links": links,
        "cached": cached,
        "container_id": CONTAINER_ID,
        "redis_connected": redis_available,
        "timestamp": datetime.utcnow().isoformat()
    })


@app.route('/compute')
def compute():
    """
    CPU-intensive endpoint to simulate 'Big Data' workload.
    Performs heavy mathematical computations to stress-test
    the load balancing mechanism under real CPU load.
    
    Query params:
        - iterations: Number of computation cycles (default: 100000)
    """
    global REQUEST_COUNT
    REQUEST_COUNT += 1
    
    iterations = int(request.args.get('iterations', 100000))
    
    start_time = time.time()
    
    # Simulate CPU-intensive big data computation
    result = 0
    for i in range(iterations):
        result += math.sqrt(i) * math.sin(i) * math.cos(i)
    
    end_time = time.time()
    computation_time = round(end_time - start_time, 4)
    
    # Store computation result in Redis
    if get_redis_status():
        redis_client.hincrby('compute_requests_per_container', CONTAINER_ID, 1)
    
    return jsonify({
        "message": "Computation complete",
        "container_id": CONTAINER_ID,
        "iterations": iterations,
        "computation_time_seconds": computation_time,
        "result": round(result, 4),
        "request_number": REQUEST_COUNT,
        "timestamp": datetime.utcnow().isoformat()
    })


@app.route('/stats')
def stats():
    """
    Returns load distribution statistics from Redis.
    Shows how requests are distributed across all 4 backend replicas.
    This data is aggregated in Redis (shared across all replicas).
    """
    redis_available = get_redis_status()
    
    if redis_available:
        request_distribution = redis_client.hgetall('requests_per_container')
        compute_distribution = redis_client.hgetall('compute_requests_per_container')
        total = redis_client.get('total_requests')
    else:
        request_distribution = {}
        compute_distribution = {}
        total = 0
    
    return jsonify({
        "total_requests": total,
        "request_distribution_per_container": request_distribution,
        "compute_distribution_per_container": compute_distribution,
        "redis_connected": redis_available,
        "reporting_container": CONTAINER_ID,
        "timestamp": datetime.utcnow().isoformat()
    })


@app.route('/info')
def info():
    """
    Returns detailed container and system information.
    Useful for debugging and demonstrating service discovery.
    """
    # Demonstrate service discovery: resolve Redis IP via Docker DNS
    redis_ip = "unknown"
    try:
        redis_ip = socket.gethostbyname('redis')
    except socket.gaierror:
        redis_ip = "DNS resolution failed"
    
    return jsonify({
        "container_id": CONTAINER_ID,
        "hostname": socket.gethostname(),
        "ip_address": socket.gethostbyname(socket.gethostname()),
        "total_requests_served": REQUEST_COUNT,
        "uptime_since": START_TIME,
        "service_discovery": {
            "redis_service_name": "redis",
            "redis_resolved_ip": redis_ip,
            "note": "Docker Swarm DNS resolves 'redis' to the Redis container IP"
        }
    })


if __name__ == '__main__':
    print(f"🚀 Backend service starting on container: {CONTAINER_ID}")
    app.run(host='0.0.0.0', port=5000, debug=False)
