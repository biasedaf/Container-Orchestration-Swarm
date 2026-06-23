"""
Service Orchestrator - Algorithm 1 Implementation (Enhanced)
=============================================================
This service implements Algorithm 1 from the research paper:
"Load balancing and service discovery using Docker Swarm for 
microservice based big data applications"

Algorithm 1: Service Orchestration (Enhanced with Active Auto-Scaling)
-----------------------------------------------------------------------
1. Manager receives service deployment request
2. Manager creates tasks for each replica
3. Scheduler assigns tasks to available nodes
4. Overlay network connects all containers
5. Health monitor watches container liveness
6. On failure → reschedule task to healthy node
7. Maintain desired state (desired replicas == actual replicas)
8. [ENHANCED] Auto-scale replicas based on aggregate resource usage
9. [ENHANCED] Active health probing of backend containers

Reference: Singh, N., Hamid, Y., et al. (2023)
"""

import os
import time
import socket
import threading
from datetime import datetime, timedelta
from collections import deque
from flask import Flask, jsonify, request as flask_request
import docker
import requests as http_requests

app = Flask(__name__)

# Container identity
CONTAINER_ID = socket.gethostname()
START_TIME = datetime.utcnow().isoformat()

# Docker client - connects via mounted Docker socket
client = docker.from_env()

# Event log - stores the last 500 orchestration events
orchestration_events = deque(maxlen=500)

# Service state tracking
service_states = {}
node_info = {}
scheduling_decisions = deque(maxlen=200)
healing_events = deque(maxlen=100)

# Auto-scaling state
BACKEND_SERVICE_NAME = os.environ.get("BACKEND_SERVICE", "cc_research_backend")
SCALE_UP_THRESHOLD = float(os.environ.get("SCALE_UP_THRESHOLD", 70.0))
SCALE_DOWN_THRESHOLD = float(os.environ.get("SCALE_DOWN_THRESHOLD", 30.0))
SCALE_UP_COOLDOWN = int(os.environ.get("SCALE_UP_COOLDOWN", 30))
SCALE_DOWN_COOLDOWN = int(os.environ.get("SCALE_DOWN_COOLDOWN", 60))
MIN_REPLICAS = int(os.environ.get("MIN_REPLICAS", 2))
MAX_REPLICAS = int(os.environ.get("MAX_REPLICAS", 8))
AUTO_SCALE_ENABLED = os.environ.get("AUTO_SCALE_ENABLED", "true").lower() == "true"

scaling_history = deque(maxlen=100)
last_scale_up_time = None
last_scale_down_time = None
auto_scale_status = {
    "enabled": AUTO_SCALE_ENABLED,
    "current_replicas": 4,
    "avg_memory_percent": 0.0,
    "avg_cpu_percent": 0.0,
    "last_action": "none",
    "last_action_time": None,
}

# Health probe results
health_probe_results = {}

# Lock for thread safety
lock = threading.Lock()


def log_event(event_type, message, details=None):
    """Log an orchestration event with timestamp."""
    event = {
        "timestamp": datetime.utcnow().isoformat(),
        "type": event_type,
        "message": message,
        "details": details or {}
    }
    with lock:
        orchestration_events.appendleft(event)
    print(f"[ORCHESTRATOR] [{event_type}] {message}")
    return event


def get_swarm_info():
    """Get current Swarm cluster information."""
    try:
        info = client.info()
        swarm_info = info.get("Swarm", {})
        return {
            "node_id": swarm_info.get("NodeID", "unknown"),
            "node_addr": swarm_info.get("NodeAddr", "unknown"),
            "is_manager": swarm_info.get("ControlAvailable", False),
            "nodes": swarm_info.get("Nodes", 0),
            "managers": swarm_info.get("Managers", 0),
            "workers": swarm_info.get("Nodes", 0) - swarm_info.get("Managers", 0),
            "local_node_state": swarm_info.get("LocalNodeState", "unknown"),
        }
    except Exception as e:
        return {"error": str(e)}


