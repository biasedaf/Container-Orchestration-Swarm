# Docker Swarm Load Balancing & Service Discovery

**Research Paper Implementation** — *"Load balancing and service discovery using Docker Swarm for microservice based big data applications"*

> **Authors**: Neelam Singh, Yasir Hamid, Sapna Juneja, Gautam Srivastava, Gaurav Dhiman, Thippa Reddy Gadekallu, Mohd Asif Shah  
> **Published in**: *Journal of Cloud Computing* (2023)

---

## Table of Contents

- [Project Overview](#project-overview)
- [Concepts Practiced](#concepts-practiced)
- [System Architecture](#system-architecture)
- [Service Discovery](#service-discovery)
- [Deployment Guide](#deployment-guide)
  - [Single-Node Deployment](#single-node-deployment)
  - [Multi-Node Simulation (DinD)](#multi-node-simulation-dind)
  - [Physical Multi-Node Setup](#physical-multi-node-setup)
- [API Documentation](#api-documentation)
  - [Backend API (Flask)](#backend-api-flask)
  - [Load Balancer API (Algorithm 2)](#load-balancer-api-algorithm-2)
  - [Orchestrator API (Algorithm 1)](#orchestrator-api-algorithm-1)
  - [Nginx Frontend](#nginx-frontend)
- [Environment Variable Reference](#environment-variable-reference)
- [Algorithm Deep Dives](#algorithm-deep-dives)
  - [Algorithm 1: Service Orchestration & Auto-Scaling](#algorithm-1-service-orchestration--auto-scaling)
  - [Algorithm 2: Memory-Based Load Balancing](#algorithm-2-memory-based-load-balancing)
- [Dashboard Guide](#dashboard-guide)
- [Testing & Validation](#testing--validation)
- [Configuration & Tuning](#configuration--tuning)
- [Project Structure](#project-structure)
- [Troubleshooting](#troubleshooting)
- [Monitoring & Observability](#monitoring--observability)
- [Cleanup](#cleanup)
- [Citation](#citation)

---

## Project Overview

This project implements a **practical, production-grade microservice architecture** deployed on **Docker Swarm** that demonstrates four key research capabilities from the paper:

### 1. Active Service Orchestration (Algorithm 1)

A dedicated **Orchestrator** service monitors real-time Docker Swarm events (container start/die/kill, service updates, node changes) and maintains desired vs. actual state reconciliation. It includes:

- **Auto-scaling**: Dynamically adjusts backend replicas between 2 and 8 based on aggregate memory usage. Scales up when average memory exceeds 70%, scales down when below 30%.
- **Health probing**: Actively HTTP-probes each backend container's `/health` endpoint every 15 seconds.
- **Self-healing detection**: Monitors Swarm events for container failures and logs recovery actions.

### 2. Memory-Based Load Balancing (Algorithm 2)

A dedicated **Load Balancer** replaces Nginx's built-in round-robin with intelligent routing:

- Queries Docker stats API every 2 seconds for memory usage of all backend containers.
- Routes each incoming request to the container with the **lowest memory utilization**.
- **Graceful fallback**: If all containers exceed 80% memory, falls back to round-robin (enhancement over the paper's 503 rejection).
- Tracks per-container request counts, response time percentiles (P50/P95/P99), and routing decisions.

### 3. Fault Tolerance (Self-Healing)

Docker Swarm's built-in self-healing automatically replaces killed containers. The orchestrator tracks every failure and recovery event in real time. Test scripts measure recovery times (typically <10 seconds).

### 4. Service Discovery

All containers communicate via Docker Swarm's internal DNS overlay network using service names (`backend`, `redis`, `loadbalancer`, `orchestrator`) instead of hardcoded IP addresses. This is demonstrated explicitly in the backend's `/info` endpoint which resolves the `redis` hostname to its container IP.

---

## Concepts Practiced

This project was built to practice and demonstrate the following **distributed systems and container orchestration concepts**:

### Docker Swarm Orchestration
- Services, tasks, and replica management
- Desired-state reconciliation (Swarm maintains declared replica counts)
- Node roles (manager vs. worker) and scheduling constraints
- Rolling updates and restart policies

### Service Discovery via Overlay Networking
- Docker Swarm's built-in DNS resolution (`<service_name>` resolves to container IPs)
- Overlay networks enable cross-host communication
- Containers discover each other dynamically without hardcoded IPs

### Memory-Aware Load Balancing
- Real-time resource monitoring via Docker stats API
- Weighted selection algorithms based on live metrics
- Graceful degradation under load (fallback strategies)
- Response time tracking and latency percentiles

### Auto-Scaling Based on Resource Metrics
- Threshold-based scaling policies (scale up at 70%, down at 30%)
- Cooldown periods to prevent thrashing
- Aggregate metric computation across replicas
- Integration with Docker SDK for programmatic scaling

### Container Health Checks & Self-Healing
- Docker HEALTHCHECK directives at container and service levels
- Automatic container replacement on failure
- Event-driven monitoring of container lifecycle

### Multi-Container Microservice Architecture
- Separation of concerns (frontend, load balancer, orchestrator, backend, cache)
- Inter-service communication patterns
- Shared state via Redis across replicas

### Docker SDK for Python
- Programmatic access to Docker API (container stats, events, service management)
- Real-time event streaming with `client.events()`
- Service scaling via `service.scale(replicas)`

### Concurrent Programming
- Threading model with daemon threads for background monitoring
- Thread-safe data access with `threading.Lock()`
- Bounded memory usage with `collections.deque(maxlen=N)`

### Distributed State Management
- Redis as a shared counter store across replicas
- Global request counting and per-container distribution tracking
- Distributed caching with TTL (link extraction results)

### Load Testing & Chaos Engineering
- Async load generation with aiohttp
- Chaos testing (intentional container killing)
- Stress testing with concurrent and CPU-intensive workloads
- Metrics collection and Markdown report generation

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Client Browser / curl                                                  │
│  http://localhost:8888                                                  │
└───────────────────────────┬─────────────────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────────────┐
│                      Nginx Frontend (1 replica)                       │
│  ┌─────────────────────────────────────────────────────────────────┐  │
│  │  location /        → dashboard.html  (static HTML)              │  │
│  │  location /api/    → http://loadbalancer:8080  (Algorithm 2)    │  │
│  │  location /lb/     → http://loadbalancer:8080  (LB status API)  │  │
│  │  location /orchestrator/ → http://orchestrator:8081 (Algo 1)    │  │
│  │  resolver 127.0.0.11 (Docker DNS)                               │  │
│  └─────────────────────────────────────────────────────────────────┘  │
└───────────────────────┬────────────────────────┬────────────────────────┘
                        │                        │
            ┌───────────▼───────────┐    ┌───────▼────────────┐
            │  Load Balancer :8080  │    │  Orchestrator :8081 │
            │  (Algorithm 2)        │    │  (Algorithm 1)     │
            │                       │    │                     │
            │  - Memory stats poll  │    │  - Docker events    │
            │  - Lowest-mem routing │    │  - State check (5s) │
            │  - Round-robin fallbk │    │  - Auto-scale (10s) │
            │  - Docker socket mnt  │    │  - Health probe(15s)│
            └───────────┬───────────┘    └───────┬─────────────┘
                        │                        │
                        │           ┌─────────────┘
                        ▼           ▼
               ┌──────────────────────────┐
               │    Docker Swarm API      │
               │  (/var/run/docker.sock)  │
               └──────────────────────────┘
                        │
            ┌───────────┼───────────┬───────────┐
            ▼           ▼           ▼           ▼
       ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐
       │ Backend│ │ Backend│ │ Backend│ │ Backend│
       │ Flask  │ │ Flask  │ │ Flask  │ │ Flask  │
       │ :5000  │ │ :5000  │ │ :5000  │ │ :5000  │
       │ #1     │ │ #2     │ │ #3     │ │ #4     │
       └───┬────┘ └───┬────┘ └───┬────┘ └───┬────┘
           └──────────┼──────────┼───────────┘
                      ▼          ▼
                 ┌────────────────────┐
                 │   Redis :6379      │
                 │  (Shared Counter)  │
                 └────────────────────┘
```

### Component Responsibilities

| Component | Technology | Role | Constraints |
|-----------|-----------|------|-------------|
| **frontend** | Nginx (alpine) | Serves dashboard, reverse proxy to LB/orchestrator | Manager node |
| **loadbalancer** | Python Flask | Memory-based routing, Docker stats polling | Manager node, Docker socket |
| **orchestrator** | Python Flask | Event monitoring, auto-scaling, health probes | Manager node, Docker socket |
| **backend** | Python Flask + Gunicorn | Microservice API (4 endpoints), Redis client | Spread across nodes |
| **redis** | redis:alpine | Shared state, request counters, cache | Any node |

### Request Flow

1. Browser sends `GET http://localhost:8888/api/` to Nginx
2. Nginx matches `/api/` and proxies to `http://loadbalancer:8080/` (resolved via Docker DNS)
3. Load Balancer calls `select_backend()` → queries latest memory stats snapshot
4. LB selects container with lowest memory % (or uses round-robin fallback)
5. LB proxies request to `http://<backend-ip>:5000/`
6. Backend Flask increments request counters in Redis (via `redis:6379` DNS)
7. Backend returns response → LB adds `X-LB-Container` and `X-LB-Response-Time` headers → Nginx → Browser

---

## Service Discovery

All inter-service communication uses **Docker Swarm's built-in DNS resolution** on the overlay network (`app-network`). Services resolve each other by service name:

| Service Name | DNS Resolves To | Used By |
|-------------|----------------|---------|
| `redis` | Redis container IP | Backend (4 replicas) |
| `loadbalancer` | LB container IP | Nginx frontend |
| `orchestrator` | Orchestrator container IP | Nginx frontend |
| `backend` | All backend task IPs | Load balancer (via Docker API label filter, not DNS) |

### Code Examples

**Backend connecting to Redis via DNS** (`backend/app.py:41`):
```python
redis_client = redis.Redis(host='redis', port=6379, decode_responses=True)
```

**Backend resolving Redis IP** (`backend/app.py:229-233`):
```python
redis_ip = socket.gethostbyname('redis')
```

**Nginx proxying via DNS** (`frontend/nginx.conf:33-34`):
```nginx
set $lb_service http://loadbalancer:8080;
proxy_pass $lb_service;
```

**Load balancer discovering backends via Docker labels** (`loadbalancer/loadbalancer.py:89-91`):
```python
containers = client.containers.list(
    filters={"label": f"com.docker.swarm.service.name={BACKEND_SERVICE_NAME}"}
)
```

---

## Deployment Guide

### Prerequisites

- **Docker Engine** 24+ with Swarm mode
- **Docker Compose** v2+
- **Python 3.8+** (for the load generator script)
- At least **8 GB RAM** recommended

### Single-Node Deployment

```bash
# Step 1: Initialize Docker Swarm
docker swarm init

# Step 2: Build the Docker images
docker-compose build

# Step 3: Deploy the stack to Swarm
docker stack deploy -c docker-compose.yml cc_research

# Step 4: Verify deployment
docker service ls

# Step 5: Test the application
curl http://localhost:8888/api/

# Step 6: Open the dashboard
# Browse to http://localhost:8888/
```

### Multi-Node Simulation (Docker-in-Docker)

Simulate a 3-node Swarm cluster (1 manager + 2 workers) on a single machine:

```bash
# Run the full automated setup (up → init → deploy → status)
bash scripts/simulate_multinode.sh full

# Or run steps individually:
bash scripts/simulate_multinode.sh up       # Start 3 DinD containers
bash scripts/simulate_multinode.sh init     # Init Swarm, join workers
bash scripts/simulate_multinode.sh deploy   # Build images, deploy stack
bash scripts/simulate_multinode.sh status   # Check cluster state
bash scripts/simulate_multinode.sh test     # Send test requests

# Access the dashboard at http://localhost:18888/

# Tear down
bash scripts/simulate_multinode.sh down
```

The DinD architecture:
- **manager1** (port 12375 Docker API, 18888 app) — Runs frontend, LB, orchestrator
- **worker1** — Runs backend replicas + redis
- **worker2** — Runs backend replicas

### Physical Multi-Node Setup

```bash
# On the manager node:
bash scripts/setup_multinode.sh manager

# On each worker node:
bash scripts/setup_multinode.sh worker <MANAGER_IP> <TOKEN>
```

---

## API Documentation

### Backend API (Flask)

**Service**: `backend` — Port `5000` (internal only, accessed via load balancer)  
**Image**: `cc-research-backend`  
**Replicas**: 4 (auto-scaled 2–8)

#### `GET /` — Home / Container Identity

Returns the container's identity, request count, and Redis connectivity. Proves load balancing distributes requests and service discovery works.

**Response**:
```json
{
  "message": "Hello from Backend Service!",
  "container_id": "a1b2c3d4e5f6",
  "request_number": 42,
  "total_requests_all_replicas": "156",
  "redis_connected": true,
  "timestamp": "2026-06-23T12:00:00.000000",
  "uptime_since": "2026-06-23T11:30:00.000000"
}
```

#### `GET /health` — Health Check

Used by Docker Swarm's HEALTHCHECK directive. If this fails 3 times, Swarm replaces the container.

**Response** (200):
```json
{
  "status": "healthy",
  "container_id": "a1b2c3d4e5f6",
  "redis_connected": true,
  "uptime_since": "2026-06-23T11:30:00.000000"
}
```

#### `GET /extract?url=<url>` — Link Extractor

Simulates the paper's "linkextractor" service. Fetches a URL, extracts href links, caches in Redis (5-minute TTL).

**Example**: `GET /extract?url=http://example.com`

**Response**:
```json
{
  "url": "http://example.com",
  "links_found": 20,
  "links": ["https://www.iana.org/domains/example", "..."],
  "cached": false,
  "container_id": "a1b2c3d4e5f6",
  "redis_connected": true,
  "timestamp": "2026-06-23T12:00:00.000000"
}
```

#### `GET /compute?iterations=<n>` — CPU-Intensive Computation

Performs `math.sqrt(i) * math.sin(i) * math.cos(i)` for `n` iterations (default: 100,000). Tests CPU workload distribution.

**Example**: `GET /compute?iterations=50000`

**Response**:
```json
{
  "message": "Computation complete",
  "container_id": "a1b2c3d4e5f6",
  "iterations": 50000,
  "computation_time_seconds": 0.4231,
  "result": -12.3456,
  "request_number": 7,
  "timestamp": "2026-06-23T12:00:00.000000"
}
```

#### `GET /stats` — Load Distribution Statistics

Returns aggregated request counts from Redis across all backend replicas.

**Response**:
```json
{
  "total_requests": "156",
  "request_distribution_per_container": {
    "a1b2c3d4e5f6": "42",
    "b2c3d4e5f6a1": "38",
    "c3d4e5f6a1b2": "40",
    "d4e5f6a1b2c3": "36"
  },
  "compute_distribution_per_container": {
    "a1b2c3d4e5f6": "12"
  },
  "redis_connected": true,
  "reporting_container": "a1b2c3d4e5f6",
  "timestamp": "2026-06-23T12:00:00.000000"
}
```

#### `GET /info` — Container & Service Discovery Info

Shows detailed container metadata and explicitly demonstrates DNS-based service discovery.

**Response**:
```json
{
  "container_id": "a1b2c3d4e5f6",
  "hostname": "a1b2c3d4e5f6",
  "ip_address": "10.0.1.8",
  "total_requests_served": 42,
  "uptime_since": "2026-06-23T11:30:00.000000",
  "service_discovery": {
    "redis_service_name": "redis",
    "redis_resolved_ip": "10.0.1.2",
    "note": "Docker Swarm DNS resolves 'redis' to the Redis container IP"
  }
}
```

---

### Load Balancer API (Algorithm 2)

**Service**: `loadbalancer` — Port `8080` (internal only)  
**Image**: `cc-research-loadbalancer`  
**Replicas**: 1 (manager node, Docker socket mount required)

#### `GET /lb-status` — Full Load Balancer Status

Shows Algorithm 2's complete internal state: memory stats per container, routing decisions, response time percentiles, fallback counts.

**Response**:
```json
{
  "algorithm": "Algorithm 2: Memory-Based Load Balancing (Enhanced with Fallback)",
  "description": "Routes requests to the container with lowest memory usage; falls back to round-robin if all overloaded",
  "memory_threshold_percent": 80.0,
  "fallback_enabled": true,
  "stats_poll_interval_seconds": 2.0,
  "total_requests_routed": 500,
  "fallback_requests": 3,
  "rejected_requests": 0,
  "requests_per_container": {
    "a1b2c3d4e5f6": 142,
    "b2c3d4e5f6a1": 120,
    "c3d4e5f6a1b2": 130,
    "d4e5f6a1b2c3": 108
  },
  "response_times_per_container": {
    "a1b2c3d4e5f6": {
      "avg_ms": 12.5,
      "p50_ms": 8.2,
      "p95_ms": 35.1,
      "p99_ms": 89.4,
      "sample_count": 100
    }
  },
  "container_memory_stats": {
    "a1b2c3d4e5f6": {
      "memory_percent": 22.3,
      "memory_usage_mb": 56.8,
      "memory_limit_mb": 256,
      "cpu_percent": 3.1,
      "ip": "10.0.1.8",
      "name": "cc_research_backend.1.abc123",
      "last_updated": "2026-06-23T12:00:00.000000"
    }
  },
  "recent_routing_decisions": [
    {
      "timestamp": "2026-06-23T12:00:00.000000",
      "selected_container": "a1b2c3d4e5f6",
      "selected_memory_percent": 22.3,
      "reason": "Lowest memory: 22.3%",
      "all_container_memory": {
        "a1b2c3d4e5f6": 22.3,
        "b2c3d4e5f6a1": 35.1,
        "c3d4e5f6a1b2": 41.2,
        "d4e5f6a1b2c3": 28.7
      }
    }
  ]
}
```

#### `GET /lb-health` — Health Check

**Response**:
```json
{
  "status": "healthy",
  "docker_connection": true,
  "backends_discovered": 4,
  "total_routed": 500,
  "fallback_count": 3,
  "container_id": "loadbalancer-container-id"
}
```

#### `GET /lb-config` — View Configuration

**Response**:
```json
{
  "memory_threshold_percent": 80.0,
  "fallback_enabled": true,
  "stats_poll_interval": 2.0,
  "backend_service": "cc_research_backend",
  "backend_port": 5000
}
```

#### `POST /lb-config` — Update Configuration at Runtime

**Request**:
```json
{
  "memory_threshold": 85.0,
  "fallback_enabled": false
}
```

**Response**:
```json
{
  "status": "updated",
  "memory_threshold": 85.0,
  "fallback_enabled": false
}
```

#### `GET /*` — Main Proxy (Algorithm 2 Routing)

Every request to any path (except `/lb-*`) triggers the memory-based routing algorithm. The request is forwarded to the backend with lowest memory.

**Headers added to response**:
- `X-LB-Container`: Container ID of the selected backend
- `X-LB-Response-Time`: Time in ms for the backend to respond

**Response** (same as backend's response for the proxied path).

When all containers exceed threshold and fallback is **disabled**:
```json
{
  "error": "Service unavailable",
  "reason": "All containers above 80.0% memory threshold",
  "algorithm": "Algorithm 2 - Memory threshold exceeded",
  "threshold": "80.0%",
  "timestamp": "2026-06-23T12:00:00.000000"
}
```
Status: **503 Service Unavailable**

---

### Orchestrator API (Algorithm 1)

**Service**: `orchestrator` — Port `8081` (internal only)  
**Image**: `cc-research-orchestrator`  
**Replicas**: 1 (manager node, Docker socket mount required)

#### `GET /` — Overview

```json
{
  "service": "Service Orchestrator (Algorithm 1) — Enhanced with Auto-Scaling",
  "paper": "Load balancing and service discovery using Docker Swarm",
  "description": "Monitors Swarm orchestration: service scheduling, self-healing, state reconciliation, and auto-scaling",
  "container_id": "orch-container-id",
  "uptime_since": "2026-06-23T11:30:00.000000",
  "endpoints": {
    "/": "This overview",
    "/orchestration-status": "Full Algorithm 1 status",
    "/events": "Recent orchestration events",
    "/nodes": "Swarm node details",
    "/services": "Service desired vs actual state",
    "/healing": "Self-healing event log",
    "/scheduling": "Task scheduling decision log",
    "/auto-scaling-status": "Auto-scaling engine status",
    "/scaling-history": "Scaling decision history",
    "/health-probes": "Active health probe results",
    "/health": "Health check"
  }
}
```

#### `GET /orchestration-status` — Full Status

```json
{
  "algorithm": "Algorithm 1: Service Orchestration (Enhanced with Auto-Scaling)",
  "cluster_health": "healthy",
  "swarm": {
    "node_id": "abc123...",
    "node_addr": "192.168.1.10",
    "is_manager": true,
    "nodes": 3,
    "managers": 1,
    "workers": 2,
    "local_node_state": "active"
  },
  "nodes": [
    {
      "id": "node1...",
      "hostname": "manager1",
      "role": "manager",
      "state": "ready",
      "resources": { "cpus": 8.0, "memory_gb": 32.0 }
    }
  ],
  "services": [
    {
      "name": "cc_research_backend",
      "desired_replicas": 4,
      "running_replicas": 4,
      "health": "healthy",
      "state_match": true
    }
  ],
  "auto_scaling": {
    "enabled": true,
    "current_replicas": 4,
    "avg_memory_percent": 45.2,
    "avg_cpu_percent": 12.3,
    "last_action": "none",
    "last_action_time": null
  },
  "recent_events": [],
  "recent_scheduling": [],
  "recent_healing": [],
  "recent_scaling": []
}
```

#### `GET /events` — Orchestration Events

```json
{
  "total_events": 45,
  "events": [
    {
      "timestamp": "2026-06-23T11:35:00.000000",
      "type": "CONTAINER_START",
      "message": "Container started: cc_research_backend.4.xyz789",
      "details": {
        "service": "cc_research_backend",
        "node": "node1...",
        "container": "cc_research_backend.4.xyz789"
      }
    }
  ]
}
```

Event types: `ORCHESTRATOR_START`, `CONTAINER_START`, `CONTAINER_DIE`, `CONTAINER_KILL`, `CONTAINER_CREATE`, `FAILURE_DETECTED`, `SELF_HEALING`, `AUTO_SCALE_UP`, `AUTO_SCALE_DOWN`, `SCALE_ERROR`, `SERVICE_UPDATE`, `SERVICE_CREATE`, `SERVICE_REMOVE`, `NODE_UPDATE`, `NODE_CREATE`, `NODE_REMOVE`.

#### `GET /services` — Service State

```json
{
  "services": [
    {
      "name": "cc_research_backend",
      "desired_replicas": 4,
      "running_replicas": 4,
      "total_tasks": 4,
      "health": "healthy",
      "state_match": true,
      "image": "cc-research-backend:latest",
      "tasks": [
        {
          "task_id": "task1...",
          "state": "running",
          "desired_state": "running",
          "node_id": "node1...",
          "container_id": "a1b2c3d4e5f6",
          "timestamp": "2026-06-23T11:30:00.000000"
        }
      ]
    }
  ]
}
```

#### `GET /nodes` — Swarm Nodes

```json
{
  "swarm": { "node_id": "...", "nodes": 3, "managers": 1, "workers": 2 },
  "nodes": [
    {
      "id": "node1...",
      "hostname": "worker1",
      "role": "worker",
      "availability": "active",
      "state": "ready",
      "resources": { "cpus": 8.0, "memory_gb": 32.0 }
    }
  ]
}
```

#### `GET /healing` — Self-Healing Events

```json
{
  "total_healing_events": 2,
  "events": [
    {
      "timestamp": "2026-06-23T11:40:00.000000",
      "service": "cc_research_backend",
      "from_replicas": 3,
      "to_replicas": 4,
      "desired": 4
    }
  ]
}
```

#### `GET /scheduling` — Task Scheduling Decisions

```json
{
  "total_decisions": 15,
  "decisions": [
    {
      "timestamp": "2026-06-23T11:30:00.000000",
      "action": "SCHEDULE",
      "service": "cc_research_backend",
      "container": "cc_research_backend.1.abc123",
      "node_id": "node1...",
      "reason": "New task scheduled by Swarm manager"
    }
  ]
}
```

#### `GET /auto-scaling-status` — Auto-Scaling Engine

```json
{
  "auto_scaling": {
    "enabled": true,
    "current_replicas": 4,
    "avg_memory_percent": 45.2,
    "avg_cpu_percent": 12.3,
    "last_action": "SCALE_UP",
    "last_action_time": "2026-06-23T11:35:00.000000"
  },
  "config": {
    "enabled": true,
    "scale_up_threshold_percent": 70.0,
    "scale_down_threshold_percent": 30.0,
    "scale_up_cooldown_seconds": 30,
    "scale_down_cooldown_seconds": 60,
    "min_replicas": 2,
    "max_replicas": 8,
    "backend_service": "cc_research_backend"
  },
  "recent_scaling": [
    {
      "timestamp": "2026-06-23T11:35:00.000000",
      "action": "SCALE_UP",
      "from_replicas": 3,
      "to_replicas": 4,
      "reason": "Avg memory 75.3% > 70.0% threshold"
    }
  ]
}
```

#### `GET /scaling-history` — Full Scaling History

```json
{
  "total_scaling_events": 5,
  "events": [
    {
      "timestamp": "2026-06-23T11:35:00.000000",
      "action": "SCALE_UP",
      "from_replicas": 3,
      "to_replicas": 4,
      "reason": "Avg memory 75.3% > 70.0% threshold"
    }
  ]
}
```

#### `GET /health-probes` — Active Health Probe Results

```json
{
  "probes": {
    "a1b2c3d4e5f6": {
      "status": "healthy",
      "status_code": 200,
      "response_time_ms": 4.2,
      "last_checked": "2026-06-23T12:00:00.000000"
    },
    "b2c3d4e5f6a1": {
      "status": "unhealthy",
      "error": "Connection refused",
      "last_checked": "2026-06-23T12:00:00.000000"
    }
  },
  "total_containers_probed": 4
}
```

#### `GET /health` — Health Check

```json
{
  "status": "healthy",
  "docker_connection": true,
  "container_id": "orch-container-id",
  "events_captured": 45,
  "auto_scaling_enabled": true
}
```

---

### Nginx Frontend

**Service**: `frontend` — Port `8888:80` (external)  
**Image**: `cc-research-frontend`  
**Replicas**: 1 (manager node)

| Endpoint | Target | Purpose |
|----------|--------|---------|
| `GET /` | `dashboard.html` | Real-time monitoring dashboard |
| `GET /api/*` | `loadbalancer:8080` | Algorithm 2 proxied routing |
| `GET /lb/*` | `loadbalancer:8080` | LB status API |
| `GET /orchestrator/*` | `orchestrator:8081` | Algorithm 1 API |
| `GET /nginx-health` | `return 200` | Nginx health check |
| `GET /nginx-status` | `stub_status` | Nginx connection stats |

---

## Environment Variable Reference

### Load Balancer (Algorithm 2)

| Variable | Default | Description | Range |
|----------|---------|-------------|-------|
| `BACKEND_SERVICE` | `cc_research_backend` | Swarm service name to route to | Docker service name |
| `BACKEND_PORT` | `5000` | Backend container port | 1–65535 |
| `MEMORY_THRESHOLD` | `80.0` | Memory % above which fallback triggers | 0.0–100.0 |
| `STATS_INTERVAL` | `2.0` | Seconds between Docker stats polls | 0.5–30.0 |
| `FALLBACK_ENABLED` | `true` | Enable round-robin fallback when all containers saturated | `true`/`false` |

### Orchestrator (Algorithm 1)

| Variable | Default | Description | Range |
|----------|---------|-------------|-------|
| `BACKEND_SERVICE` | `cc_research_backend` | Service to monitor and scale | Docker service name |
| `AUTO_SCALE_ENABLED` | `true` | Enable auto-scaling engine | `true`/`false` |
| `SCALE_UP_THRESHOLD` | `70.0` | Avg memory % to trigger scale up | 0.0–100.0 |
| `SCALE_DOWN_THRESHOLD` | `30.0` | Avg memory % to trigger scale down | 0.0–100.0 |
| `SCALE_UP_COOLDOWN` | `30` | Seconds to wait between scale ups | 0–300 |
| `SCALE_DOWN_COOLDOWN` | `60` | Seconds to wait between scale downs | 0–300 |
| `MIN_REPLICAS` | `2` | Minimum backend replica count | 1–MAX_REPLICAS |
| `MAX_REPLICAS` | `8` | Maximum backend replica count | MIN_REPLICAS–100 |

### Backend

| Variable | Default | Description | Defined in |
|----------|---------|-------------|-----------|
| `FLASK_ENV` | `production` | Flask environment | App code |
| `REDIS_HOST` | `redis` | Redis service DNS name | Code constant |

---

## Algorithm Deep Dives

### Algorithm 1: Service Orchestration & Auto-Scaling

**File**: `orchestrator/orchestrator.py` (691 lines)

#### Core Responsibilities

1. **Event Monitoring** — Watches Docker events in real-time via `client.events(decode=True)` stream
2. **State Reconciliation** — Every 5 seconds, compares desired vs. actual replica counts
3. **Auto-Scaling** — Every 10 seconds, evaluates memory usage and scales up/down
4. **Health Probing** — Every 15 seconds, HTTP-probes each backend's `/health` endpoint

#### Auto-Scaling Decision Logic

```
                    ┌─────────────────────┐
                    │  Collect avg memory │
                    │  & CPU from all     │
                    │  backend containers │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │  avg_mem >          │  YES
                    │  SCALE_UP_THRESHOLD │─────► ┌──────────────────┐
                    │  (70%)?             │       │ count < MAX_REPL │────────► SCALE UP (+1)
                    └──────────┬──────────┘       │ AND cooldown met │
                               │ NO               └──────────────────┘
                    ┌──────────▼──────────┐
                    │  avg_mem <          │  YES
                    │  SCALE_DOWN_        │─────► ┌──────────────────┐
                    │  THRESHOLD (30%)?   │       │ count > MIN_REPL │────────► SCALE DOWN (-1)
                    └──────────┬──────────┘       │ AND cooldown met │
                               │ NO               └──────────────────┘
                               ▼
                      Wait 10 seconds
```

#### Key Implementation Details

- **Scale UP cooldown**: 30 seconds (prevents rapid oscillations)
- **Scale DOWN cooldown**: 60 seconds (more conservative on scale-down)
- **Initial settle time**: 30 seconds on startup before any scaling action
- **Memory metric**: Actual usage = `usage - cache` (subtracts page cache for accuracy)
- **CPU metric**: Computed from delta between consecutive stats snapshots

#### Enhanced Features Beyond the Paper

- **Active auto-scaling**: The paper describes basic orchestration; this adds proactive resource-based scaling
- **Health probing**: Active HTTP health checks complement Swarm's built-in HEALTHCHECK
- **API endpoints**: 11 REST endpoints expose full internal state for monitoring

### Algorithm 2: Memory-Based Load Balancing

**File**: `loadbalancer/loadbalancer.py` (431 lines)

#### Core Logic

```
                    ┌────────────────────────┐
                    │  Background thread:    │
                    │  poll Docker stats API │
                    │  every STATS_INTERVAL  │
                    └──────────┬─────────────┘
                               │
                    ┌──────────▼─────────────┐
                    │  Request arrives at    │
                    │  proxy endpoint        │
                    └──────────┬─────────────┘
                               │
                    ┌──────────▼─────────────┐
                    │  Take snapshot of      │
                    │  container_stats       │
                    └──────────┬─────────────┘
                               │
                    ┌──────────▼─────────────┐
                    │  Any backends          │  NO
                    │  discovered?           │────► Return error
                    └──────────┬─────────────┘
                               │ YES
                    ┌──────────▼─────────────┐
                    │  ALL containers >      │
                    │  MEMORY_THRESHOLD?     │
                    └──────────┬─────────────┘
                    ┌──────────┼─────────────┐
                    │ YES      │              │ NO
                    ▼          │              ▼
            ┌───────────┐      │      ┌──────────────┐
            │ Fallback  │      │      │ Select        │
            │ enabled?  │      │      │ container with│
            └─────┬─────┘      │      │ LOWEST memory │
              YES │    NO      │      └──────┬───────┘
                  ▼            ▼             │
          ┌────────────┐ ┌──────────┐        │
          │ Round-robin│ │ Return   │        │
          │ fallback   │ │ 503      │        │
          └─────┬──────┘ └──────────┘        │
                │                            │
                └──────────┬─────────────────┘
                           ▼
                ┌─────────────────────┐
                │  Proxy request to   │
                │  selected container │
                │  + track metrics    │
                └─────────────────────┘
```

#### Memory Stats Collection

Each 2-second poll cycle:
```python
raw_stats = container.stats(stream=False)
memory_usage = raw_stats["memory_stats"]["usage"]
memory_limit = raw_stats["memory_stats"]["limit"]
cache = raw_stats["memory_stats"]["stats"]["cache"]
actual_usage = memory_usage - cache  # Subtract cache for "real" usage
memory_percent = (actual_usage / memory_limit) * 100
```

#### Enhanced Feature: Graceful Fallback

The paper's original Algorithm 2 returns **503 Service Unavailable** when all containers exceed the memory threshold. This implementation adds optional **round-robin fallback** (`FALLBACK_ENABLED=true`), providing graceful degradation instead of complete rejection — more practical for production environments.

#### Routing Decision Tracking

Each decision is logged with:
- Timestamp
- Selected container and its memory %
- Reason for selection (lowest memory / fallback round-robin)
- Snapshot of all container memory percentages at decision time

#### Response Time Tracking

Per-container latency percentiles (P50, P95, P99) computed from the last 100 response times, providing insight into which containers are responding fastest.

---

## Dashboard Guide

The real-time monitoring dashboard at `http://localhost:8888/` is a single-page HTML/JS app (`frontend/dashboard.html`, 454 lines) that auto-refreshes every 3–10 seconds.

### Section 1: Algorithm 1 — Active Auto-Scaling Engine

| Card | Data Source | Description |
|------|-----------|-------------|
| **Backend Replicas** | `/orchestrator/auto-scaling-status` | Current replica count (2–8 range displayed) |
| **Cluster Avg Memory** | `/orchestrator/auto-scaling-status` | Average memory across all backends (green/rose/cyan) |
| **Last Scaling Action** | `/orchestrator/auto-scaling-status` | Most recent SCALE_UP/SCALE_DOWN with timestamp |
| **Health Probes** | `/orchestrator/health-probes` | Number of containers actively monitored |

### Section 2: Overview Cards

| Card | Data Source | Description |
|------|-----------|-------------|
| **Total Requests** | `/api/stats` | Sum of all requests across all containers (from Redis) |
| **Active Containers** | `/lb/lb-status` | Number of backend containers discovered by LB |
| **Redis Status** | `/api/` | Redis connectivity (green/red indicator) |
| **LB Algorithm** | `/lb/lb-status` | Currently active algorithm |

### Section 3: Algorithm 2 — Memory-Based Load Balancing

Per-container memory usage cards with color-coded progress bars:
- **Green** (<40%): Healthy, preferred routing target
- **Amber** (40–70%): Moderate usage
- **Red** (>70%): High usage, near threshold
- Each card shows: memory %, absolute usage/limit MB, CPU %

Fallback status tag shows:
- `Fallback: ENABLED (N used)` — with count of fallback decisions
- `Fallback: DISABLED (N rejected)` — if disabled, shows rejection count

### Section 4: Container Load Distribution

Shows request count per container with percentage of total and P50 latency. Container cards flash briefly when they receive a request (via the "Send 20/100 Requests" buttons).

### Section 5: Real-Time Charts

- **Pie Chart** (Doughnut): Request distribution across containers
- **Line Chart**: Memory % over time (last 30 samples, one line per container)

### Section 6: Live Testing Controls

| Button | Action | API Call |
|--------|--------|----------|
| Send 20 Requests | Sends 20 sequential requests | `GET /api/` × 20 |
| Send 100 Requests | Sends 100 sequential requests | `GET /api/` × 100 |
| Test Link Extractor | Tests link extraction with caching | `GET /api/extract?url=http://example.com` |
| Reset Stats | Clears local state and charts | Client-side only |

### Section 7: Orchestrator Event Log

Color-coded real-time events from Algorithm 1:
- **Green**: `CONTAINER_START`, `CONTAINER_CREATE`, `SELF_HEALING`, `AUTO_SCALE_UP/DOWN`
- **Red**: `CONTAINER_DIE`, `CONTAINER_KILL`, `FAILURE_DETECTED`
- **Amber**: Service/node updates

### Section 8: Service Discovery Map

Table showing all 5 services with DNS names, replica counts, and assigned algorithms. Backend replica count updates dynamically from auto-scaling status.

---

## Testing & Validation

### Load Generator (`scripts/load_generator.py`)

Professional async load tester supporting 4 modes:

```bash
# Install async engine (recommended)
pip install aiohttp

# Burst: Send N requests as fast as possible
python scripts/load_generator.py --mode burst --requests 500

# Ramp: Gradually increase concurrency over time
python scripts/load_generator.py --mode ramp --concurrency 50 --duration 60

# Stress: Increase load until failures are detected
python scripts/load_generator.py --mode stress --duration 120

# Constant: Fixed-rate requests
python scripts/load_generator.py --mode constant --requests 200 --concurrency 10
```

**Output metrics**:
- Total requests, successful, failed, error rate
- Throughput (req/s)
- Latency percentiles (Min, Avg, P50, P95, P99, Max)
- Per-container request distribution
- Max deviation from ideal balance
- **Auto-saves** Markdown report to `results/` folder with PASS/FAIL verdicts

**Verdict criteria**:
- Error rate: PASS if <5%
- Distribution: PASS if <10% deviation from ideal balance
- Saved as `results/load_test_<mode>_<timestamp>.md`

### Chaos Test — Fault Tolerance (`scripts/chaos_test.sh`)

```bash
bash scripts/chaos_test.sh
```

**What it does**:
1. Shows current running backend containers
2. Force-kills one container with `docker rm -f`
3. Monitors recovery every 1 second for up to 60 seconds
4. Verifies application is still available after recovery
5. Auto-saves detailed report to `results/chaos_test_results.md`

**Expected result**: Swarm replaces the killed container within ~5–15 seconds. Application remains available throughout (200 OK responses).

### Stress Test — Load Balancing Validation (`scripts/stress_test.sh`)

```bash
bash scripts/stress_test.sh
```

**Three tests**:
1. **100 sequential requests** — Verifies distribution across containers
2. **200 concurrent requests** — Tests throughput under concurrency
3. **20 CPU-intensive compute requests** — Validates compute workload distribution

**Output**: Container distribution table, Docker stats snapshot, auto-saved to `results/stress_test_results.md`

### Service Discovery Test (`scripts/service_discovery.sh`)

```bash
bash scripts/service_discovery.sh
```

**Validates**:
- DNS resolution of `backend` service name
- All backend task IPs are listed
- HTTP connectivity via service name
- Persistence after container replacement

---

## Configuration & Tuning

### Runtime Configuration (Load Balancer)

View and update LB config without restarting:

```bash
# View current config
curl http://localhost:8888/lb/lb-config

# Update memory threshold to 85%
curl -X POST http://localhost:8888/lb/lb-config \
  -H "Content-Type: application/json" \
  -d '{"memory_threshold": 85.0}'

# Disable fallback (return 503 when all overloaded)
curl -X POST http://localhost:8888/lb/lb-config \
  -H "Content-Type: application/json" \
  -d '{"fallback_enabled": false}'
```

### Scaling Thresholds (Orchestrator)

Adjust via environment variables in `docker-compose.yml`:

```yaml
orchestrator:
  environment:
    - SCALE_UP_THRESHOLD=75.0     # Scale up at 75% avg memory
    - SCALE_DOWN_THRESHOLD=25.0   # Scale down at 25% avg memory
    - SCALE_UP_COOLDOWN=45        # Wait 45s between scale-ups
    - MIN_REPLICAS=3              # Never go below 3 replicas
    - MAX_REPLICAS=10             # Allow up to 10 replicas
```

### Stats Poll Interval

```yaml
loadbalancer:
  environment:
    - STATS_INTERVAL=1.0  # Poll Docker stats every 1 second for faster response
```

---

## Project Structure

```
container-orchestration-swarm/
├── README.md                    # This file
├── .gitignore                   # Python gitignore
│
├── docker-compose.yml           # Main Swarm stack definition (170 lines)
├── docker-compose.multinode.yml # Docker-in-Docker multi-node simulation (75 lines)
│
├── backend/                     # Flask microservice — Algorithm 2 routing targets
│   ├── app.py                   # 6 endpoints: home, health, extract, compute, stats, info (251 lines)
│   ├── Dockerfile               # Python 3.11-slim + Gunicorn (27 lines)
│   └── requirements.txt         # Flask, gunicorn, redis
│
├── frontend/                    # Nginx reverse proxy + dashboard
│   ├── dashboard.html           # Real-time monitoring with Chart.js (454 lines)
│   ├── Dockerfile               # Nginx alpine (13 lines)
│   └── nginx.conf               # Reverse proxy with Docker DNS resolver (78 lines)
│
├── loadbalancer/                # Algorithm 2 — Memory-Based Load Balancer
│   ├── loadbalancer.py          # Stats polling, memory routing, fallback, API (431 lines)
│   ├── Dockerfile               # Python 3.11-slim (21 lines)
│   └── requirements.txt         # Flask, docker, requests
│
├── orchestrator/                # Algorithm 1 — Service Orchestrator
│   ├── orchestrator.py          # Event stream, state check, auto-scale, health probes (691 lines)
│   ├── Dockerfile               # Python 3.11-slim (20 lines)
│   └── requirements.txt         # flask, docker, requests
│
├── scripts/                     # Testing & automation
│   ├── load_generator.py        # Async load tester — 4 modes (414 lines)
│   ├── chaos_test.sh            # Fault tolerance / self-healing test (225 lines)
│   ├── stress_test.sh           # Load balancing validation (174 lines)
│   ├── simulate_multinode.sh    # Docker-in-Docker cluster simulation (173 lines)
│   ├── setup_multinode.sh       # Physical multi-node Swarm setup (126 lines)
│   └── service_discovery.sh     # DNS service discovery validation (123 lines)
│
└── results/                     # Auto-generated test reports
    ├── stress_test_results.md   # Stress test report template
    └── chaos_test_results.md    # Chaos test report template
```

**Total**: 25 files, ~3,740 lines of code and configuration.

---

## Troubleshooting

### "docker stack deploy" fails with network errors

```bash
# Remove existing network and retry
docker network rm cc_research_app-network
docker stack rm cc_research
# Wait 10 seconds, then redeploy
```

### Dashboard shows no data / "No backend containers discovered"

1. Ensure Docker socket is mounted: `docker service inspect cc_research_loadbalancer` — check `Mounts` section
2. Verify backends are running: `docker service ps cc_research_backend`
3. Check LB logs: `docker service logs cc_research_loadbalancer`
4. Wait 5–10 seconds after deploy for stats collection to begin

### Services won't start / stay in "pending" state

```bash
# Check for scheduling issues
docker service ps cc_research_backend

# Check node availability
docker node ls

# If using multi-node, check all nodes are "Ready"
docker node inspect <node-id> --format '{{.Status.State}}'
```

### Docker socket not accessible

The loadbalancer and orchestrator containers mount the Docker socket. On Windows, ensure the compose file uses the correct path format:

```yaml
volumes:
  - //var/run/docker.sock:/var/run/docker.sock  # Double slash for Windows
```

On Linux:
```yaml
volumes:
  - /var/run/docker.sock:/var/run/docker.sock
```

### "port already in use" for port 8888

Change the host port in `docker-compose.yml`:
```yaml
frontend:
  ports:
    - "8889:80"  # Change to any available port
```

### Container keeps restarting

```bash
# Inspect container logs
docker service logs cc_research_backend

# Check if health check is failing
docker inspect <container-id> --format '{{json .State.Health}}'

# Verify Redis is reachable
docker service logs cc_research_backend | grep redis
```

---

## Monitoring & Observability

### View Service Logs

```bash
# Load Balancer (Algorithm 2)
docker service logs cc_research_loadbalancer --tail 100 -f

# Orchestrator (Algorithm 1)
docker service logs cc_research_orchestrator --tail 100 -f

# Backend replicas
docker service logs cc_research_backend --tail 50

# All services
docker service logs cc_research_frontend --tail 50
```

### Check Service Status

```bash
# List all services and replicas
docker service ls

# Detailed task view for backend
docker service ps cc_research_backend

# Inspect service configuration
docker service inspect cc_research_loadbalancer
```

### Monitor Resource Usage

```bash
# Live stats for all containers in the stack
docker stats $(docker ps --filter "label=com.docker.stack.namespace=cc_research" -q)

# Single snapshot
docker stats --no-stream $(docker ps --filter "label=com.docker.stack.namespace=cc_research" -q)
```

### Docker Events

```bash
# Watch live Docker events (filtered for our services)
docker events --filter "label=com.docker.stack.namespace=cc_research"
```

### API Health Checks

```bash
# All services health check
curl http://localhost:8888/nginx-health
curl http://localhost:8888/lb/lb-health
curl http://localhost:8888/orchestrator/health
curl http://localhost:8888/api/health
```

---

## Cleanup

### Remove the Stack

```bash
docker stack rm cc_research
```

### Remove Docker Images (Optional)

```bash
docker rmi cc-research-frontend cc-research-loadbalancer cc-research-orchestrator cc-research-backend
```

### Multi-Node Simulation Cleanup

```bash
bash scripts/simulate_multinode.sh down
```

This removes all DinD containers and their named volumes (`-v` flag).

### Full Docker System Cleanup

```bash
# Remove all unused containers, networks, images
docker system prune -f

# Remove all volumes (WARNING: destroys Redis data)
docker volume prune -f
```

---

## Citation

If you use this project in academic work, please cite the original research paper:

```bibtex
@article{singh2023load,
  title={Load balancing and service discovery using Docker Swarm for microservice based big data applications},
  author={Singh, Neelam and Hamid, Yasir and Juneja, Sapna and Srivastava, Gautam and Dhiman, Gaurav and Gadekallu, Thippa Reddy and Shah, Mohd Asif},
  journal={Journal of Cloud Computing},
  year={2023}
}
```
