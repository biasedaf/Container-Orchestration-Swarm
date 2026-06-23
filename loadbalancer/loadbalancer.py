"""
Memory-Based Load Balancer - Algorithm 2 Implementation (Enhanced)
===================================================================
This service implements Algorithm 2 from the research paper:
"Load balancing and service discovery using Docker Swarm for
microservice based big data applications"

Algorithm 2: Memory-Aware Load Balancing (Enhanced with Fallback)
------------------------------------------------------------------
1. Periodically monitor memory consumption on each backend container
2. When a request arrives:
   a. Check memory usage of ALL backend containers
   b. Select the container with LOWEST memory utilization
   c. If ALL containers exceed memory threshold → FALLBACK to round-robin
      (original: return 503; enhanced: graceful degradation)
   d. Forward the request to the selected container
3. Log the routing decision (which container, why, memory at time of decision)
4. Track response times per container for latency-aware insights

Reference: Singh, N., Hamid, Y., et al. (2023)
"""

import os
import time
import socket
import threading
import json
from datetime import datetime
from collections import deque
from flask import Flask, jsonify, request, Response
import docker
import requests as http_requests

app = Flask(__name__)

# Container identity
CONTAINER_ID = socket.gethostname()
START_TIME = datetime.utcnow().isoformat()

# Configuration
BACKEND_SERVICE_NAME = os.environ.get("BACKEND_SERVICE", "cc_research_backend")
BACKEND_PORT = int(os.environ.get("BACKEND_PORT", 5000))
MEMORY_THRESHOLD_PERCENT = float(os.environ.get("MEMORY_THRESHOLD", 80.0))
STATS_POLL_INTERVAL = float(os.environ.get("STATS_INTERVAL", 2.0))
FALLBACK_ENABLED = os.environ.get("FALLBACK_ENABLED", "true").lower() == "true"

# Docker client
client = docker.from_env()

# State tracking
container_stats = {}  # {container_id: {memory_usage, memory_limit, memory_percent, ip, ...}}
routing_decisions = deque(maxlen=500)
total_requests_routed = 0
requests_per_container = {}
response_times = {}  # {container_id: deque of response times}
fallback_count = 0
rejected_count = 0

# Round-robin index for fallback mode
rr_index = 0

# Lock for thread safety
lock = threading.Lock()


def log_decision(container_id, memory_pct, reason, all_stats):
    """Log a routing decision."""
    decision = {
        "timestamp": datetime.utcnow().isoformat(),
        "selected_container": container_id[:12],
        "selected_memory_percent": round(memory_pct, 2),
        "reason": reason,
        "all_container_memory": {
            k[:12]: round(v.get("memory_percent", 0), 2) 
            for k, v in all_stats.items()
        }
    }
    with lock:
        routing_decisions.appendleft(decision)
    return decision


def discover_backends():
    """
    Discover all backend container IPs and their Docker container IDs.
    Uses Docker API to find containers belonging to the backend service.
    """
    try:
        containers = client.containers.list(
            filters={"label": f"com.docker.swarm.service.name={BACKEND_SERVICE_NAME}"}
        )
        backends = {}
        for container in containers:
            if container.status == "running":
                # Get container's IP on the overlay network
                networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})
                ip = None
                for net_name, net_config in networks.items():
                    if "app-network" in net_name or "cc_research" in net_name:
                        ip = net_config.get("IPAddress")
                        break
                
                if not ip:
                    # Fallback: use first available network IP
                    for net_name, net_config in networks.items():
                        ip = net_config.get("IPAddress")
                        if ip:
                            break
                
                if ip:
                    backends[container.id] = {
                        "ip": ip,
                        "name": container.name,
                        "short_id": container.short_id,
                    }
        return backends
    except Exception as e:
        print(f"[LB] Error discovering backends: {e}")
        return {}