def get_node_details():
    """Get details about all nodes in the Swarm cluster."""
    try:
        nodes = client.nodes.list()
        node_list = []
        for node in nodes:
            attrs = node.attrs
            spec = attrs.get("Spec", {})
            status = attrs.get("Status", {})
            manager_status = attrs.get("ManagerStatus", {})
            
            node_data = {
                "id": attrs.get("ID", "")[:12],
                "hostname": attrs.get("Description", {}).get("Hostname", "unknown"),
                "role": spec.get("Role", "unknown"),
                "availability": spec.get("Availability", "unknown"),
                "state": status.get("State", "unknown"),
                "addr": status.get("Addr", "unknown"),
                "is_leader": manager_status.get("Leader", False),
                "manager_reachability": manager_status.get("Reachability", "N/A"),
                "resources": {
                    "cpus": attrs.get("Description", {}).get("Resources", {}).get("NanoCPUs", 0) / 1e9,
                    "memory_gb": round(attrs.get("Description", {}).get("Resources", {}).get("MemoryBytes", 0) / (1024**3), 2),
                },
                "engine_version": attrs.get("Description", {}).get("Engine", {}).get("EngineVersion", "unknown"),
                "os": attrs.get("Description", {}).get("Platform", {}).get("OS", "unknown"),
                "arch": attrs.get("Description", {}).get("Platform", {}).get("Architecture", "unknown"),
            }
            node_list.append(node_data)
        return node_list
    except Exception as e:
        return [{"error": str(e)}]


def get_service_status():
    """
    Algorithm 1 - Core: Monitor desired vs actual state for all services.
    This is the heart of service orchestration.
    """
    try:
        services = client.services.list()
        status = []
        
        for service in services:
            attrs = service.attrs
            spec = attrs.get("Spec", {})
            service_name = spec.get("Name", "unknown")
            
            # Get desired replicas
            mode = spec.get("Mode", {})
            desired_replicas = mode.get("Replicated", {}).get("Replicas", 0)
            
            # Get actual running tasks
            tasks = service.tasks(filters={"desired-state": "running"})
            running_tasks = [t for t in tasks if t.get("Status", {}).get("State") == "running"]
            
            # Get task details including node assignments
            task_details = []
            for task in tasks:
                task_status = task.get("Status", {})
                task_data = {
                    "task_id": task.get("ID", "")[:12],
                    "state": task_status.get("State", "unknown"),
                    "desired_state": task.get("DesiredState", "unknown"),
                    "node_id": task.get("NodeID", "")[:12],
                    "container_id": task_status.get("ContainerStatus", {}).get("ContainerID", "")[:12],
                    "timestamp": task_status.get("Timestamp", ""),
                    "message": task_status.get("Message", ""),
                    "error": task_status.get("Err", ""),
                }
                task_details.append(task_data)
            
            # Determine health status
            state_match = len(running_tasks) == desired_replicas
            health = "healthy" if state_match else "converging" if running_tasks else "critical"
            
            # Track state changes
            prev_state = service_states.get(service_name, {})
            prev_running = prev_state.get("running_replicas", 0)
            
            if prev_running != len(running_tasks):
                if len(running_tasks) < prev_running:
                    log_event("FAILURE_DETECTED", 
                             f"Service '{service_name}': replicas dropped {prev_running} → {len(running_tasks)}",
                             {"service": service_name, "before": prev_running, "after": len(running_tasks)})
                elif len(running_tasks) > prev_running and prev_running < desired_replicas:
                    log_event("SELF_HEALING",
                             f"Service '{service_name}': replicas recovered {prev_running} → {len(running_tasks)}",
                             {"service": service_name, "before": prev_running, "after": len(running_tasks)})
                    healing_events.appendleft({
                        "timestamp": datetime.utcnow().isoformat(),
                        "service": service_name,
                        "from_replicas": prev_running,
                        "to_replicas": len(running_tasks),
                        "desired": desired_replicas,
                    })
            
            service_data = {
                "name": service_name,
                "desired_replicas": desired_replicas,
                "running_replicas": len(running_tasks),
                "total_tasks": len(tasks),
                "health": health,
                "state_match": state_match,
                "image": spec.get("TaskTemplate", {}).get("ContainerSpec", {}).get("Image", "unknown").split("@")[0],
                "tasks": task_details,
                "update_status": attrs.get("UpdateStatus", {}),
            }
            
            # Update tracked state
            service_states[service_name] = {
                "running_replicas": len(running_tasks),
                "desired_replicas": desired_replicas,
                "last_check": datetime.utcnow().isoformat(),
            }
            
            status.append(service_data)
        
        return status
    except Exception as e:
        return [{"error": str(e)}]


