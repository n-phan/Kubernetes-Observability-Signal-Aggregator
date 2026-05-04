#!/usr/bin/env bash
# =============================================================================
# Scenario A — High error rate (random 500 responses)
# =============================================================================
#
# What this does:
#   Sets FAILURE_RATE=0.7 on service-b (70% of /data requests fail with HTTP
#   500), then fires 30 requests through service-a so that metrics, logs, and
#   traces all accumulate enough signal for a useful AI analysis.
#
# What to look for in the UI (http://localhost:8081):
#   - Logs panel: "DatabaseConnectionError: connection pool exhausted" errors
#   - Traces panel: spans with error status (red)
#   - Metrics panel: http_requests_total with status="500" climbing
#   - RCA panel: should name service-b as the error source and suggest checking
#                the database connection pool
#
# Prerequisites: stack must be running (docker compose up -d)
#
# To reset afterwards:
#   bash demo/reset.sh
# =============================================================================

set -euo pipefail

SERVICE_A_URL="http://localhost:8001"
REQUESTS=30

echo "==> Scenario A: High error rate (70%)"
echo ""
echo "Step 1 — Updating docker-compose.yml to set FAILURE_RATE=0.7..."

# Patch docker-compose.yml in place using sed.
# This edits the file on disk so docker compose picks it up on restart.
if grep -q 'FAILURE_RATE:' docker-compose.yml; then
    sed -i.bak 's/FAILURE_RATE: "0\.[0-9]*"/FAILURE_RATE: "0.7"/' docker-compose.yml
    echo "    docker-compose.yml updated (backup saved as docker-compose.yml.bak)"
else
    echo "    ERROR: Could not find FAILURE_RATE in docker-compose.yml"
    echo "    Are you running this from the project root directory?"
    exit 1
fi

echo ""
echo "Step 2 — Restarting service-b to apply the new setting..."
docker compose up -d --no-deps service-b

echo ""
echo "Waiting 8 s for service-b to become healthy..."
sleep 8

echo ""
echo "Step 3 — Sending ${REQUESTS} requests through service-a..."
echo "         About 70% should return HTTP 500."
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
        echo "  [${i}/${REQUESTS}] ${HTTP_STATUS} ERROR"
    fi
done

echo ""
echo "Done. ${FAIL} errors and ${SUCCESS} successes out of ${REQUESTS} requests."
echo ""
echo "Next steps:"
echo "  1. Open http://localhost:8081"
echo "  2. Type 'service-a' in the target box, click Query"
echo "  3. Review errors in the Logs and Traces panels"
echo "  4. Check the Metrics panel for status=\"500\" counts"
echo "  5. Click 'Analyze with AI' — the RCA should identify service-b errors"
echo ""
echo "To reset and run the next scenario:"
echo "  bash demo/reset.sh"