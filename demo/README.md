# Demo Guide

This directory contains shell scripts that trigger failure scenarios against service-b so
the observability stack has real signals to analyze.

Run all scripts from the **project root directory** (the folder that contains
`docker-compose.yml`), not from inside `demo/`.

---

## Prerequisites

- The full stack must be running: `docker compose up -d`
- Wait until all services show `healthy` or `running` in `docker compose ps`
- An `ANTHROPIC_API_KEY` in your `.env` file is required for the "Analyze with AI"
  button — metrics, logs, and traces work without it

---

## Scripts

| Script | Scenario | What it changes |
|---|---|---|
| `scenario_errors.sh` | A — 70% error rate | `FAILURE_RATE=0.7` on service-b |
| `scenario_slow.sh` | B — 2 second latency | `LATENCY_MS=2000` on service-b |
| `scenario_crash.sh` | C — Payment processor crash | Nothing — `/crash` is always enabled |
| `reset.sh` | Clean slate | Restores defaults, clears all stored data, restarts stack |

---

## Workflow

Every scenario follows the same four steps:

```
1. Run the script      bash demo/scenario_X.sh
2. Open the web UI     http://localhost:8081
3. Query the service   type the target name, click Query
4. Reset               bash demo/reset.sh
```

---

## Scenario A — High error rate

**Script:** `bash demo/scenario_errors.sh`

**What it does:**
Sets `FAILURE_RATE=0.7` on service-b, which causes 70% of `/data` requests to return HTTP
500. Fires 30 requests through service-a so that all three backends (metrics, logs,
traces) accumulate enough signal for a useful AI analysis.

**Query target:** `service-a`

Why service-a? The bug is in service-b, but service-a is the entry point that calls it.
Querying service-a shows the full picture: how errors in service-b propagate upstream and
affect the caller.

**What to look for:**

- **Logs panel:** Lines reading `"DatabaseConnectionError: connection pool exhausted"` — this
  is service-b's simulated error message
- **Traces panel:** Spans with a red error status showing service-a calling service-b and
  getting a 500
- **Metrics panel:** `http_requests_total` with `status="500"` climbing alongside the
  successes
- **RCA panel:** Should name service-b as the error source and suggest checking the
  database connection pool

**Expected script output:**
```
  [1/30] 200 OK
  [2/30] 500 ERROR
  [3/30] 500 ERROR
  ...
Done. 21 errors and 9 successes out of 30 requests.
```
Roughly 70% errors is expected — exact counts will vary due to randomness.

---

## Scenario B — Latency spike

**Script:** `bash demo/scenario_slow.sh`

**What it does:**
Sets `LATENCY_MS=2000` on service-b, which adds 2 seconds of delay to every `/data`
response. Fires 10 requests directly to service-b so the slow spans appear clearly in
Jaeger.

**Query target:** `service-b`

Why service-b directly? The latency is injected in service-b's request handler, so querying
service-b gives the clearest view of the 2-second spans. If you queried service-a you would
see the latency too, but with additional network overhead mixed in.

**What to look for:**

- **Traces panel:** Spans with durations around 2000–2100 ms — the added delay is visible
  as a long bar
- **Logs panel:** `"slow query completed: table scan on orders (no index on created_at)"` —
  the warning service-b logs after each slow response
- **Metrics panel:** `http_request_duration_seconds` showing a clear spike to 2+ seconds
- **RCA panel:** Should identify the latency source and suggest investigating slow database
  queries

**Expected script output:**
```
  [1/10] HTTP 200 — 2043 ms
  [2/10] HTTP 200 — 2011 ms
  ...
```
Each request should take approximately 2000 ms. If requests complete in under 100 ms, the
latency setting was not applied — make sure the script ran without errors before querying.

---

## Scenario C — Payment processor crash

**Script:** `bash demo/scenario_crash.sh`

**What it does:**
Calls the `/crash` endpoint on service-b 15 times. Each call triggers an unhandled Python
exception inside a simulated payment processing function. The full stack trace is logged to
stderr, shipped to Loki, and the failed span is recorded in Jaeger.

No environment variable changes are needed — the `/crash` endpoint is always enabled.

**Query target:** `service-b`

**What to look for:**

- **Logs panel:** The full Python traceback — look for `"Unhandled exception in payment
  processor"` followed by `"ValueError: payment_processor.charge() received None for
  amount"`. The loki client merges multi-line tracebacks into a single log entry, so
  you see the whole traceback as one block.
- **Traces panel:** Spans with error status on `service-b → _process_payment`
- **Metrics panel:** Requests with `status="500"` — every call to `/crash` fails
- **RCA panel:** Should identify the payment processor crash and suggest validating the
  `amount` argument before calling `charge()`

**Expected script output:**
```
  [1/15] 500 — crash recorded (expected)
  [2/15] 500 — crash recorded (expected)
  ...
Done. 15 crashes recorded, 0 unexpected successes.
```
All 15 requests returning 500 is correct — the endpoint always crashes.

---

## Resetting

After each scenario, run:

```bash
bash demo/reset.sh
```

This script will:
1. Restore `docker-compose.yml` to defaults (`FAILURE_RATE=0.0`, `LATENCY_MS=0`)
2. Stop all services and delete stored data (Prometheus metrics, Loki logs, Jaeger traces)
3. Start the stack fresh
4. Wait 45 seconds for services to initialize
5. Print the current container status

The reset takes about a minute. Once it's done, open http://localhost:8081, query a service,
and confirm the panels are empty before running the next scenario.

---

## Using the web UI

After running a scenario and querying the target service:

1. **Metrics panel** — request counts, error rates, and latency percentiles over time
2. **Logs panel** — log lines from the service, sorted by time. Use the search box to
   filter by keyword (e.g. `ERROR`, `exception`, `pool`).
3. **Traces panel** — individual request traces from Jaeger. Error traces are highlighted.
   Click a trace to expand its spans.
4. **Correlations panel** — events the aggregator detected by comparing signals across
   backends (e.g. "error rate spike aligns with latency increase")
5. **RCA panel** — click **"Analyze with AI"** to send all signals to Claude. The response
   includes a summary, root cause, confidence score, supporting evidence, and recommended
   next steps.

> **Give Prometheus 15 seconds to scrape** after running a scenario script before
> querying. Prometheus pulls metrics on a fixed interval, so very recent traffic may not
> appear immediately.

---

## Troubleshooting

**Traces panel is empty**
Jaeger collects spans asynchronously. Wait 10–15 seconds after the script finishes, then
refresh.

**Logs panel shows no results**
Loki uses service label selectors. Make sure you typed the exact service name — `service-a`
or `service-b` — in the query box. Labels are case-sensitive.

**"Analyze with AI" does nothing**
`ANTHROPIC_API_KEY` is missing or invalid in your `.env` file. Restart the aggregator after
adding the key: `docker compose up -d --no-deps aggregator`.

**Script says "Could not find FAILURE_RATE in docker-compose.yml"**
Run the script from the project root (the directory that contains `docker-compose.yml`),
not from inside `demo/`.

**RCA says "No error signals found" or `performed: false`**
RCA only runs when error signals are present. For Scenario B (latency), the RCA may not
trigger if all requests returned 200. Run Scenario A or C first to confirm the RCA is
working, then come back to Scenario B.
