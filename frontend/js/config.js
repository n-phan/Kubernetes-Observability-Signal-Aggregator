// ── Pagination ──────────────────────────────────────────────────────────────
const LOG_PAGE_SIZE   = 10;
const TRACE_PAGE_SIZE = 10;

// ── Mock data ────────────────────────────────────────────────────────────────
// Used when the Mock button is toggled on. Mirrors the real API response shape
// so all render functions work identically against live and mock data.
const MOCK_DATA = {
  meta: {
    target: 'service-b',
    namespace: 'default',
    window_start: '2026-05-02T19:00:00Z',
    window_end:   '2026-05-02T19:30:00Z',
    query_duration_ms: 1243,
  },

  correlations: [
    {
      severity:    'error',
      kind:        'error_spike',
      description: 'HTTP error rate peaked at 14.04% on /crash handler',
      confidence:  0.95,
    },
    {
      severity:    'warn',
      kind:        'error_rate_anomaly',
      description: 'Error rate z-score=3.8 — statistically unusual spike detected',
      confidence:  0.72,
    },
  ],

  rca: {
    performed: true,
    summary:
      'The /crash endpoint in service-b is consistently returning HTTP 500 errors due to an ' +
      'unhandled ValueError in the payment processing code path.',
    root_cause:
      'The /crash endpoint calls _process_payment(amount=None), which raises a ValueError when ' +
      'amount is None. The exception is caught and logged, but the endpoint re-raises it as an ' +
      'HTTPException(500). This is occurring on every request to /crash, causing a 100% error ' +
      'rate on that handler and contributing to the overall service error rate spike of 14.04%. ' +
      'CPU and memory remain stable, confirming this is a logic error rather than a resource issue.',
    confidence: 0.92,
    supporting_evidence: [
      'http_error_rate for /crash peaked at 0.1404 req/s — 100% of requests to that handler failed',
      'Stack trace shows ValueError raised at app.py:L97 inside _process_payment()',
      'All other endpoints (/health, /metrics, /data) show 0% error rate',
      'CPU (0.39%) and memory (47.67 MB) are within normal operating ranges',
      'Error rate z-score of 3.8 is statistically significant — not random variance',
    ],
    log_evidence: [
      {
        timestamp: '2026-05-02T19:12:34Z',
        severity: 'error',
        message:
          'Unhandled exception in payment processor:\n' +
          'ValueError: payment_processor.charge() received None for amount',
        relevance:
          'The stack trace identifies the payment-processing path and exception causing the 500s.',
        labels: { service: 'service-b', handler: '/crash' },
      },
      {
        timestamp: '2026-05-02T19:12:35Z',
        severity: 'error',
        message:
          'Unhandled exception in payment processor:\n' +
          'ValueError: payment_processor.charge() received None for amount',
        relevance: 'Repeated error logs show the failure is persistent, not a single request.',
        labels: { service: 'service-b', handler: '/crash' },
      },
    ],
    recommended_actions: [
      {
        priority: 1,
        action:
          'Add input validation to _process_payment() — reject None values before processing ' +
          'and return a 400 Bad Request instead of raising an unhandled exception',
        rationale:
          'The root cause is a missing null-check. Validating input at the boundary prevents the ' +
          'ValueError from propagating and eliminates the 500 error entirely.',
      },
      {
        priority: 1,
        action:
          'Remove or gate the /crash endpoint behind a feature flag — it should never be reachable in production',
        rationale:
          'An endpoint that intentionally crashes has no production use case. Exposing it inflates ' +
          'error rates and masks real incidents.',
      },
      {
        priority: 2,
        action:
          "Add a circuit breaker to service-a's calls to service-b to gracefully degrade when " +
          'service-b error rate exceeds 10%',
        rationale:
          'Prevents cascading failures upstream when service-b experiences error spikes like this one.',
      },
      {
        priority: 3,
        action:
          'Add structured error logging with exception type, endpoint, and request ID to all 5xx responses',
        rationale:
          'Current logs capture the traceback but lack structured fields, making automated alerting ' +
          'and log-based correlation harder.',
      },
    ],
    code_references: [
      {
        path: 'demo/service-b/app.py',
        line_number: 88,
        url: 'https://github.com/n-phan/k8s-obs-aggregator-final/blob/main/demo/service-b/app.py#L88',
        relevance: 'Line 88 in app.py — referenced in error stack trace',
      },
      {
        path: 'demo/service-b/app.py',
        line_number: 97,
        url: 'https://github.com/n-phan/k8s-obs-aggregator-final/blob/main/demo/service-b/app.py#L97',
        relevance: 'Line 97 in app.py — raise ValueError in _process_payment()',
      },
    ],
    github_search_terms: ['_process_payment', 'payment_processor', 'ValueError amount'],
  },

  metrics: {
    error: null,
    series: [
      { name: 'cpu_usage',                latest_value: 0.00393,  peak_value: 0.004456, labels: { instance: 'service-b:8002', job: 'service-b' } },
      { name: 'memory_bytes',             latest_value: 47670000, peak_value: 47740000, labels: { instance: 'service-b:8002', job: 'service-b' } },
      { name: 'http_requests_per_second', latest_value: 0.07018,  peak_value: 0.1404,   labels: { handler: '/crash',   job: 'service-b', status: '5xx' } },
      { name: 'http_requests_per_second', latest_value: 0.09825,  peak_value: 0.1018,   labels: { handler: '/health',  job: 'service-b', status: '2xx' } },
      { name: 'http_requests_per_second', latest_value: 0.06667,  peak_value: 0.06667,  labels: { handler: '/metrics', job: 'service-b', status: '2xx' } },
      { name: 'http_error_rate',          latest_value: 0.07018,  peak_value: 0.1404,   labels: { handler: '/crash',   job: 'service-b' } },
      { name: 'http_latency_p99',         latest_value: 0.099,    peak_value: null,      labels: { handler: '/crash',   job: 'service-b' } },
      { name: 'http_latency_p99',         latest_value: 0.099,    peak_value: 0.099,     labels: { handler: '/health',  job: 'service-b' } },
    ],
  },

  logs: {
    total_lines: 120,
    error_count: 20,
    error: null,
    lines: [
      { timestamp: '2026-05-02T19:12:34Z', severity: 'ERROR',   message: 'Unhandled exception in payment processor:\nTraceback (most recent call last):\n  File "/app/app.py", line 88, in crash\n    _process_payment(amount=None)\n  File "/app/app.py", line 97, in _process_payment\n    raise ValueError("payment_processor.charge() received None for amount")\nValueError: payment_processor.charge() received None for amount' },
      { timestamp: '2026-05-02T19:12:34Z', severity: 'UNKNOWN', message: 'INFO: 192.168.65.1:28903 - "GET /crash HTTP/1.1" 500 Internal Server Error' },
      { timestamp: '2026-05-02T19:12:35Z', severity: 'ERROR',   message: 'Unhandled exception in payment processor:\nTraceback (most recent call last):\n  File "/app/app.py", line 88, in crash\n    _process_payment(amount=None)\n  File "/app/app.py", line 97, in _process_payment\n    raise ValueError("payment_processor.charge() received None for amount")\nValueError: payment_processor.charge() received None for amount' },
      { timestamp: '2026-05-02T19:12:38Z', severity: 'UNKNOWN', message: 'INFO: 127.0.0.1:40394 - "GET /health HTTP/1.1" 200 OK' },
      { timestamp: '2026-05-02T19:12:42Z', severity: 'UNKNOWN', message: 'INFO: 172.20.0.2:34728 - "GET /metrics HTTP/1.1" 200 OK' },
      { timestamp: '2026-05-02T19:12:48Z', severity: 'UNKNOWN', message: 'INFO: 127.0.0.1:56734 - "GET /health HTTP/1.1" 200 OK' },
      { timestamp: '2026-05-02T19:12:57Z', severity: 'UNKNOWN', message: 'INFO: 172.20.0.2:33460 - "GET /metrics HTTP/1.1" 200 OK' },
      { timestamp: '2026-05-02T19:13:05Z', severity: 'ERROR',   message: 'Unhandled exception in payment processor:\nTraceback (most recent call last):\n  File "/app/app.py", line 88, in crash\n    _process_payment(amount=None)\nValueError: payment_processor.charge() received None for amount' },
      { timestamp: '2026-05-02T19:13:08Z', severity: 'UNKNOWN', message: 'INFO: 127.0.0.1:46348 - "GET /health HTTP/1.1" 200 OK' },
    ],
  },

  traces: {
    error: null,
    error_trace_count: 2,
    traces: [
      {
        trace_id:     'abc123def456789012345678',
        root_service: 'service-b',
        duration_ms:  12.4,
        spans: [
          { service_name: 'service-b', operation_name: 'GET /crash',       duration_ms: 12.4, is_error: true,  references: [], tags: { 'http.status_code': '500', 'otel.status_code': 'ERROR' } },
          { service_name: 'service-b', operation_name: '_process_payment', duration_ms: 0.3,  is_error: true,  references: [{ span_id: undefined }], tags: { error: 'ValueError' } },
        ],
      },
      {
        trace_id:     'def456abc123789012345678',
        root_service: 'service-b',
        duration_ms:  8.1,
        spans: [
          { service_name: 'service-b', operation_name: 'GET /health', duration_ms: 8.1, is_error: false, references: [], tags: { 'http.status_code': '200' } },
        ],
      },
      {
        trace_id:     'fed987cba321012345678901',
        root_service: 'service-b',
        duration_ms:  99.2,
        spans: [
          { service_name: 'service-b', operation_name: 'GET /crash',       duration_ms: 99.2, is_error: true, references: [], tags: { 'http.status_code': '500', 'otel.status_code': 'ERROR' } },
          { service_name: 'service-b', operation_name: '_process_payment', duration_ms: 1.1,  is_error: true, references: [{ span_id: undefined }], tags: { error: 'ValueError' } },
        ],
      },
    ],
  },
};
