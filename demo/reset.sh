#!/usr/bin/env bash
# =============================================================================
# Demo cleanup — reset the stack to a clean state
# =============================================================================
#
# What this does:
#   Restores all services to their default configuration (no injected failures).
#   Clears any stale data, restarts the observability backends, and confirms
#   everything is healthy.
#
# Safe to run:
#   - After any demo scenario
#   - Before running a new scenario
#   - Anytime the stack feels "dirty" from previous testing
#
# No arguments needed — just run and wait.
# =============================================================================

set -euo pipefail

echo "==> Demo cleanup: resetting stack to original state"
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# Step 1: Check docker compose is available
# ─────────────────────────────────────────────────────────────────────────────

if ! command -v docker compose &> /dev/null; then
    echo "ERROR: 'docker compose' not found. Are you in the project root?"
    exit 1
fi

echo "Step 1 — Restoring docker-compose.yml to defaults..."

# If a backup exists from scenario_errors.sh, restore it
if [ -f docker-compose.yml.bak ]; then
    echo "    Found docker-compose.yml.bak — restoring original"
    mv docker-compose.yml.bak docker-compose.yml
    echo "    ✓ Restored"
else
    # No backup, so check if FAILURE_RATE is non-default and fix it
    if grep -q 'FAILURE_RATE: "0\.[1-9]' docker-compose.yml; then
        echo "    Found non-default FAILURE_RATE — resetting to 0.0"
        sed -i.tmp 's/FAILURE_RATE: "0\.[0-9]*"/FAILURE_RATE: "0.0"/' docker-compose.yml
        rm -f docker-compose.yml.tmp
        echo "    ✓ Reset"
    else
        echo "    Already at defaults"
    fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# Step 2: Stop services and clear their data
# ─────────────────────────────────────────────────────────────────────────────

echo ""
echo "Step 2 — Stopping all services..."
docker compose stop 2>/dev/null || true
echo "    ✓ Stopped"

echo ""
echo "Step 3 — Clearing observability data..."
echo "    (Prometheus, Loki, and Jaeger data will be wiped)"

# Remove named volumes to clear stored metrics, logs, and traces
docker compose down -v 2>/dev/null || true
echo "    ✓ Volumes cleared"

# ─────────────────────────────────────────────────────────────────────────────
# Step 4: Start fresh
# ─────────────────────────────────────────────────────────────────────────────

echo ""
echo "Step 4 — Starting fresh stack..."
docker compose up -d

echo ""
echo "Step 5 — Waiting for services to start (45 seconds)..."

# Services take time to initialize. Rather than parsing health status
# (which varies by Docker version), just wait a fixed time.
WAIT_SECS=45
for i in $(seq $WAIT_SECS -1 1); do
    printf "    ⏳ ${i}s remaining...\r"
    sleep 1
done
echo "    ✓ Done waiting. Services should be running now.                  "

# ─────────────────────────────────────────────────────────────────────────────
# Step 6: Confirm state
# ─────────────────────────────────────────────────────────────────────────────

echo ""
echo "Step 6 — Confirming state..."

# Check that all env vars are back to defaults
if grep -q 'FAILURE_RATE: "0.0"' docker-compose.yml && \
   grep -q 'LATENCY_MS: "0"' docker-compose.yml && \
   grep -q 'OOM_ENDPOINT: "false"' docker-compose.yml; then
    echo "    ✓ All env vars at defaults (no failure injection active)"
else
    echo "    ⚠ Some env vars may not be at defaults"
fi

echo ""
echo "==> Reset complete! Stack is starting up."
echo ""
echo "Current container status:"
docker compose ps --format "table {{.Service}}\t{{.Status}}"
echo ""
echo "Your stack is ready when:"
echo "  • All services show 'healthy' or 'running' status (above)"
echo "  • http://localhost:8081 loads in your browser"
echo "  • You can query a service and see results"
echo ""
echo "Tips:"
echo "  • If services are still 'starting', wait 30 more seconds"
echo "  • Frontend (nginx) is usually the last to stabilize"
echo "  • Run: watch -n 2 'docker compose ps' to monitor"
echo "  • Run: docker compose logs -f to see detailed startup logs"
echo ""
echo "Once ready, run:"
echo "  bash demo/scenario_errors.sh"