# ============================================
# AUTO-SCALING ENGINE (Algorithm 1 Enhancement)
# ============================================

def get_backend_resource_stats():
    """Collect aggregate CPU and memory stats for backend containers."""
    try:
        containers = client.containers.list(
            filters={"label": f"com.docker.swarm.service.name={BACKEND_SERVICE_NAME}"}
        )
        if not containers:
            return 0.0, 0.0, 0

        total_mem_pct = 0.0
        total_cpu_pct = 0.0
        count = 0

        for container in containers:
            try:
                if container.status != "running":
                    continue
                stats = container.stats(stream=False)
                # Memory
                mem = stats.get("memory_stats", {})
                usage = mem.get("usage", 0) - mem.get("stats", {}).get("cache", 0)
                limit = mem.get("limit", 1)
                mem_pct = (usage / limit) * 100 if limit > 0 else 0
                # CPU
                cpu = stats.get("cpu_stats", {})
                precpu = stats.get("precpu_stats", {})
                cpu_delta = cpu.get("cpu_usage", {}).get("total_usage", 0) - \
                           precpu.get("cpu_usage", {}).get("total_usage", 0)
                sys_delta = cpu.get("system_cpu_usage", 0) - precpu.get("system_cpu_usage", 0)
                ncpus = len(cpu.get("cpu_usage", {}).get("percpu_usage", [1]))
                cpu_pct = (cpu_delta / sys_delta) * ncpus * 100 if sys_delta > 0 else 0

                total_mem_pct += mem_pct
                total_cpu_pct += cpu_pct
                count += 1
            except Exception:
                continue

        if count == 0:
            return 0.0, 0.0, 0
        return total_mem_pct / count, total_cpu_pct / count, count
    except Exception as e:
        print(f"[ORCHESTRATOR] Error getting backend stats: {e}")
        return 0.0, 0.0, 0


def scale_service(new_replica_count, reason):
    """Scale the backend service to a new replica count."""
    global last_scale_up_time, last_scale_down_time
    try:
        service = client.services.get(BACKEND_SERVICE_NAME)
        current = service.attrs.get("Spec", {}).get("Mode", {}).get("Replicated", {}).get("Replicas", 4)

        if new_replica_count == current:
            return False

        service.scale(new_replica_count)

        now = datetime.utcnow()
        action = "SCALE_UP" if new_replica_count > current else "SCALE_DOWN"

        if action == "SCALE_UP":
            last_scale_up_time = now
        else:
            last_scale_down_time = now

        log_event(f"AUTO_{action}",
                  f"Backend scaled {current} → {new_replica_count} replicas ({reason})",
                  {"from": current, "to": new_replica_count, "reason": reason})

        scaling_history.appendleft({
            "timestamp": now.isoformat(),
            "action": action,
            "from_replicas": current,
            "to_replicas": new_replica_count,
            "reason": reason,
        })

        with lock:
            auto_scale_status["current_replicas"] = new_replica_count
            auto_scale_status["last_action"] = action
            auto_scale_status["last_action_time"] = now.isoformat()

        return True
    except Exception as e:
        log_event("SCALE_ERROR", f"Failed to scale: {e}")
        return False


