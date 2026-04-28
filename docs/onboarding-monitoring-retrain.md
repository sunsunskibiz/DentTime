# DentTime — Monitoring System & Retrain Trigger: Technical Onboarding

**Audience:** New developer joining the MLOps/monitoring component (P4/P5).  
**Goal:** Understand how the monitoring stack works end-to-end, run the classroom drift demo, and implement the automatic retrain trigger that connects Prometheus alerts to the Airflow DAG.

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
                  └── fires alert → [Section 7: webhook receiver to implement]
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

The monitoring stack lives in `Monitoring-Alerting/`. The retrain DAG lives in `airflow/dags/`. The demo scripts live in `scripts/`.

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
| `denttime_metrics_updater` | — | Runs `update_metrics.py` every 15 s |
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
| `denttime_prediction_class_ratio{slot_minutes=...}` | Proportion of each predicted slot (15/30/45/60/90/105 min) | Detects output distribution shift |

### 3.3 Current state (after running the demo scripts)

After running `run_critical_alert_demo`, your `monitoring/state.json` will look similar to this:

```json
{
  "macro_f1": 0.347,
  "mae_minutes": 45.86,
  "under_estimation_rate": 0.266,
  "input_missing_rate": 0.369,
  "prediction_ratio": {
    "15": 0.033, "30": 0.104, "60": 0.003, "105": 0.861
  }
}
```

This shows a real degraded state: `macro_f1` dropped below baseline, `under_estimation_rate` is 26.6% (well above the +0.05 threshold), and `input_missing_rate` is 37% (far above the 10% warning threshold). These values are what make Prometheus alerts fire.

### 3.4 PSI interpretation

PSI (Population Stability Index) compares the distribution of a feature at training time (reference) vs. live predictions:

| PSI value | Meaning | Action |
|---|---|---|
| < 0.1 | No significant shift | None |
| 0.1 – 0.25 | Minor shift | Monitor closely |
| > 0.25 | Major shift | **Alert fires → trigger retrain** |

PSI is computed in `psi_series()` inside `monitoring/update_metrics.py`. For continuous features it uses quantile-binned histograms; for categorical features it uses direct value frequency comparison.

---

## 4. Alert Rules

Defined in `prometheus/alerts.yml`. All rules evaluate every 1 minute (with `for: 1m` meaning the condition must hold for 1 minute before the alert fires — it passes through a "Pending" state first).

### 4.1 Feature drift (warning)

```yaml
alert: DentTimeFeatureDriftHigh
expr: denttime_feature_psi > 0.25
severity: warning
```

Fires when any monitored feature's PSI exceeds 0.25. This alert is a warning only — it does not by itself trigger a retrain, but it should prompt investigation into what changed in the input data.

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

This is the most business-critical alert. Chronic under-estimation means dentists run late, creating cascading schedule problems. This alert (critical severity) is what should trigger an automatic retrain.

### 4.4 Input quality (warning)

```yaml
alert: DentTimeMissingRateHigh
expr: denttime_input_missing_rate > 0.10
severity: warning
```

Fires when more than 10% of key input fields arrive as null. This indicates a frontend or integration problem — retrain will not help here, so the retrain trigger must skip this alert (see Section 7.2).

---

## 5. Demo Scripts: How to Make Alerts Fire

The `scripts/` directory contains two PowerShell scripts for classroom demos. Run them from the project root with the monitoring stack running.

### 5.1 `run_critical_alert_demo` — triggers critical alerts

```bash
# Windows (PowerShell)
.\scripts\run_critical_alert_demo.ps1

# Windows (Command Prompt)
scripts\run_critical_alert_demo.bat
```

What this script does in two batches:

**Batch 1 — `MACRO_F1_CRITICAL` (130 requests):**
- Sends predictions with an unseen treatment name (`UNSEEN_CRITICAL_DEMO_TREATMENT_...`)
- Labels every prediction with `actual_duration = 45 minutes` via `POST /actual`
- The model will predict mostly 105-minute slots for these heavy inputs, but actual is labeled 45 — this tanks macro F1

**Batch 2 — `UNDER_EST_CRITICAL` (40 requests):**
- Labels predictions with `actual_duration = 180 minutes` (above all model output classes)
- Since every prediction (15/30/60/105) is less than 180, under-estimation rate approaches 100% for this batch

Both batches also omit `doctorId` and `notes`, which raises `input_missing_rate` as a side effect.

