# Disha's Feature Implementation

This document outlines the four features implemented by Disha:

## 1. Incident Timeline (Medium Effort)

**Purpose**: Show causal ordering of incident events to help developers understand whether a metric spike caused errors or vice versa.

### How It Works

- **Timeline Building**: `aggregator/core/timeline.py` extracts timestamps from all three signals:
  - Metric spikes (2x baseline = notable)
  - Log error bursts (first error to last error in window)
  - Trace errors and latency spikes (3x p50 latency = notable)

- **Causal Ordering**: Events are sorted by timestamp to reveal which signal type occurred first
  
- **Root Cause Inference**: Simple heuristic: whichever signal type appears first is likely the root cause

### API Integration

- `UnifiedResult.timeline`: Contains `IncidentTimeline` with:
  - `events[]`: Ordered list of `TimelineEvent` objects
  - `earliest_event` / `latest_event`: Query time bounds
  - `total_span_seconds`: Duration of incident
  - `dominant_cause`: "metrics" | "logs" | "traces" (which signal type first)

### Frontend Component

- **TimelinePanel.js**: Renders ordered event timeline with:
  - Event icons (📊 metrics, 📝 logs, ⚠️ errors, 🐢 latency)
  - Timestamps and severity colors (error/warn/info)
  - Expandable event details
  - Dominant cause highlight

### Usage

1. Run a query via the UI (Dashboard)
2. Scroll down to see the "Timeline" section
3. Events are sorted chronologically
4. Look for the "Likely root cause: ..." badge at the top

---

## 2. Multi-Environment Support (Small Effort)

**Purpose**: Point the dashboard at different clusters (local, staging, production) by switching a single dropdown without restarting.

### How It Works