def auto_scale_loop():
    """Background thread: evaluate auto-scaling every 10 seconds."""
    global last_scale_up_time, last_scale_down_time

    print(f"[ORCHESTRATOR] Auto-scaling engine started (up>{SCALE_UP_THRESHOLD}%, down<{SCALE_DOWN_THRESHOLD}%)")
    # Let services settle on startup
    time.sleep(30)

    while True:
        try:
            if not AUTO_SCALE_ENABLED:
                time.sleep(10)
                continue

            avg_mem, avg_cpu, count = get_backend_resource_stats()

            with lock:
                auto_scale_status["avg_memory_percent"] = round(avg_mem, 2)
                auto_scale_status["avg_cpu_percent"] = round(avg_cpu, 2)
                auto_scale_status["current_replicas"] = count

            now = datetime.utcnow()

            # Scale UP check
            if avg_mem > SCALE_UP_THRESHOLD and count < MAX_REPLICAS:
                can_scale = last_scale_up_time is None or \
                    (now - last_scale_up_time).total_seconds() > SCALE_UP_COOLDOWN
                if can_scale:
                    scale_service(count + 1, f"Avg memory {avg_mem:.1f}% > {SCALE_UP_THRESHOLD}% threshold")

            # Scale DOWN check
            elif avg_mem < SCALE_DOWN_THRESHOLD and count > MIN_REPLICAS:
                can_scale = last_scale_down_time is None or \
                    (now - last_scale_down_time).total_seconds() > SCALE_DOWN_COOLDOWN
                if can_scale:
                    scale_service(count - 1, f"Avg memory {avg_mem:.1f}% < {SCALE_DOWN_THRESHOLD}% threshold")

        except Exception as e:
            print(f"[ORCHESTRATOR] Auto-scale error: {e}")

        time.sleep(10)


def health_probe_loop():
    """Background thread: actively probe backend health via HTTP every 15s."""
    time.sleep(20)
    while True:
        try:
            containers = client.containers.list(
                filters={"label": f"com.docker.swarm.service.name={BACKEND_SERVICE_NAME}"}
            )
            for container in containers:
                cid = container.short_id
                try:
                    networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})
                    ip = None
                    for net_name, net_config in networks.items():
                        if "app-network" in net_name or "cc_research" in net_name:
                            ip = net_config.get("IPAddress")
                            break
                    if not ip:
                        for _, net_config in networks.items():
                            ip = net_config.get("IPAddress")
                            if ip:
                                break
                    if ip:
                        resp = http_requests.get(f"http://{ip}:5000/health", timeout=5)
                        health_probe_results[cid] = {
                            "status": "healthy" if resp.status_code == 200 else "unhealthy",
                            "status_code": resp.status_code,
                            "response_time_ms": round(resp.elapsed.total_seconds() * 1000, 1),
                            "last_checked": datetime.utcnow().isoformat(),
                        }
                    else:
                        health_probe_results[cid] = {"status": "unknown", "error": "No IP found"}
                except Exception as e:
                    health_probe_results[cid] = {
                        "status": "unhealthy",
                        "error": str(e),
                        "last_checked": datetime.utcnow().isoformat(),
                    }
        except Exception:
            pass
        time.sleep(15)


