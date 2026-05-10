#!/usr/bin/env bash
# =============================================================================
# Scenario A — Normal operation (baseline / healthy traffic)
# =============================================================================
#
# What this does:
#   Resets service-b to default settings (no failure injection), restarts it,
#   then fires 20 requests through service-a so you can establish a clean
#   baseline in metrics, logs, and traces before running a failure scenario.
#
# What to look for in the UI (http://localhost:8081):
#   - Logs panel: no ERROR lines — only INFO entries
#   - Traces panel: all spans green (no error status)
#   - Metrics panel: http_requests_total with status="200" only
#   - RCA panel: "No error signals found" or "performed: false"
#
# Prerequisites: stack must be running (docker compose up -d)
#
# Run next:
#   bash demo/scenario_errors.sh   (Scenario B — High error rate)
#   bash demo/scenario_slow.sh     (Scenario C — Latency spike)
#   bash demo/scenario_crash.sh    (Scenario D — Payment crash)
# =============================================================================

set -euo pipefail

SERVICE_A_URL="http://localhost:8001"
REQUESTS=20

echo "==> Scenario A: Normal operation (healthy baseline)"
echo ""
echo "Step 1 — Ensuring service-b is at default settings (no failure injection)..."

CHANGED=0

if grep -q 'FAILURE_RATE: "0\.[1-9]' docker-compose.yml; then
    sed -i.bak 's/FAILURE_RATE: "0\.[0-9]*"/FAILURE_RATE: "0.0"/' docker-compose.yml
    echo "    FAILURE_RATE reset to 0.0"
    CHANGED=1
fi

if grep -q 'LATENCY_MS: "[1-9]' docker-compose.yml; then
    sed -i.bak 's/LATENCY_MS: "[0-9]*"/LATENCY_MS: "0"/' docker-compose.yml
    echo "    LATENCY_MS reset to 0"
    CHANGED=1
fi

if [ "$CHANGED" -eq 0 ]; then
    echo "    Already at defaults — no changes needed"
fi

echo ""
echo "Step 2 — Restarting service-b to confirm clean state..."
docker compose up -d --no-deps service-b

echo ""
echo "Waiting 8 s for service-b to become healthy..."
sleep 8

echo ""
echo "Step 3 — Sending ${REQUESTS} requests through service-a..."
echo "         All should return HTTP 200."
echo ""

SUCCESS=0
FAIL=0

for i in $(seq 1 "$REQUESTS"); do
    HTTP_STATUS=$(curl --silent --output /dev/null --write-out "%{http_code}" \
        --max-time 5 "${SERVICE_A_URL}/api/data")
    if [ "$HTTP_STATUS" = "200" ]; then
        SUCCESS=$((SUCCESS + 1))
        echo "  [${i}/${REQUESTS}] 200 OK"
    else
        FAIL=$((FAIL + 1))
        echo "  [${i}/${REQUESTS}] ${HTTP_STATUS} (unexpected)"
    fi
done

echo ""
echo "Done. ${SUCCESS} successes and ${FAIL} unexpected errors out of ${REQUESTS} requests."
echo ""
echo "Next steps:"
echo "  1. Open http://localhost:8081"
echo "  2. Select 'service-a' from the Target dropdown, click Query"
echo "  3. Confirm all panels show clean data (no errors, normal latency)"
echo "  4. Run a failure scenario to compare against this baseline:"
echo "       bash demo/scenario_errors.sh   (Scenario B — High error rate)"
echo "       bash demo/scenario_slow.sh     (Scenario C — Latency spike)"
echo "       bash demo/scenario_crash.sh    (Scenario D — Payment crash)"
echo ""
echo "To reset:"
echo "  bash demo/reset.sh"
