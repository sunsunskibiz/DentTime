# DentTime — Monitoring System & Retrain Trigger: Technical Onboarding

**Audience:** New developer joining the MLOps/monitoring component (P4/P5).  
**Goal:** Understand how the monitoring stack works end-to-end, and implement the automatic retrain trigger that connects Prometheus alerts to the Airflow DAG.

---

## 1. Big Picture: How the Four Pieces Fit Together

```
  Clinic staff → Frontend (React :5173)
                       │
                       ▼
            FastAPI backend (:8000)
            ├── POST /predict      → writes to SQLite (denttime.db)
            ├── POST /actual       → records real outcome in SQLite
            └── GET  /metrics      → exposes Prometheus metrics

                       │  scrape every 15 s
                       ▼
                  Prometheus (:9090)
                  ├── evaluates alert rules (alerts.yml)
                  └── fires alert → [YOUR WORK: webhook receiver]
                                          │
                                          ▼
                                   Airflow REST API (:8080)
                                   └── triggers denttime_retrain DAG

                  Prometheus ─────► Grafana (:3000)
                                   └── denttime-monitoring dashboard

  metrics_updater container (runs every 15 s)
  └── update_metrics.py → writes monitoring/state.json
                                │
                                └── read by /metrics endpoint
```

The monitoring stack lives in `Monitoring-Alerting/`. The retrain DAG lives in `airflow/dags/`.

---

## 2. Starting the Stack

### Monitoring stack (Prometheus + Grafana + API)

```bash
cd Monitoring-Alerting/
docker compose up --build -d
```

Services that start:

| Container | Port | Purpose |
|---|---|---|
| `denttime_api` | 8000 | FastAPI inference + /metrics |
| `denttime_prometheus` | 9090 | Metrics scraping + alert evaluation |
| `denttime_grafana` | 3000 | Dashboard UI (admin/admin) |
| `denttime_metrics_updater` | — | Runs `update_metrics.py` in a loop |
| `denttime_frontend` | 5173 | React UI |

### Airflow stack (separate compose)

```bash
cd docker/
docker compose up --build -d
```

Airflow UI: http://localhost:8080 (admin/admin)

---

## 3. What Gets Monitored and Why

### 3.1 Metrics pipeline

Every 15 seconds, `monitoring/update_metrics.py` runs and writes `monitoring/state.json`. The FastAPI `/metrics` endpoint then reads this file and exposes the values as Prometheus Gauges.

The flow is: `SQLite predictions table` → `update_metrics.py` → `state.json` → `/metrics` endpoint → `Prometheus` → `Grafana`.

### 3.2 Metrics exposed

| Prometheus metric | What it measures | Why it matters |
|---|---|---|
| `denttime_feature_psi{feature=...}` | Population Stability Index for each of 17 features | Detects distribution shift between reference (training) data and live traffic |
| `denttime_macro_f1` | Rolling macro F1 on labeled predictions | Overall model accuracy across all time slots |
| `denttime_underestimation_rate` | % of predictions where predicted < actual duration | Business risk: scheduling too short causes dentist overrun |
| `denttime_mae_minutes` | Mean absolute error in minutes | Magnitude of average prediction error |
| `denttime_input_missing_rate` | % of key input fields that are null/empty | Data quality proxy — signals upstream UI or integration problems |
| `denttime_prediction_class_ratio{slot_minutes=...}` | Proportion of each predicted slot (15/30/45/60/90/105 min) | Detects output distribution shift (e.g., model always predicting 30 min) |

### 3.3 PSI interpretation

PSI (Population Stability Index) compares the distribution of a feature at training time (reference) vs. live predictions:

| PSI value | Meaning | Action |
|---|---|---|
| < 0.1 | No significant shift | None |
| 0.1 – 0.25 | Minor shift | Monitor closely |
| > 0.25 | Major shift | **Alert fires → trigger retrain** |

PSI is computed in `psi_series()` inside `update_metrics.py`. For continuous features it uses quantile-binned histograms; for categorical it uses direct value frequency comparison.

---

## 4. Alert Rules

Defined in `prometheus/alerts.yml`. All rules evaluate every 1 minute.

### 4.1 Feature drift (warning)

```yaml
alert: DentTimeFeatureDriftHigh
expr: denttime_feature_psi > 0.25
severity: warning
```

Fires when any monitored feature's PSI exceeds 0.25. In the current `state.json` you'll see that almost all features have extremely high PSI (e.g., `tooth_count: 12.8`) — this is because the mock data in SQLite is synthetic and doesn't match the training reference. In production, this threshold catches real distribution drift.

### 4.2 Model performance drop (critical)

```yaml
alert: DentTimeMacroF1Drop
expr: denttime_macro_f1 < (denttime_macro_f1_baseline - 0.05)
severity: critical
```