After the batches, the script waits 45 seconds for `metrics_updater` to refresh `state.json` and Prometheus to scrape, then prints a summary showing which alerts are expected to fire.

**Expected result:**
```
DentTimeMacroF1Drop         : expected=FIRING
DentTimeUnderEstimationHigh : expected=FIRING
DentTimeMissingRateHigh     : expected=FIRING
```

### 5.2 `run_data_diff_demo` — triggers PSI/data drift

```bash
.\scripts\run_data_diff_demo.ps1

# or with custom size:
.\scripts\run_data_diff_demo.ps1 -Total 80
```

What this script does:

- Sends 80 requests with deliberately shifted feature distributions:
  - **Unseen treatment names** → `treatment_class` becomes the "unknown" class (20), shifting its distribution
  - **`totalAmount = 99999`** → amount far outside training distribution
  - **All 32 tooth numbers** → `tooth_count = 32` vs. typical 1–3
  - **All 5 surfaces** → `surface_count = 5`
  - **Fixed Sunday 02:30** → `appt_day_of_week = 6`, `appt_hour_bucket = 2` — both unusual
  - **No `doctorId`** → `has_dentist_id = 0` shifts that feature's distribution

After 35 seconds the script prints per-feature PSI values. Features like `total_amount`, `tooth_count`, `surface_count`, and `appt_hour_bucket` should show `DRIFT` (PSI > 0.25).

### 5.3 Resetting the database between demos

If you want to reset to a clean state:

```bash
cd Monitoring-Alerting/
docker compose down
rm data/denttime.db          # deletes all predictions
docker compose up -d
```

The database is recreated empty on next startup by `init_db()` in `app/db.py`.

---

## 6. The Retrain DAG

Located at `airflow/dags/denttime_retrain_dag.py`. It has `schedule=None`, meaning it only runs when triggered manually or via API.

### 6.1 Pipeline steps

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
      │  Retrains pruned model, compares with full
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

### 6.2 Important path constants

```python
PROJECT_ROOT = Path("/opt/airflow/project")   # project root inside container
FEATURES     = PROJECT_ROOT / "features"       # features_train.parquet, features_test.parquet
MODEL_DIR    = PROJECT_ROOT / "models"         # model outputs
MLFLOW_TRACKING_URI = "http://mlflow:5000"
```

### 6.3 Triggering manually (for testing)

```bash
# Via Airflow UI
# http://localhost:8080 → DAGs → denttime_retrain → Trigger DAG ▶

# Via Airflow CLI inside container
docker exec airflow-scheduler airflow dags trigger denttime_retrain

# Via Airflow REST API (what the automatic trigger uses)
curl -X POST http://localhost:8080/api/v1/dags/denttime_retrain/dagRuns \
  -H "Content-Type: application/json" \
  -u admin:admin \
  -d '{"conf": {"triggered_by": "manual_test"}}'
```

---

## 7. Implementing the Retrain Trigger

This is the missing link: when Prometheus fires a `critical` alert, something needs to call the Airflow REST API. Here is how to build it.

### 7.1 Architecture of the trigger

```
Prometheus alert fires
        │
        │  webhook (POST alertmanager → receiver)
        ▼
  Alert Receiver (new service: retrain_trigger)
        │  checks: is it a retrain-worthy alert?
        │    ✓ DentTimeMacroF1Drop      → retrain
        │    ✓ DentTimeUnderEstimation  → retrain
        │    ✗ DentTimeMissingRateHigh  → skip (data quality, not model drift)
        │    ✗ DentTimeFeatureDrift     → skip (warning only, monitor first)
        │
        │  debounce: was retrain triggered in last 4 hours?
        ▼
  Airflow REST API
  POST /api/v1/dags/denttime_retrain/dagRuns
```

### 7.2 Step 1 — Add Alertmanager to the monitoring stack

Prometheus alone fires alerts but cannot call webhooks — Alertmanager handles routing and delivery.

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
  repeat_interval: 4h          # don't re-fire retrain every minute
  receiver: 'retrain-trigger'
  routes:
    - match:
        severity: critical
      receiver: 'retrain-trigger'
    - match:
        severity: warning
      receiver: 'null'           # warnings go to Grafana only, no retrain

receivers:
  - name: 'null'
  - name: 'retrain-trigger'
    webhook_configs:
      - url: 'http://retrain_trigger:5001/alert'
        send_resolved: false     # fire on alert only, not on resolution

inhibit_rules:
  - source_match:
      severity: 'critical'
    target_match:
      severity: 'warning'
    equal: ['alertname']
