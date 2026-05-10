#!/usr/bin/env bash
# =============================================================================
# Scenario C — Latency spike (slow database query simulation)
# =============================================================================
#
# What this does:
#   Sets LATENCY_MS=2000 on service-b (adds 2 seconds to every /data response),
#   then sends 10 requests directly to service-b's /data endpoint. Each request
#   takes ~2 extra seconds, producing high-latency spans in Jaeger.
#
# What to look for in the UI (http://localhost:8081):
#   - Traces panel: spans with duration > 2 000 ms (the /data endpoint takes 2s)
#   - Metrics panel: http_request_duration_seconds spiking
#   - Logs panel: "slow query completed" warnings from service-b
#   - RCA panel: should identify the added latency
#
# Prerequisites: stack must be running (docker compose up -d)
# This script edits docker-compose.yml directly — same as scenario_errors.sh.
#
# To reset afterwards:
#   bash demo/reset.sh
# =============================================================================

set -euo pipefail

SERVICE_B_URL="http://localhost:8002"
REQUESTS=10

echo "==> Scenario C: Latency spike (2 second added delay)"
echo ""
echo "Step 1 — Updating docker-compose.yml to set LATENCY_MS=2000..."

# Patch docker-compose.yml to add latency
if grep -q 'LATENCY_MS:' docker-compose.yml; then
    sed -i.bak 's/LATENCY_MS: "0"/LATENCY_MS: "2000"/' docker-compose.yml
    echo "    docker-compose.yml updated (backup saved as docker-compose.yml.bak)"
else
    echo "    ERROR: Could not find LATENCY_MS in docker-compose.yml"
    exit 1
fi

echo ""
echo "Step 2 — Restarting service-b to apply the new setting..."
docker compose up -d --no-deps service-b

echo ""
echo "Waiting 8 s for service-b to become healthy..."
sleep 8

echo ""
echo "Step 3 — Sending ${REQUESTS} requests directly to service-b /data..."
echo "         Each should take ~2000 ms (2 seconds) due to added latency"
echo ""

for i in $(seq 1 "$REQUESTS"); do
    START_NS=$(date +%s%N 2>/dev/null || date +%s)
    HTTP_STATUS=$(curl --silent --output /dev/null --write-out "%{http_code}" \
        --max-time 5 "${SERVICE_B_URL}/data")
    END_NS=$(date +%s%N 2>/dev/null || date +%s)
    # Milliseconds (falls back gracefully if %N not supported)
    if [[ "${START_NS}" =~ ^[0-9]{10,}$ ]] && [[ "${END_NS}" =~ ^[0-9]{10,}$ ]]; then
        ELAPSED_MS=$(( (END_NS - START_NS) / 1000000 ))
        echo "  [${i}/${REQUESTS}] HTTP ${HTTP_STATUS} — ${ELAPSED_MS} ms"
    else
        echo "  [${i}/${REQUESTS}] HTTP ${HTTP_STATUS}"
    fi
done

echo ""
echo "Done. Give Prometheus 15 s to scrape the new metrics."
echo ""
echo "Next steps:"
echo "  1. Open http://localhost:8081"
echo "  2. Select 'service-b' from the Target dropdown, click Query"
echo "  3. Look at the Traces panel — spans should show 2 000+ ms durations"
echo "  4. Check the Logs panel for 'slow query completed' messages"
echo "  5. Check the Metrics panel for http_request_duration_seconds"
echo "  6. Click 'Analyze with AI'"
echo ""
echo "To reset back to normal latency:"
echo "  bash demo/reset.sh"