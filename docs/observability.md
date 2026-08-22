# Observability & Remote Logging

The Pico Train Display exports OpenTelemetry (OTel) logs and metrics to **Grafana Cloud** via OTLP HTTP.

---

## 1. Remote Telemetry Architecture

```
+-----------------------------+
|    Pico Train Display       |
|    (src/otel.py + WAL)      |
+--------------+--------------+
               | OTLP HTTP / Protobuf
               v
+-----------------------------+
|    Grafana Cloud OTLP       |
|   (otlp-gateway-prod-...)   |
+--------------+--------------+
               |
               v
+-----------------------------+
|    Grafana Cloud Loki       |
|   (logs-prod-035...)        |
+--------------+--------------+
               ^
               | LogQL Query API
+--------------+--------------+
|     tools/logs.py (CLI)     |
+-----------------------------+
```

---

## 2. Configuration & Credentials

Telemetry credentials can be set via `.env` or environment variables:

| Variable | Description | Example |
| :--- | :--- | :--- |
| `LOKI_URL` | Base URL of Grafana Cloud Loki endpoint | `https://logs-prod-035.grafana.net` |
| `LOKI_USER` | Grafana Cloud Loki user / instance ID | `1280388` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OTLP HTTP ingest gateway | `https://otlp-gateway-prod-gb-south-0.grafana.net/otlp` |
| `OTEL_EXPORTER_OTLP_HEADERS` | Basic Auth header | `Authorization=Basic <base64(USER:TOKEN)>` |

---

## 3. Querying Logs with `tools/logs.py`

A dedicated query CLI tool is located at `tools/logs.py`.

### Prerequisites
```bash
# Optional: create a .env file in repository root
cp .env.example .env
# Fill in your Grafana Cloud token/credentials
```

### Usage Examples
```bash
# Fetch recent logs from the physical device (default: last 2 hours)
python3 tools/logs.py

# Fetch logs from a specific device instance
python3 tools/logs.py --instance 8e288865 --hours 6

# Fetch logs from the simulator
python3 tools/logs.py --env sim --hours 1

# Fetch more lines
python3 tools/logs.py --limit 500 --hours 24
```

---

## 4. LogQL Queries (Grafana Web UI)

In the Grafana Explore UI, you can query streams directly:

```logql
# All logs from the display
{service_name="pico-train-display"}

# Logs from physical device only
{service_name="pico-train-display", deployment_environment_name="device"}

# Logs with rate limits or errors
{service_name="pico-train-display"} |= "failed"
```