def watch_docker_events():
    """
    Algorithm 1 - Event Loop: Watch Docker Swarm events in real-time.
    Captures container lifecycle events to track orchestration decisions.
    """
    log_event("ORCHESTRATOR_START", "Service Orchestrator started - watching Swarm events")
    
    try:
        for event in client.events(decode=True):
            event_type = event.get("Type", "")
            action = event.get("Action", "")
            actor = event.get("Actor", {})
            attributes = actor.get("Attributes", {})
            
            # Filter for relevant orchestration events
            if event_type == "container":
                container_name = attributes.get("name", "unknown")
                service_name = attributes.get("com.docker.swarm.service.name", "")
                node_id = attributes.get("com.docker.swarm.node.id", "")[:12]
                
                if service_name:  # Only Swarm-managed containers
                    if action == "start":
                        log_event("CONTAINER_START",
                                 f"Container started: {container_name}",
                                 {"service": service_name, "node": node_id,
                                  "container": container_name})
                        scheduling_decisions.appendleft({
                            "timestamp": datetime.utcnow().isoformat(),
                            "action": "SCHEDULE",
                            "service": service_name,
                            "container": container_name,
                            "node_id": node_id,
                            "reason": "New task scheduled by Swarm manager"
                        })
                        
                    elif action == "die":
                        exit_code = attributes.get("exitCode", "unknown")
                        log_event("CONTAINER_DIE",
                                 f"Container died: {container_name} (exit code: {exit_code})",
                                 {"service": service_name, "node": node_id,
                                  "exit_code": exit_code, "container": container_name})
                        
                    elif action == "kill":
                        signal = attributes.get("signal", "unknown")
                        log_event("CONTAINER_KILL",
                                 f"Container killed: {container_name} (signal: {signal})",
                                 {"service": service_name, "node": node_id,
                                  "signal": signal, "container": container_name})
                        
                    elif action == "create":
                        log_event("CONTAINER_CREATE",
                                 f"Container created: {container_name}",
                                 {"service": service_name, "node": node_id,
                                  "container": container_name})
                        
            elif event_type == "service":
                service_name = attributes.get("name", "unknown")
                if action == "update":
                    log_event("SERVICE_UPDATE",
                             f"Service updated: {service_name}",
                             {"service": service_name})
                elif action == "create":
                    log_event("SERVICE_CREATE",
                             f"Service created: {service_name}",
                             {"service": service_name})
                elif action == "remove":
                    log_event("SERVICE_REMOVE",
                             f"Service removed: {service_name}",
                             {"service": service_name})
                             
            elif event_type == "node":
                node_name = attributes.get("name", "unknown")
                if action in ("update", "create", "remove"):
                    log_event(f"NODE_{action.upper()}",
                             f"Node {action}: {node_name}",
                             {"node": node_name})
                             
    except Exception as e:
        log_event("EVENT_ERROR", f"Event stream error: {str(e)}")


def periodic_state_check():
    """
    Algorithm 1 - State Reconciliation: Periodically check desired vs actual state.
    This runs every 5 seconds to detect drift.
    """
    while True:
        try:
            get_service_status()  # This also logs state changes
        except Exception as e:
            log_event("STATE_CHECK_ERROR", f"Error checking state: {str(e)}")
        time.sleep(5)


# ============================================
# REST API Endpoints
# ============================================

@app.route('/')
def home():
    """Overview of the orchestrator."""
    return jsonify({
        "service": "Service Orchestrator (Algorithm 1) — Enhanced with Auto-Scaling",
        "paper": "Load balancing and service discovery using Docker Swarm",
        "description": "Monitors Swarm orchestration: service scheduling, self-healing, state reconciliation, and auto-scaling",
        "container_id": CONTAINER_ID,
        "uptime_since": START_TIME,
        "endpoints": {
            "/": "This overview",
            "/orchestration-status": "Full Algorithm 1 status (services, nodes, events)",
            "/events": "Recent orchestration events",
            "/nodes": "Swarm node details",
            "/services": "Service desired vs actual state",
            "/healing": "Self-healing event log",
            "/scheduling": "Task scheduling decision log",
            "/auto-scaling-status": "Auto-scaling engine status",
            "/scaling-history": "Scaling decision history",
            "/health-probes": "Active health probe results",
            "/health": "Health check",
        }
    })


@app.route('/orchestration-status')
def orchestration_status():
    """Full Algorithm 1 status - the main endpoint."""
    swarm = get_swarm_info()
    nodes = get_node_details()
    services = get_service_status()
    
    all_healthy = all(s.get("state_match", False) for s in services if "error" not in s)
    
    return jsonify({
        "algorithm": "Algorithm 1: Service Orchestration (Enhanced with Auto-Scaling)",
        "cluster_health": "healthy" if all_healthy else "converging",
        "swarm": swarm,
        "nodes": nodes,
        "services": services,
        "auto_scaling": dict(auto_scale_status),
        "recent_events": list(orchestration_events)[:50],
        "recent_scheduling": list(scheduling_decisions)[:20],
        "recent_healing": list(healing_events)[:20],
        "recent_scaling": list(scaling_history)[:20],
        "timestamp": datetime.utcnow().isoformat(),
    })


@app.route('/events')
def events():
    """Recent orchestration events."""
    limit = int(os.environ.get("EVENT_LIMIT", 100))
    return jsonify({
        "total_events": len(orchestration_events),
        "events": list(orchestration_events)[:limit],
    })