Fires when live macro F1 drops more than 0.05 below the baseline recorded in `artifacts/baseline_metrics.json`. The `_task_evaluate_model()` task in the retrain DAG enforces the same threshold before promoting a new model.

### 4.3 Under-estimation rate (critical)

```yaml
alert: DentTimeUnderEstimationHigh
expr: denttime_underestimation_rate > (denttime_underestimation_rate_baseline + 0.05)
severity: critical
```

This is the most business-critical alert. Chronic under-estimation means dentists run late, creating cascading schedule problems. The threshold is baseline + 0.05 to allow for natural variation.

### 4.4 Input quality (warning)

```yaml
alert: DentTimeMissingRateHigh
expr: denttime_input_missing_rate > 0.10
severity: warning
```

Fires when more than 10% of the key input fields (`treatmentSymptoms`, `timeOfDay`, `doctorId`, `toothNumbers`, `notes`) arrive as null. This indicates a frontend or integration problem, not a model problem — retrain won't help here.

---

## 5. The Retrain DAG

Located at `airflow/dags/denttime_retrain_dag.py`. It has `schedule=None`, meaning it only runs when triggered manually or via API.

### 5.1 Pipeline steps

```
task_load_features
      │  Reads features_train.parquet and features_test.parquet
      ▼
task_train_model
      │  Trains XGBoost (300 estimators, max_depth=6, balanced weights)
      │  Logs to MLflow experiment "DentTime_Duration_Prediction"
      │  Saves model.joblib + feature_columns.json
      ▼
task_rank_features
      │  Computes XGBoost (gain/weight/cover) + Permutation importance
      │  Drops features with score < (mean − 0.5·std)
      │  Retrains pruned model, compares with full model
      │  Chooses pruned if macro_f1 difference ≤ 0.01
      ▼
task_evaluate_model
      │  Computes macro_f1, MAE, under_estimation_rate
      │  FAILS (raises ValueError) if new model is >0.05 F1 below baseline
      │  Transitions model to "Staging" in MLflow registry
      ▼
task_export_artifacts
         Writes reference_features.parquet, smoke_test_inputs.json,
         baseline_metrics.json, feature_ranking.csv to models/
         Logs all to MLflow as artifacts
```

### 5.2 Important path constants

```python
PROJECT_ROOT = Path("/opt/airflow/project")   # → project root inside container
FEATURES     = PROJECT_ROOT / "features"       # → features_train.parquet, features_test.parquet
MODEL_DIR    = PROJECT_ROOT / "models"         # → model outputs
MLFLOW_TRACKING_URI = "http://mlflow:5000"
```

These paths assume the Docker compose mount from `docker/docker-compose.yml` which bind-mounts the project root to `/opt/airflow/project`.

### 5.3 Triggering manually (for testing)

```bash
# Via Airflow UI
# http://localhost:8080 → DAGs → denttime_retrain → Trigger DAG ▶

# Via Airflow CLI inside container
docker exec airflow-scheduler airflow dags trigger denttime_retrain

# Via Airflow REST API (what the trigger webhook will use)
curl -X POST http://localhost:8080/api/v1/dags/denttime_retrain/dagRuns \
  -H "Content-Type: application/json" \
  -u admin:admin \
  -d '{"conf": {"triggered_by": "manual_test"}}'
```

---

## 6. Implementing the Retrain Trigger

This is the missing link: when Prometheus fires a `critical` alert, something needs to call the Airflow REST API. Here is how to build it.

### 6.1 Architecture of the trigger

```
Prometheus alert fires
        │
        │  webhook (POST alertmanager → receiver)
        ▼
  Alert Receiver (new Python service or endpoint)
        │  checks: is it a retrain-worthy alert? (severity=critical, not missing_rate)
        │  debounce: was retrain triggered in last N hours?
        ▼
  Airflow REST API
  POST /api/v1/dags/denttime_retrain/dagRuns
        │
        ▼
  denttime_retrain DAG runs
```

### 6.2 Step 1 — Add Alertmanager to the monitoring stack

Prometheus alone fires alerts but cannot call webhooks. You need Alertmanager.

Add to `Monitoring-Alerting/docker-compose.yml`:

```yaml
alertmanager:
  image: prom/alertmanager:v0.27.0
  container_name: denttime_alertmanager
  ports:
    - "9093:9093"
  volumes:
    - ./alertmanager/alertmanager.yml:/etc/alertmanager/alertmanager.yml:ro
  command:
    - '--config.file=/etc/alertmanager/alertmanager.yml'
  depends_on:
    - api
```

Create `Monitoring-Alerting/alertmanager/alertmanager.yml`:

