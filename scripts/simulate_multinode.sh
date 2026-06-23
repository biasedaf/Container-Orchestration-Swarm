#!/bin/bash
# ==========================================
# MULTI-NODE SWARM SIMULATION SCRIPT
# ==========================================
# Simulates a 3-node Docker Swarm cluster using Docker-in-Docker.
# This allows demonstrating multi-node behavior on a single machine.
#
# Commands:
#   up       - Start DinD containers
#   init     - Initialize Swarm and join workers
#   deploy   - Build images and deploy stack inside the simulated cluster
#   status   - Show cluster status and task distribution
#   test     - Run a quick load test against the simulated cluster
#   down     - Tear down everything
#   full     - Run all steps: up → init → deploy → status
#
# Usage: bash scripts/simulate_multinode.sh [command]
# ==========================================

COMPOSE_FILE="$(dirname "$0")/../docker-compose.multinode.yml"
PROJECT_DIR="$(dirname "$0")/.."

case "${1:-help}" in

    up)
        echo "============================================"
        echo "  Starting DinD Nodes..."
        echo "============================================"
        docker-compose -f "$COMPOSE_FILE" up -d
        echo ""
        echo "Waiting 15 seconds for DinD containers to initialize..."
        sleep 15
        echo "✅ DinD nodes started"
        docker-compose -f "$COMPOSE_FILE" ps
        ;;

    init)
        echo "============================================"
        echo "  Initializing Swarm Cluster"
        echo "============================================"
        echo ""

        # Get manager IP on the swarm-net bridge
        MANAGER_IP=$(docker inspect swarm-manager1 --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}')
        echo "Manager IP: $MANAGER_IP"

        # Initialize Swarm on manager
        echo "Initializing Swarm on manager1..."
        docker exec swarm-manager1 docker swarm init --advertise-addr "$MANAGER_IP" 2>/dev/null || echo "Swarm already initialized"
        echo ""

        # Get worker join token
        JOIN_TOKEN=$(docker exec swarm-manager1 docker swarm join-token -q worker)
        echo "Worker join token: $JOIN_TOKEN"
        echo ""

        # Join workers
        echo "Joining worker1..."
        docker exec swarm-worker1 docker swarm join --token "$JOIN_TOKEN" "$MANAGER_IP":2377 2>/dev/null || echo "worker1 already joined"
        echo ""

        echo "Joining worker2..."
        docker exec swarm-worker2 docker swarm join --token "$JOIN_TOKEN" "$MANAGER_IP":2377 2>/dev/null || echo "worker2 already joined"
        echo ""

        echo "✅ Swarm cluster ready!"
        docker exec swarm-manager1 docker node ls
        ;;

    deploy)
        echo "============================================"
        echo "  Deploying Stack to Simulated Cluster"
        echo "============================================"
        echo ""

        echo "Building images inside manager node..."
        docker exec swarm-manager1 sh -c "cd /workspace && docker build -t cc-research-backend ./backend"
        docker exec swarm-manager1 sh -c "cd /workspace && docker build -t cc-research-frontend ./frontend"
        docker exec swarm-manager1 sh -c "cd /workspace && docker build -t cc-research-loadbalancer ./loadbalancer"
        docker exec swarm-manager1 sh -c "cd /workspace && docker build -t cc-research-orchestrator ./orchestrator"
        echo ""

        echo "Deploying stack..."
        docker exec swarm-manager1 sh -c "cd /workspace && docker stack deploy -c docker-compose.yml cc_research"
        echo ""

        echo "Waiting 20 seconds for services to start..."
        sleep 20

        echo "✅ Stack deployed!"
        docker exec swarm-manager1 docker service ls
        echo ""

        echo "Task distribution across nodes:"
        docker exec swarm-manager1 docker service ps cc_research_backend --format "table {{.Name}}\t{{.Node}}\t{{.CurrentState}}"
        ;;

    status)
        echo "============================================"
        echo "  SIMULATED CLUSTER STATUS"
        echo "============================================"
        echo ""

        echo "📋 Nodes:"
        docker exec swarm-manager1 docker node ls
        echo ""

        echo "📋 Services:"
        docker exec swarm-manager1 docker service ls
        echo ""

        echo "📋 Backend Task Distribution Across Nodes:"
        docker exec swarm-manager1 docker service ps cc_research_backend \
            --filter "desired-state=running" \
            --format "table {{.Name}}\t{{.Node}}\t{{.CurrentState}}"
        echo ""

        echo "📋 Node Details:"
        for NODE in manager1 worker1 worker2; do
            CONTAINERS=$(docker exec swarm-manager1 docker node ps "$(docker exec swarm-manager1 docker node ls --filter "name=$NODE" -q)" --filter "desired-state=running" -q 2>/dev/null | wc -l)
            echo "  $NODE: $CONTAINERS running tasks"
        done
        ;;

    test)
        echo "============================================"
        echo "  Testing Simulated Multi-Node Cluster"
        echo "============================================"
        echo ""

        echo "Sending 20 requests to verify distribution..."
        for i in $(seq 1 20); do
            RESPONSE=$(curl -s http://localhost:18888/api/ 2>/dev/null)
            CONTAINER=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('container_id','error'))" 2>/dev/null)
            echo "  Request $i → Container: $CONTAINER"
        done
        echo ""
        echo "✅ Multi-node test complete"
        ;;

    down)
        echo "Tearing down simulated cluster..."
        docker-compose -f "$COMPOSE_FILE" down -v
        echo "✅ Done"
        ;;

    full)
        echo "Running full simulation: up → init → deploy → status"
        echo ""
        bash "$0" up
        echo ""
        bash "$0" init
        echo ""
        bash "$0" deploy
        echo ""
        bash "$0" status
        ;;

    help|*)
        echo "Multi-Node Swarm Simulation (Docker-in-Docker)"
        echo ""
        echo "Usage: bash scripts/simulate_multinode.sh [command]"
        echo ""
        echo "Commands:"
        echo "  up       Start DinD containers"
        echo "  init     Initialize Swarm and join workers"
        echo "  deploy   Build images and deploy stack"
        echo "  status   Show cluster status"
        echo "  test     Quick load test"
        echo "  down     Tear everything down"
        echo "  full     Run all steps (up → init → deploy → status)"
        ;;
esac
