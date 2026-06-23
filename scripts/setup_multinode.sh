#!/bin/bash
# ==========================================
# MULTI-NODE SWARM SETUP SCRIPT
# ==========================================
# Sets up a multi-node Docker Swarm cluster
# 
# Architecture (3 nodes):
#   Node 1 (Manager): orchestrator, loadbalancer, frontend
#   Node 2 (Worker):  backend replicas, redis
#   Node 3 (Worker):  backend replicas
#
# Prerequisites:
#   - 3 machines (physical, VMs, or cloud instances)
#   - Docker installed on all 3
#   - Network connectivity between them (ports 2377, 7946, 4789)
#
# Usage:
#   On MANAGER: bash setup_multinode.sh manager
#   On WORKERS: bash setup_multinode.sh worker <MANAGER_IP> <TOKEN>
# ==========================================

MODE="${1:-help}"
MANAGER_IP="${2:-}"
JOIN_TOKEN="${3:-}"

case "$MODE" in
    manager)
        echo "============================================"
        echo "  INITIALIZING SWARM MANAGER NODE"
        echo "============================================"
        echo ""
        
        # Get this machine's IP
        MY_IP=$(hostname -I | awk '{print $1}')
        echo "Manager IP: $MY_IP"
        echo ""
        
        # Initialize Swarm
        echo "Initializing Docker Swarm..."
        docker swarm init --advertise-addr "$MY_IP"
        echo ""
        
        # Get worker join token
        WORKER_TOKEN=$(docker swarm join-token -q worker)
        echo "============================================"
        echo "  WORKER JOIN COMMAND"
        echo "============================================"
        echo ""
        echo "Run this on each WORKER node:"
        echo ""
        echo "  bash setup_multinode.sh worker $MY_IP $WORKER_TOKEN"
        echo ""
        echo "Or manually:"
        echo "  docker swarm join --token $WORKER_TOKEN $MY_IP:2377"
        echo ""
        echo "============================================"
        echo ""
        echo "After workers join, deploy the stack from this manager:"
        echo "  docker stack deploy -c docker-compose.yml cc_research"
        ;;
        
    worker)
        if [ -z "$MANAGER_IP" ] || [ -z "$JOIN_TOKEN" ]; then
            echo "Usage: bash setup_multinode.sh worker <MANAGER_IP> <TOKEN>"
            echo ""
            echo "Get the token from the manager node by running:"
            echo "  docker swarm join-token worker"
            exit 1
        fi
        
        echo "============================================"
        echo "  JOINING SWARM AS WORKER NODE"
        echo "============================================"
        echo ""
        echo "Manager: $MANAGER_IP"
        echo ""
        
        docker swarm join --token "$JOIN_TOKEN" "$MANAGER_IP":2377
        
        echo ""
        echo "✅ Joined the Swarm cluster!"
        echo "The manager can now schedule containers on this node."
        ;;
        
    status)
        echo "============================================"
        echo "  SWARM CLUSTER STATUS"
        echo "============================================"
        echo ""
        echo "Nodes:"
        docker node ls
        echo ""
        echo "Services:"
        docker service ls
        echo ""
        echo "Task Distribution:"
        docker service ps cc_research_backend --format "table {{.Name}}\t{{.Node}}\t{{.CurrentState}}"
        echo ""
        echo "Node Details:"
        for NODE_ID in $(docker node ls -q); do
            echo "--- Node: $(docker node inspect $NODE_ID --format '{{.Description.Hostname}}') ---"
            echo "  Role: $(docker node inspect $NODE_ID --format '{{.Spec.Role}}')"
            echo "  State: $(docker node inspect $NODE_ID --format '{{.Status.State}}')"
            echo "  Addr: $(docker node inspect $NODE_ID --format '{{.Status.Addr}}')"
            echo "  CPUs: $(docker node inspect $NODE_ID --format '{{.Description.Resources.NanoCPUs}}' | awk '{printf "%.0f\n", $1/1000000000}')"
            echo "  Memory: $(docker node inspect $NODE_ID --format '{{.Description.Resources.MemoryBytes}}' | awk '{printf "%.1f GB\n", $1/1073741824}')"
            echo ""
        done
        ;;
        
    help|*)
        echo "Multi-Node Docker Swarm Setup"
        echo ""
        echo "Usage:"
        echo "  bash setup_multinode.sh manager          # Initialize Swarm on manager node"
        echo "  bash setup_multinode.sh worker IP TOKEN   # Join Swarm as worker"
        echo "  bash setup_multinode.sh status            # Show cluster status"
        echo ""
        echo "Required ports (open in firewall/security group):"
        echo "  2377/tcp  - Swarm management"
        echo "  7946/tcp  - Node communication"
        echo "  7946/udp  - Node communication"
        echo "  4789/udp  - Overlay network"
        echo "  8888/tcp  - Application access"
        ;;
esac