```yaml
global:
  resolve_timeout: 5m

route:
  group_by: ['alertname']
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h          # don't spam retrain every minute
  receiver: 'retrain-trigger'
  routes:
    - match:
        severity: critical
      receiver: 'retrain-trigger'
    - match:
        severity: warning
      receiver: 'null'           # warnings are Grafana-only, no retrain

receivers:
  - name: 'null'
  - name: 'retrain-trigger'
    webhook_configs:
      - url: 'http://retrain_trigger:5001/alert'
        send_resolved: false     # only fire on alert, not on resolution

inhibit_rules:
  - source_match:
      severity: 'critical'
    target_match:
      severity: 'warning'
    equal: ['alertname']
```

Update Prometheus config (`prometheus/prometheus.yml`) to point at Alertmanager:

```yaml
alerting:
  alertmanagers:
    - static_configs:
        - targets:
          - alertmanager:9093

rule_files:
  - /etc/prometheus/alerts.yml
```

### 6.3 Step 2 — Write the webhook receiver service

Create `Monitoring-Alerting/retrain_trigger/main.py`:

```python
"""
Retrain trigger webhook — receives Alertmanager POST requests
and calls the Airflow REST API to trigger denttime_retrain DAG.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from fastapi import FastAPI, Request

app = FastAPI(title="DentTime Retrain Trigger")
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

AIRFLOW_URL = os.getenv("AIRFLOW_URL", "http://airflow-webserver:8080")
AIRFLOW_USER = os.getenv("AIRFLOW_USER", "admin")
AIRFLOW_PASS = os.getenv("AIRFLOW_PASS", "admin")
DAG_ID = "denttime_retrain"

# Debounce: don't trigger more often than this (seconds)
MIN_RETRAIN_INTERVAL_S = int(os.getenv("MIN_RETRAIN_INTERVAL_S", str(4 * 3600)))  # 4 hours

# Alerts that should NOT trigger retrain (data quality issues, not model drift)
SKIP_ALERTS = {"DentTimeMissingRateHigh"}

_last_trigger_ts: float = 0.0  # module-level debounce state


@app.post("/alert")
async def receive_alert(request: Request):
    global _last_trigger_ts
    payload = await request.json()
    alerts = payload.get("alerts", [])
    log.info("Received %d alert(s) from Alertmanager", len(alerts))

    retrain_worthy = [
        a for a in alerts
        if a.get("status") == "firing"
        and a.get("labels", {}).get("alertname") not in SKIP_ALERTS
    ]

    if not retrain_worthy:
        log.info("No retrain-worthy alerts — skipping")
        return {"status": "skipped", "reason": "no retrain-worthy alerts"}

    now = time.time()
    elapsed = now - _last_trigger_ts
    if elapsed < MIN_RETRAIN_INTERVAL_S:
        remaining_min = int((MIN_RETRAIN_INTERVAL_S - elapsed) / 60)
        log.info("Debounce active — next retrain allowed in %d min", remaining_min)
        return {"status": "debounced", "retry_in_minutes": remaining_min}

    alert_names = [a["labels"]["alertname"] for a in retrain_worthy]
    conf = {
        "triggered_by": "prometheus_alert",
        "alert_names": alert_names,
        "triggered_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        resp = requests.post(
            f"{AIRFLOW_URL}/api/v1/dags/{DAG_ID}/dagRuns",
            json={"conf": conf},
            auth=(AIRFLOW_USER, AIRFLOW_PASS),
            timeout=10,
        )
        resp.raise_for_status()
        dag_run_id = resp.json().get("dag_run_id", "unknown")
        _last_trigger_ts = now
        log.info("DAG triggered: %s (run_id: %s)", DAG_ID, dag_run_id)
        return {"status": "triggered", "dag_run_id": dag_run_id, "alerts": alert_names}
    except requests.RequestException as exc:
        log.error("Failed to trigger DAG: %s", exc)
        return {"status": "error", "detail": str(exc)}


@app.get("/health")
def health():
    return {"status": "ok"}
```

Create `Monitoring-Alerting/retrain_trigger/requirements.txt`:

```
fastapi==0.115.0
uvicorn==0.30.6
requests==2.32.3
```

Create `Monitoring-Alerting/retrain_trigger/Dockerfile`:

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5001"]
```

### 6.4 Step 3 — Add retrain_trigger to docker-compose

```yaml
retrain_trigger:
  build: ./retrain_trigger
  container_name: denttime_retrain_trigger
  ports:
    - "5001:5001"
  environment:
    - AIRFLOW_URL=http://airflow-webserver:8080
    - AIRFLOW_USER=admin
    - AIRFLOW_PASS=admin
    - MIN_RETRAIN_INTERVAL_S=14400   # 4 hours
  depends_on:
    - alertmanager