def get_container_memory_stats():
    """
    Algorithm 2 - Core: Query Docker stats API for memory usage of each backend container.
    Returns memory usage percentage for each container.
    """
    try:
        backends = discover_backends()
        stats = {}
        
        for container_id, info in backends.items():
            try:
                container = client.containers.get(container_id)
                # Get stats (stream=False returns a single snapshot)
                raw_stats = container.stats(stream=False)
                
                # Calculate memory usage
                memory_stats = raw_stats.get("memory_stats", {})
                memory_usage = memory_stats.get("usage", 0)
                memory_limit = memory_stats.get("limit", 1)
                
                # Subtract cache for accurate "real" memory usage
                cache = memory_stats.get("stats", {}).get("cache", 0)
                actual_usage = memory_usage - cache
                
                memory_percent = (actual_usage / memory_limit) * 100 if memory_limit > 0 else 0
                
                # CPU stats
                cpu_stats = raw_stats.get("cpu_stats", {})
                precpu_stats = raw_stats.get("precpu_stats", {})
                cpu_delta = cpu_stats.get("cpu_usage", {}).get("total_usage", 0) - \
                           precpu_stats.get("cpu_usage", {}).get("total_usage", 0)
                system_delta = cpu_stats.get("system_cpu_usage", 0) - \
                              precpu_stats.get("system_cpu_usage", 0)
                num_cpus = len(cpu_stats.get("cpu_usage", {}).get("percpu_usage", [1]))
                cpu_percent = (cpu_delta / system_delta) * num_cpus * 100 if system_delta > 0 else 0
                
                stats[container_id] = {
                    "ip": info["ip"],
                    "name": info["name"],
                    "short_id": info["short_id"],
                    "memory_usage_bytes": actual_usage,
                    "memory_limit_bytes": memory_limit,
                    "memory_usage_mb": round(actual_usage / (1024 * 1024), 2),
                    "memory_limit_mb": round(memory_limit / (1024 * 1024), 2),
                    "memory_percent": round(memory_percent, 2),
                    "cpu_percent": round(cpu_percent, 2),
                    "last_updated": datetime.utcnow().isoformat(),
                }
            except Exception as e:
                print(f"[LB] Error getting stats for {container_id[:12]}: {e}")
        
        return stats
    except Exception as e:
        print(f"[LB] Error in memory stats collection: {e}")
        return {}


def poll_stats():
    """Background thread: periodically update container memory stats."""
    global container_stats
    print(f"[LB] Stats poller started (interval: {STATS_POLL_INTERVAL}s)")
    
    while True:
        try:
            new_stats = get_container_memory_stats()
            if new_stats:
                with lock:
                    container_stats = new_stats
        except Exception as e:
            print(f"[LB] Stats poll error: {e}")
        time.sleep(STATS_POLL_INTERVAL)


def select_backend():
    """
    Algorithm 2 - Routing Decision: Select the backend container 
    with the LOWEST memory utilization.
    
    Enhanced: Falls back to round-robin when all containers exceed threshold.
    
    Returns: (container_id, ip, memory_percent, reason) or (None, None, 0, error_reason)
    """
    global total_requests_routed, fallback_count, rejected_count, rr_index
    
    with lock:
        stats_snapshot = dict(container_stats)
    
    if not stats_snapshot:
        return None, None, 0, "No backend containers discovered"
    
    # Check if ALL containers are above the memory threshold
    all_above_threshold = all(
        s.get("memory_percent", 0) >= MEMORY_THRESHOLD_PERCENT 
        for s in stats_snapshot.values()
    )
    
    if all_above_threshold:
        if FALLBACK_ENABLED:
            # ENHANCED: Fall back to round-robin instead of rejecting
            fallback_count += 1
            total_requests_routed += 1
            container_ids = list(stats_snapshot.keys())
            selected_id = container_ids[rr_index % len(container_ids)]
            rr_index += 1
            selected = stats_snapshot[selected_id]
            
            short_id = selected_id[:12]
            requests_per_container[short_id] = requests_per_container.get(short_id, 0) + 1
            
            reason = f"FALLBACK round-robin (all above {MEMORY_THRESHOLD_PERCENT}% threshold)"
            log_decision(selected_id, selected["memory_percent"], reason, stats_snapshot)
            return selected_id, selected["ip"], selected["memory_percent"], reason
        else:
            # Original behavior: reject when all nodes are overloaded
            rejected_count += 1
            return None, None, 0, f"All containers above {MEMORY_THRESHOLD_PERCENT}% memory threshold"
    
    # Select container with LOWEST memory usage (Algorithm 2 core logic)
    selected_id = min(stats_snapshot, key=lambda k: stats_snapshot[k].get("memory_percent", 100))
    selected = stats_snapshot[selected_id]
    
    total_requests_routed += 1
    
    # Track per-container routing
    short_id = selected_id[:12]
    requests_per_container[short_id] = requests_per_container.get(short_id, 0) + 1
    
    reason = f"Lowest memory: {selected['memory_percent']}%"
    log_decision(selected_id, selected["memory_percent"], reason, stats_snapshot)
    
    return selected_id, selected["ip"], selected["memory_percent"], reason