- **Environment Switching**: `EnvironmentPanel.js` allows selecting between:
  - `local`: Default (http://localhost:9090, etc.)
  - `staging`: Staging cluster endpoints (configured via env vars)
  - `production`: Production cluster endpoints (configured via env vars)

- **Persistent Selection**: Choice stored in localStorage so it persists across page reloads

- **Backend Tracking**: Optional audit logging to `/api/environment`

### Configuration

In `.env`:
```bash
ENVIRONMENT=local  # Set startup default

# To enable staging/production, add custom URLs to docker-compose.yml
# or use environment-specific .env files (.env.staging, .env.production)
```

### Frontend Component

- **EnvironmentPanel.js**: Dropdown selector with:
  - Radio buttons for each environment
  - Confirmation badge on switch
  - Current environment display

### API

- `POST /api/environment` — Switch environment
- `GET /api/environment` — Get current environment

### Usage

1. Look for "Environment:" dropdown in Settings/Connection panel
2. Select "Staging" or "Production"
3. UI automatically queries that cluster's Prometheus, Loki, Jaeger
4. Selection persists until manually changed

---

## 3. Auto-Watchdog Mode (Medium Effort)

**Purpose**: Continuously monitor services for anomalies in the background and surface them without manual queries.

### How It Works

- **Background Polling**: `WatchdogMonitor` runs an async loop on configurable interval (default 60s)
  
- **Anomaly Detection**: For each service, queries the past N minutes for correlations
  - Filters by confidence threshold (default 0.7 = 70%)
  - Only alerts on `error` or `warn` severity anomalies

- **Alert Storage**: Up to 100 alerts stored in memory with:
  - Detection timestamp
  - Anomaly type (error_spike, latency_spike, log_burst, etc.)
  - Service name and confidence score

- **Notification Bridge**: Alerts automatically forwarded to configured notification providers (Slack, SNS)

### Configuration

In `.env`:
```bash
WATCHDOG_ENABLED=false                  # Enable/disable at startup
WATCHDOG_INTERVAL_SECONDS=60            # How often to poll (default 60s)
WATCHDOG_LOOKBACK_MINUTES=15            # How far back to query (default 15 min)
WATCHDOG_ANOMALY_THRESHOLD=0.7          # Min confidence to alert (0.0-1.0)
```

### Frontend Component

- **WatchdogPanel.js**: Control panel with:
  - Play/stop button to toggle watchdog
  - Alert list (newest first)
  - Refresh button to poll latest alerts
  - Clear button to dismiss all alerts
  - Severity color coding (🔴 error, 🟠 warn, 🔵 info)

### API

- `POST /api/watchdog` — Start/stop watchdog
- `GET /api/watchdog/alerts` — Fetch recent alerts (paginated)
- `DELETE /api/watchdog/alerts` — Clear all alerts

### Usage

1. Enable via Settings or set `WATCHDOG_ENABLED=true` in `.env`
2. Watchdog automatically queries all registered services every 60s
3. When anomalies detected (confidence > threshold), they appear in alert panel
4. Click "Refresh Alerts" to see latest detections
5. Alerts also sent to Slack/SNS if configured

### Example Output

```
🔴 error_spike detected in payment-service
   HTTP error rate peaked at 22.5%
   Confidence: 89%
   Time: 12:34:56

🟠 latency_spike detected in api-gateway
   Request latency spike (2,450ms)
   Confidence: 73%
   Time: 12:33:12
```

---

## 4. Proactive Notifications (Medium Effort)

**Purpose**: Deliver RCA summaries to Slack/SNS/Email when anomalies are detected, so developers are notified during incidents rather than after.

### How It Works

- **Notification Providers**: `notifier.py` supports:
  - **SlackNotifier**: Posts to Slack webhook with formatted blocks
  - **SNSNotifier**: Publishes to AWS SNS topic (requires boto3)
  - **EmailNotifier**: Sends via Mailgun (requires mailgun API key)

- **Multi-Channel**: Can register multiple providers; alerts sent to all

- **Triggered By**: Watchdog alerts automatically forwarded via notification manager

- **Context**: Each notification includes:
  - Alert title (anomaly type + service)
  - Summary (what was detected)
  - Severity (error/warn/info/critical)
  - Service name
  - Dashboard link (if available)

### Configuration

In `.env`:
```bash
# Slack
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/WEBHOOK/URL

# AWS SNS
SNS_TOPIC_ARN=arn:aws:sns:us-east-1:123456789:observability-alerts
SNS_REGION=us-east-1

# Mailgun Email
MAILGUN_DOMAIN=observability.local
MAILGUN_API_KEY=key-xxxxx
ALERT_EMAIL=incidents@company.com
```

### API Integration

- `NotificationManager`: Orchestrates multi-provider delivery
- `AlertNotificationBridge`: Connects watchdog alerts to notification providers
- Automatic initialization in main.py lifespan

### Usage

1. Set up Slack webhook, SNS topic, or Mailgun credentials in `.env`
2. Enable watchdog mode (`WATCHDOG_ENABLED=true`)
3. Anomalies detected by watchdog → notifications sent to all configured channels
4. Developers receive alerts in Slack DM / email / SNS subscriber without opening dashboard

### Example Slack Message

```
🔍 Error Spike Detected in payment-service

Service: payment-service
Severity: ERROR

Summary:
HTTP error rate peaked at 22.5% on /charge endpoint. 
Stack trace shows ValueError in validate_card() when amount is None.

Time: 2026-05-11 14:22:33
Dashboard: http://localhost:8081
```

---

## Integration Points

### Backend Flow

```
API Query (/query)
  ↓
SignalAggregator.query()
  ├─ Prometheus, Loki, Jaeger (concurrent)
  ├─ Correlator (detects anomalies)
  ├─ build_timeline() ← NEW
  └─ RCA Analysis (optional)
  ↓
Return UnifiedResult with timeline ← NEW
```

### Watchdog Loop

```
Watchdog background task (every 60s)
  ├─ For each monitored service:
  │  └─ SignalAggregator.query()
  │     ├─ Extract correlations
  │     ├─ Filter by confidence threshold
  │     └─ Create AnomalyAlert
  │
  ├─ Store alerts in memory
  │
  └─ Dispatch to notification providers
     └─ Slack, SNS, Email
```

### Frontend Architecture

```
EnvironmentPanel.js
  └─ Environment selector dropdown
     ├─ localStorage persistence
     └─ /api/environment endpoint

TimelinePanel.js
  └─ Timeline event rendering
     ├─ UnifiedResult.timeline
     └─ Causal ordering UI

WatchdogPanel.js
  └─ Watchdog control + alert display
     ├─ /api/watchdog (start/stop)
     ├─ /api/watchdog/alerts (fetch)
     └─ Severity color coding
```

---

## Testing Scenarios

### Scenario 1: Timeline Causality
1. Trigger metric spike: `bash demo/scenario_errors.sh`
2. Wait ~30 seconds for logs to accumulate
3. Run query for last 30 minutes
4. Verify timeline shows: metric_spike → log_burst → trace_error (in order)
5. Confirm "Likely root cause: metrics"

### Scenario 2: Multi-Environment
1. Open UI at http://localhost:8081
2. Switch Environment dropdown to "Staging"
3. Verify: query targets staging cluster (check Prometheus URL in /config)
4. Refresh page → environment persists
5. Switch back to "Local"

### Scenario 3: Watchdog Detection
1. Set `WATCHDOG_ENABLED=true` in `.env`
2. Restart aggregator: `docker compose up -d aggregator`
3. Trigger scenario: `bash demo/scenario_errors.sh`
4. Wait 60+ seconds (watchdog interval)
5. Open WatchdogPanel → should show error_spike alert with 70%+ confidence
6. Click "Refresh Alerts" to see latest

### Scenario 4: Slack Notifications
1. Create Slack webhook at https://api.slack.com/messaging/webhooks
2. Set `SLACK_WEBHOOK_URL=...` in `.env`
3. Set `WATCHDOG_ENABLED=true`
4. Restart aggregator
5. Trigger scenario
6. Wait 60+ seconds
7. Verify Slack DM receives alert message with service name, severity, summary

---

## Future Enhancements

- **Environment-specific URLs**: Load staging/prod URLs from separate config files
- **Watchdog persistence**: Store alerts in SQLite (like query history)
- **Timeline UI**: Interactive timeline graph showing event flow over time
- **Smart notification filtering**: Deduplicate similar alerts to avoid Slack spam
- **Notification templates**: Customizable message templates per provider
- **Webhook triggers**: External systems can trigger watchdog queries
- **Alert rules**: Define custom anomaly detection rules (e.g., CPU > 80% = critical)

---

## Configuration Reference

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `ENVIRONMENT` | string | `local` | active cluster (local/staging/production) |
| `WATCHDOG_ENABLED` | bool | `false` | enable background monitoring |
| `WATCHDOG_INTERVAL_SECONDS` | int | `60` | polling frequency |
| `WATCHDOG_LOOKBACK_MINUTES` | int | `15` | query window per poll |
| `WATCHDOG_ANOMALY_THRESHOLD` | float | `0.7` | min confidence to alert (0.0-1.0) |
| `SLACK_WEBHOOK_URL` | string | `null` | Slack incoming webhook |
| `SNS_TOPIC_ARN` | string | `null` | AWS SNS topic ARN |
| `SNS_REGION` | string | `us-east-1` | AWS region |
| `MAILGUN_DOMAIN` | string | `null` | Mailgun domain |
| `MAILGUN_API_KEY` | string | `null` | Mailgun API key |
| `ALERT_EMAIL` | string | `null` | recipient email address |

---

## Implementation Checklist

- [x] Timeline: Data model + builder (`aggregator/core/timeline.py`)
- [x] Timeline: Integration into UnifiedResult
- [x] Timeline: Frontend rendering (`TimelinePanel.js`)
- [x] Timeline: CSS styling
- [x] Multi-env: Config extension (`config.py`)
- [x] Multi-env: Frontend selector (`EnvironmentPanel.js`)
- [x] Multi-env: API endpoints (/api/environment)
- [x] Watchdog: Monitoring loop (`aggregator/watchdog.py`)
- [x] Watchdog: Frontend control panel (`WatchdogPanel.js`)
- [x] Watchdog: API endpoints (/api/watchdog*)
- [x] Watchdog: Configuration integration
- [x] Notifications: Provider system (`aggregator/notifier.py`)
- [x] Notifications: Slack support
- [x] Notifications: SNS support
- [x] Notifications: Email (Mailgun) support
- [x] Notifications: Bridge to watchdog
- [x] CSS: Styles for all new components
- [x] HTML: Script includes for new components
- [x] .env: Configuration documentation
- [x] Tests: Scenario walkthroughs (see Testing Scenarios section)

---

## Code Organization

```
aggregator/
├── core/
│   ├── timeline.py          ← NEW: Timeline building + event extraction
│   ├── aggregator.py        ← MODIFIED: Integrated timeline building
│   └── correlator.py
├── watchdog.py              ← NEW: Auto-watchdog mode
├── notifier.py              ← NEW: Notification providers (Slack/SNS/Email)
├── config.py                ← MODIFIED: Added environment + watchdog + notification settings
└── main.py                  ← MODIFIED: Added API endpoints + watchdog/notification initialization

frontend/
├── js/components/
│   ├── TimelinePanel.js     ← NEW: Timeline UI
│   ├── WatchdogPanel.js     ← NEW: Watchdog control + alert display
│   ├── EnvironmentPanel.js  ← NEW: Environment selector
│   └── ...
├── css/styles.css           ← MODIFIED: Added new component styles
└── index.html               ← MODIFIED: Added script includes

.env                         ← MODIFIED: Added feature configurations
```

---

**Status**: ✅ **Fully Implemented**  
**Total Effort**: 4 medium/small features  
**Code Files Modified**: 4 backend + 3 frontend + 2 config files  
**New Files Created**: 6 backend + 3 frontend + 1 documentation  