```

> **Network note:** The monitoring stack and the Airflow stack run in separate Docker Compose projects, so their containers cannot reach each other by service name by default. You have two options:
> - Add both compose files to the same Docker network (recommended for dev/demo): add `networks: [denttime]` to both and define `networks: { denttime: { external: true } }`.
> - Or point `AIRFLOW_URL` to `http://host.docker.internal:8080` to go through the host machine.

### 6.5 Step 4 — Enable the Airflow REST API

By default, Airflow 2.x requires authentication for the REST API. Confirm in `docker/docker-compose.yml` that these env vars are set for the webserver:

```yaml
AIRFLOW__API__AUTH_BACKENDS: airflow.api.auth.backend.basic_auth
AIRFLOW__WEBSERVER__EXPOSE_CONFIG: "true"
```

### 6.6 Step 5 — Test the end-to-end flow

```bash
# 1. Bring up both stacks
cd Monitoring-Alerting/ && docker compose up -d
cd ../docker && docker compose up -d

# 2. Check trigger service health
curl http://localhost:5001/health

# 3. Simulate an Alertmanager payload to the trigger
curl -X POST http://localhost:5001/alert \
  -H "Content-Type: application/json" \
  -d '{
    "alerts": [{
      "status": "firing",
      "labels": {
        "alertname": "DentTimeMacroF1Drop",
        "severity": "critical"
      }
    }]
  }'

# 4. Verify a DAG run was created in Airflow
curl -s http://localhost:8080/api/v1/dags/denttime_retrain/dagRuns \
  -u admin:admin | python3 -m json.tool | grep -E "dag_run_id|state|start_date"
```

Expected output from step 3:

```json
{
  "status": "triggered",
  "dag_run_id": "manual__2026-04-27T...",
  "alerts": ["DentTimeMacroF1Drop"]
}
```

---

## 7. Key Files Reference

| File | What it does |
|---|---|
| `Monitoring-Alerting/monitoring/update_metrics.py` | Reads SQLite predictions, computes PSI/F1/MAE, writes `state.json` |
| `Monitoring-Alerting/run_metrics_loop.py` | Runs `update_metrics.py` every 15 s (the `metrics_updater` container entrypoint) |
| `Monitoring-Alerting/app/main.py` | FastAPI: `/predict`, `/actual`, `/metrics` (reads `state.json`, exposes Prometheus metrics) |
| `Monitoring-Alerting/prometheus/alerts.yml` | Alert thresholds (PSI > 0.25, F1 drop > 0.05, etc.) |
| `Monitoring-Alerting/prometheus/prometheus.yml` | Prometheus scrape config (scrapes `:8000/metrics` every 15 s) |
| `Monitoring-Alerting/grafana/dashboards/denttime-monitoring.json` | Pre-provisioned Grafana dashboard |
| `airflow/dags/denttime_retrain_dag.py` | 5-task retrain pipeline with MLflow tracking and feature ranking |
| `airflow/dags/retrain_dag.py` | Simpler 4-task retrain pipeline (without feature ranking) |

---

## 8. Common Issues

**`state.json` is empty / metrics show 0:**  
The `metrics_updater` container needs the SQLite database to exist and have at least one prediction row. Make a prediction via the UI or `POST /predict` first.

**PSI values are extremely high (> 5.0) in development:**  
This is expected. The `reference_features.parquet` was built from training data; the mock predictions in the dev SQLite are synthetic. PSI is meaningful only when `denttime.db` accumulates real clinic traffic.

**Airflow REST API returns 401:**  
Check that `AIRFLOW__API__AUTH_BACKENDS` is set to `airflow.api.auth.backend.basic_auth` in the Airflow compose file and that the credentials match `AIRFLOW_USER`/`AIRFLOW_PASS` in the trigger service.

**DAG run created but immediately fails at `task_load_features`:**  
The features parquet files at `features/features_train.parquet` don't exist yet. Run the `denttime_feature_engineering` DAG first.

**Retrain trigger fires but Airflow container is on a different network:**  
Use `AIRFLOW_URL=http://host.docker.internal:8080` in the trigger service environment to route through the host instead of by container name.

---

## 9. What to Build Next (Technical Debt)

- **Model promotion from Staging → Production:** After retrain, the new model goes to MLflow "Staging". There is no automated step to copy `models/model.joblib` into `Monitoring-Alerting/artifacts/model.joblib` and reload it in the running API. This hot-reload step is the next implementation gap.
- **Persistent alert history:** The current debounce state (`_last_trigger_ts`) lives in memory and resets on container restart. Persist it to a file or Redis.
- **Labeled data accumulation:** The `POST /actual` endpoint records real outcomes, but only ~0 rows currently have `actual_slot` filled. Automating this feedback loop (e.g., linking clinic appointment records back to predictions) is required for meaningful live F1/MAE monitoring.
- **Alertmanager → Line/email notification:** For the dental clinic stakeholders, add a second Alertmanager receiver that sends a Line message or email summarizing the retrain outcome.