def proxy_request(target_ip, path, container_id):
    """Forward the incoming request to the selected backend container."""
    target_url = f"http://{target_ip}:{BACKEND_PORT}{path}"
    
    try:
        start_time = time.time()
        # Forward the request with original method, headers, and body
        resp = http_requests.request(
            method=request.method,
            url=target_url,
            headers={k: v for k, v in request.headers if k.lower() != 'host'},
            data=request.get_data(),
            params=request.args,
            timeout=30,
            allow_redirects=False,
        )
        elapsed_ms = round((time.time() - start_time) * 1000, 1)
        
        # Track response time per container
        short_id = container_id[:12] if container_id else "unknown"
        if short_id not in response_times:
            response_times[short_id] = deque(maxlen=100)
        response_times[short_id].append(elapsed_ms)
        
        # Build the response
        excluded_headers = ['content-encoding', 'content-length', 'transfer-encoding', 'connection']
        headers = [(k, v) for k, v in resp.raw.headers.items() if k.lower() not in excluded_headers]
        headers.append(('X-LB-Container', short_id))
        headers.append(('X-LB-Response-Time', str(elapsed_ms)))
        
        return Response(resp.content, resp.status_code, headers)
    except Exception as e:
        return jsonify({
            "error": "Backend request failed",
            "detail": str(e),
            "target": target_url,
        }), 502


# ============================================
# REST API Endpoints
# ============================================