```

Update `prometheus/prometheus.yml` to point at Alertmanager:

```yaml
alerting:
  alertmanagers:
    - static_configs:
        - targets:
          - alertmanager:9093

rule_files:
  - /etc/prometheus/alerts.yml
```

### 7.3 Step 2 — Write the webhook receiver service

Create `Monitoring-Alerting/retrain_trigger/main.py`:

```python
"""
Retrain trigger webhook — receives Alertmanager POST requests
and calls the Airflow REST API to trigger denttime_retrain DAG.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone

import requests
from fastapi import FastAPI, Request

app = FastAPI(title="DentTime Retrain Trigger")
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

AIRFLOW_URL  = os.getenv("AIRFLOW_URL",  "http://airflow-webserver:8080")
AIRFLOW_USER = os.getenv("AIRFLOW_USER", "admin")
AIRFLOW_PASS = os.getenv("AIRFLOW_PASS", "admin")
DAG_ID = "denttime_retrain"

# Debounce: don't trigger more often than this (seconds)
MIN_RETRAIN_INTERVAL_S = int(os.getenv("MIN_RETRAIN_INTERVAL_S", str(4 * 3600)))

# Alerts that should NOT trigger retrain:
#   - DentTimeMissingRateHigh  → data quality issue, retrain won't help
#   - DentTimeFeatureDriftHigh → warning only, needs human review first
SKIP_ALERTS = {"DentTimeMissingRateHigh", "DentTimeFeatureDriftHigh"}

_last_trigger_ts: float = 0.0


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

### 7.4 Step 3 — Add retrain_trigger to docker-compose

```yaml
retrain_trigger:
  build: ./retrain_trigger
  container_name: denttime_retrain_trigger
  ports:
    - "5001:5001"
  environment:
    - AIRFLOW_URL=http://host.docker.internal:8080   # routes through host to reach Airflow stack
    - AIRFLOW_USER=admin
    - AIRFLOW_PASS=admin
    - MIN_RETRAIN_INTERVAL_S=14400
  depends_on:
    - alertmanager
```

> **Network note:** The monitoring stack and the Airflow stack run in separate Docker Compose projects. Using `host.docker.internal:8080` routes through the host machine to reach the Airflow webserver running on port 8080. This works on Docker Desktop (Mac/Windows). On Linux, use `172.17.0.1:8080` or add both compose files to a shared external Docker network.

### 7.5 Step 4 — Enable the Airflow REST API

In `docker/docker-compose.yml`, confirm these env vars are set for the webserver and scheduler:

```yaml
AIRFLOW__API__AUTH_BACKENDS: airflow.api.auth.backend.basic_auth
AIRFLOW__WEBSERVER__EXPOSE_CONFIG: "true"
```

### 7.6 Step 5 — Full end-to-end test

```bash
# 1. Start both stacks
cd Monitoring-Alerting/ && docker compose up -d
cd ../docker && docker compose up -d

# 2. Verify trigger service is healthy
curl http://localhost:5001/health
# → {"status":"ok"}

# 3. Run the critical alert demo to generate real degradation
cd ..
.\scripts\run_critical_alert_demo.ps1

# 4. Check Prometheus for FIRING alerts
# http://localhost:9090/alerts
# Wait up to 1 min after the demo script finishes (alerts use "for: 1m")

# 5. Simulate an Alertmanager webhook payload directly (for fast testing)
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
# → {"status":"triggered","dag_run_id":"manual__2026-04-27T...","alerts":["DentTimeMacroF1Drop"]}

# 6. Verify the DAG run was created in Airflow
curl -s http://localhost:8080/api/v1/dags/denttime_retrain/dagRuns \
  -u admin:admin | python3 -m json.tool | grep -E "dag_run_id|state|start_date"
```

---

## 8. Full Demo Walkthrough (for classroom presentation)

This is the recommended sequence to show the complete monitoring → alert → retrain loop to the instructor.

**Step 1:** Start the monitoring stack and verify it's healthy.
```bash
cd Monitoring-Alerting/
docker compose up --build -d
```
Open http://localhost:3000 (Grafana) and http://localhost:9090/alerts (Prometheus).

**Step 2:** Run the data drift demo to show PSI alerts.
```bash
.\scripts\run_data_diff_demo.ps1
```
After ~35 seconds, Grafana should show elevated PSI on `total_amount`, `tooth_count`, and `appt_hour_bucket`. Prometheus `/alerts` shows `DentTimeFeatureDriftHigh` in Pending or Firing state.

**Step 3:** Run the critical alert demo to show model degradation.
```bash
.\scripts\run_critical_alert_demo.ps1
```
After ~45 seconds, macro F1 drops to ~0.35 and under-estimation rate rises to ~0.27. Prometheus shows `DentTimeMacroF1Drop` and `DentTimeUnderEstimationHigh` as Firing.

**Step 4:** Show that the trigger service receives the alert and fires the DAG. If the retrain trigger is implemented, the Airflow UI at http://localhost:8080 will show a new `denttime_retrain` run created automatically.

**Step 5:** Walk through the Grafana dashboard panels — PSI per feature, macro F1 vs. baseline, under-estimation rate trend, prediction class distribution.

---

## 9. Key Files Reference

| File | What it does |
|---|---|
| `Monitoring-Alerting/monitoring/update_metrics.py` | Reads SQLite predictions, computes PSI/F1/MAE, writes `monitoring/state.json` |
| `Monitoring-Alerting/run_metrics_loop.py` | Runs `update_metrics.py` every 15 s (the `metrics_updater` container entrypoint) |
| `Monitoring-Alerting/app/main.py` | FastAPI: `/predict`, `/actual`, `/metrics` (reads `state.json`, exposes Prometheus metrics) |
| `Monitoring-Alerting/prometheus/alerts.yml` | Alert thresholds (PSI > 0.25, F1 drop > 0.05, etc.) |
| `Monitoring-Alerting/prometheus/prometheus.yml` | Prometheus scrape config (scrapes `:8000/metrics` every 15 s) |
| `Monitoring-Alerting/grafana/dashboards/denttime-monitoring.json` | Pre-provisioned Grafana dashboard |
| `airflow/dags/denttime_retrain_dag.py` | 5-task retrain pipeline with MLflow tracking and feature ranking |
| `scripts/run_critical_alert_demo.ps1` | Sends 170 labeled predictions to trigger DentTimeMacroF1Drop + DentTimeUnderEstimationHigh |
| `scripts/run_data_diff_demo.ps1` | Sends 80 shifted predictions to trigger DentTimeFeatureDriftHigh (PSI > 0.25) |

---

## 10. Common Issues

**`state.json` is empty / metrics show 0:**  
The `metrics_updater` container needs at least one prediction row in SQLite. Run a prediction via the UI or the demo script first.

**Prometheus shows alert as Pending but never Firing:**  
All alert rules use `for: 1m`. The condition must hold continuously for 1 minute. If `metrics_updater` has not refreshed `state.json` with enough data, the metric may fluctuate. Wait the full minute, or check that `metrics_updater` is running: `docker compose ps`.

**Airflow REST API returns 401:**  
Check that `AIRFLOW__API__AUTH_BACKENDS` is set to `airflow.api.auth.backend.basic_auth` in the Airflow compose file.

**DAG run created but immediately fails at `task_load_features`:**  
The features parquet files at `features/features_train.parquet` don't exist yet. Run the `denttime_feature_engineering` DAG first.

**Retrain trigger cannot reach Airflow (`Connection refused`):**  
On Linux, `host.docker.internal` may not resolve. Use `AIRFLOW_URL=http://172.17.0.1:8080` or put both compose stacks on the same Docker external network.

**PSI values are extremely high (> 5.0) in development:**  
Expected when using the demo scripts — they intentionally send out-of-distribution data (e.g., `totalAmount = 99999`, all 32 tooth numbers). In production, PSI is meaningful only with real clinic traffic matched against real training data.

---

## 11. What to Build Next (Technical Debt)

- **Model hot-reload after retrain:** After retrain, the new `model.joblib` goes to `models/` and MLflow "Staging". There is no automated step to copy it to `Monitoring-Alerting/artifacts/model.joblib` and reload it in the running API container. Adding a `POST /reload-model` endpoint or a Docker volume share would close this gap.
- **Persistent debounce state:** The current `_last_trigger_ts` in the retrain trigger resets on container restart. Persist it to a file or Redis to survive restarts.
- **Labeled data accumulation:** Only predictions with a corresponding `POST /actual` call have `actual_slot` filled in SQLite. Automating the feedback loop (linking clinic appointment completion back to predictions) is needed for live F1/MAE monitoring to reflect real performance.
- **Line/email notification:** Add a second Alertmanager receiver that notifies dental clinic stakeholders when retraining is triggered and when the new model is ready.
