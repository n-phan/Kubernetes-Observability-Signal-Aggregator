#!/usr/bin/env bash
# =============================================================================
# Scenario C — Payment processor crash with stack trace
# =============================================================================
#
# What this does:
#   Calls the /crash endpoint on service-b 15 times. Each call triggers an
#   unhandled Python exception inside a simulated payment function. The full
#   stack trace is logged to stderr, shipped to Loki, and the failed span is
#   recorded in Jaeger.
#
# What to look for in the UI (http://localhost:8081):
#   - Logs panel: "Unhandled exception in payment processor" with a traceback
#   - Traces panel: spans with error status on service-b → _process_payment
#   - RCA panel: should identify "payment_processor.charge() received None"
#
# Prerequisites: stack must be running (docker compose up -d)
# No env var changes needed — the /crash endpoint is always enabled.
# =============================================================================

set -euo pipefail

BASE_URL="http://localhost:8002"
REQUESTS=15

echo "==> Scenario C: Payment processor crash"
echo "    Hitting ${BASE_URL}/crash ${REQUESTS} times..."
echo ""

SUCCESS=0
FAIL=0

for i in $(seq 1 "$REQUESTS"); do
    # `|| true` prevents set -e from aborting the loop if curl can't connect
    # (e.g. service-b is briefly restarting after an OOM kill).
    HTTP_STATUS=$(curl --silent --output /dev/null --write-out "%{http_code}" "${BASE_URL}/crash" || true)
    if [ "$HTTP_STATUS" = "500" ]; then
        FAIL=$((FAIL + 1))
        echo "  [${i}/${REQUESTS}] 500 — crash recorded (expected)"
    elif [ -z "$HTTP_STATUS" ]; then
        FAIL=$((FAIL + 1))
        echo "  [${i}/${REQUESTS}] connection failed — is service-b running?"
    else
        SUCCESS=$((SUCCESS + 1))
        echo "  [${i}/${REQUESTS}] ${HTTP_STATUS} — unexpected response"
    fi
done

echo ""
echo "Done. ${FAIL} crashes recorded, ${SUCCESS} unexpected successes."
echo ""
echo "Give Prometheus ~15 s to scrape, then:"
echo "  1. Open http://localhost:8081"
echo "  2. Type 'service-b' in the target box, click Query"
echo "  3. Check the Logs panel for the full Python traceback"
echo "  4. Check the Traces panel for error spans"
echo "  5. Click 'Analyze with AI' — the RCA should identify the payment processor issue"
echo ""
echo "To reset and run the next scenario:"
echo "  bash demo/reset.sh"