@app.route('/lb-status')
def lb_status():
    """Load balancer status - shows Algorithm 2 in action."""
    with lock:
        stats_snapshot = dict(container_stats)
    
    # Calculate avg response times
    avg_response_times = {}
    for cid, times in response_times.items():
        if times:
            sorted_times = sorted(times)
            avg_response_times[cid] = {
                "avg_ms": round(sum(times) / len(times), 1),
                "p50_ms": round(sorted_times[len(sorted_times) // 2], 1),
                "p95_ms": round(sorted_times[int(len(sorted_times) * 0.95)], 1) if len(sorted_times) > 1 else round(sorted_times[0], 1),
                "p99_ms": round(sorted_times[int(len(sorted_times) * 0.99)], 1) if len(sorted_times) > 1 else round(sorted_times[0], 1),
                "sample_count": len(times),
            }
    
    return jsonify({
        "algorithm": "Algorithm 2: Memory-Based Load Balancing (Enhanced with Fallback)",
        "description": "Routes requests to the container with lowest memory usage; falls back to round-robin if all overloaded",
        "memory_threshold_percent": MEMORY_THRESHOLD_PERCENT,
        "fallback_enabled": FALLBACK_ENABLED,
        "stats_poll_interval_seconds": STATS_POLL_INTERVAL,
        "total_requests_routed": total_requests_routed,
        "fallback_requests": fallback_count,
        "rejected_requests": rejected_count,
        "requests_per_container": requests_per_container,
        "response_times_per_container": avg_response_times,
        "container_memory_stats": {
            k[:12]: {
                "memory_percent": v.get("memory_percent", 0),
                "memory_usage_mb": v.get("memory_usage_mb", 0),
                "memory_limit_mb": v.get("memory_limit_mb", 0),
                "cpu_percent": v.get("cpu_percent", 0),
                "ip": v.get("ip", "unknown"),
                "name": v.get("name", "unknown"),
                "last_updated": v.get("last_updated", ""),
            }
            for k, v in stats_snapshot.items()
        },
        "recent_routing_decisions": list(routing_decisions)[:30],
        "container_id": CONTAINER_ID,
        "timestamp": datetime.utcnow().isoformat(),
    })


@app.route('/lb-health')
def lb_health():
    """Health check."""
    try:
        client.ping()
        docker_ok = True
    except Exception:
        docker_ok = False
    
    return jsonify({
        "status": "healthy" if docker_ok else "degraded",
        "docker_connection": docker_ok,
        "backends_discovered": len(container_stats),
        "total_routed": total_requests_routed,
        "fallback_count": fallback_count,
        "container_id": CONTAINER_ID,
    })


@app.route('/lb-config', methods=['GET', 'POST'])
def lb_config():
    """View or update LB configuration at runtime."""
    global MEMORY_THRESHOLD_PERCENT, FALLBACK_ENABLED

    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        if "memory_threshold" in data:
            MEMORY_THRESHOLD_PERCENT = float(data["memory_threshold"])
        if "fallback_enabled" in data:
            FALLBACK_ENABLED = bool(data["fallback_enabled"])
        return jsonify({"status": "updated", "memory_threshold": MEMORY_THRESHOLD_PERCENT, "fallback_enabled": FALLBACK_ENABLED})

    return jsonify({
        "memory_threshold_percent": MEMORY_THRESHOLD_PERCENT,
        "fallback_enabled": FALLBACK_ENABLED,
        "stats_poll_interval": STATS_POLL_INTERVAL,
        "backend_service": BACKEND_SERVICE_NAME,
        "backend_port": BACKEND_PORT,
    })


# ============================================
# Proxy Routes - Forward to selected backend
# ============================================

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def proxy(path):
    """
    Main proxy endpoint - implements Algorithm 2 routing.
    Every request goes through memory-based selection.
    """
    # Skip internal endpoints
    if path in ('lb-status', 'lb-health', 'lb-config'):
        return  # handled by explicit routes above
    
    # Algorithm 2: Select backend based on memory
    container_id, target_ip, memory_pct, reason = select_backend()
    
    if target_ip is None:
        # All containers overloaded or none discovered
        return jsonify({
            "error": "Service unavailable",
            "reason": reason,
            "algorithm": "Algorithm 2 - Memory threshold exceeded",
            "threshold": f"{MEMORY_THRESHOLD_PERCENT}%",
            "timestamp": datetime.utcnow().isoformat(),
        }), 503
    
    # Forward request to selected container
    request_path = f"/{path}" if path else "/"
    return proxy_request(target_ip, request_path, container_id)


if __name__ == '__main__':
    # Start stats polling thread
    stats_thread = threading.Thread(target=poll_stats, daemon=True)
    stats_thread.start()
    
    print(f"🔀 Memory-Based Load Balancer (Algorithm 2 — Enhanced) starting on container: {CONTAINER_ID}")
    print(f"   Backend service: {BACKEND_SERVICE_NAME}")
    print(f"   Memory threshold: {MEMORY_THRESHOLD_PERCENT}%")
    print(f"   Fallback mode: {'ENABLED (round-robin)' if FALLBACK_ENABLED else 'DISABLED (503 reject)'}")
    print(f"   Stats poll interval: {STATS_POLL_INTERVAL}s")
    
    app.run(host='0.0.0.0', port=8080, debug=False, threaded=True)