@app.route('/nodes')
def nodes():
    """Swarm node details."""
    return jsonify({
        "swarm": get_swarm_info(),
        "nodes": get_node_details(),
        "timestamp": datetime.utcnow().isoformat(),
    })


@app.route('/services')
def services():
    """Service desired vs actual state."""
    return jsonify({
        "services": get_service_status(),
        "timestamp": datetime.utcnow().isoformat(),
    })


@app.route('/healing')
def healing():
    """Self-healing event log."""
    return jsonify({
        "total_healing_events": len(healing_events),
        "events": list(healing_events),
        "timestamp": datetime.utcnow().isoformat(),
    })


@app.route('/scheduling')
def scheduling():
    """Task scheduling decisions."""
    return jsonify({
        "total_decisions": len(scheduling_decisions),
        "decisions": list(scheduling_decisions),
        "timestamp": datetime.utcnow().isoformat(),
    })


@app.route('/auto-scaling-status')
def auto_scaling_status():
    """Auto-scaling engine status and configuration."""
    return jsonify({
        "auto_scaling": dict(auto_scale_status),
        "config": {
            "enabled": AUTO_SCALE_ENABLED,
            "scale_up_threshold_percent": SCALE_UP_THRESHOLD,
            "scale_down_threshold_percent": SCALE_DOWN_THRESHOLD,
            "scale_up_cooldown_seconds": SCALE_UP_COOLDOWN,
            "scale_down_cooldown_seconds": SCALE_DOWN_COOLDOWN,
            "min_replicas": MIN_REPLICAS,
            "max_replicas": MAX_REPLICAS,
            "backend_service": BACKEND_SERVICE_NAME,
        },
        "recent_scaling": list(scaling_history)[:20],
        "timestamp": datetime.utcnow().isoformat(),
    })


@app.route('/scaling-history')
def scaling_hist():
    """Full scaling decision history."""
    return jsonify({
        "total_scaling_events": len(scaling_history),
        "events": list(scaling_history),
        "timestamp": datetime.utcnow().isoformat(),
    })


@app.route('/health-probes')
def health_probes():
    """Active health probe results for all backend containers."""
    return jsonify({
        "probes": dict(health_probe_results),
        "total_containers_probed": len(health_probe_results),
        "timestamp": datetime.utcnow().isoformat(),
    })


@app.route('/health')
def health():
    """Health check endpoint."""
    try:
        client.ping()
        docker_ok = True
    except Exception:
        docker_ok = False
    
    return jsonify({
        "status": "healthy" if docker_ok else "degraded",
        "docker_connection": docker_ok,
        "container_id": CONTAINER_ID,
        "events_captured": len(orchestration_events),
        "auto_scaling_enabled": AUTO_SCALE_ENABLED,
    })


if __name__ == '__main__':
    # Start event watcher in background thread
    event_thread = threading.Thread(target=watch_docker_events, daemon=True)
    event_thread.start()
    
    # Start periodic state checker in background thread
    state_thread = threading.Thread(target=periodic_state_check, daemon=True)
    state_thread.start()
    
    # Start auto-scaling engine
    scale_thread = threading.Thread(target=auto_scale_loop, daemon=True)
    scale_thread.start()

    # Start active health prober
    probe_thread = threading.Thread(target=health_probe_loop, daemon=True)
    probe_thread.start()
    
    log_event("ORCHESTRATOR_READY", f"Orchestrator API ready on container {CONTAINER_ID}")
    
    print(f"🎯 Service Orchestrator (Algorithm 1 — Enhanced) starting on container: {CONTAINER_ID}")
    print(f"   Auto-scaling: {'ENABLED' if AUTO_SCALE_ENABLED else 'DISABLED'}")
    print(f"   Scale up threshold: {SCALE_UP_THRESHOLD}%  |  Scale down: {SCALE_DOWN_THRESHOLD}%")
    print(f"   Replicas range: {MIN_REPLICAS} — {MAX_REPLICAS}")
    app.run(host='0.0.0.0', port=8081, debug=False, threaded=True)
