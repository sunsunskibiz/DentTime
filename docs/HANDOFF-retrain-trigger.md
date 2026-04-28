# Handoff Spec: Retrain Trigger with Human-in-the-Loop

**Feature:** Automated model retraining triggered by Prometheus alerts, with human-in-the-loop escalation when no new data is available.  
**Stack:** Python 3.11 · FastAPI · Apache Airflow 2.4+ · Alertmanager · SMTP  
**Based on:** ADR-002 (pipeline scope), ADR-003 (human-in-loop + wait strategy)  
**Status:** Ready for implementation (revised after critique — see changelog at bottom)

---

## 1. Overview

When Prometheus detects model degradation (F1 drop, under-estimation spike, or feature drift), the system must either retrain automatically or notify a human to supply new data first. The retrain only produces a meaningfully different model if `data/raw/data.csv` has been updated since the last feature run.

Three new components must be built and wired together:

| Component | Type | What it does |
|---|---|---|
| `retrain_trigger` | FastAPI service (new container) | Receives Alertmanager webhooks, gates on data freshness, emails engineer or fires full pipeline |
| `denttime_await_data` | Airflow DAG (new file) | FileSensor fallback — waits for `data/raw/data.csv` to update, then chains to Feature Engineering |
| Dataset definitions | Shared Airflow constants (new file) | Declares `RAW_DATA` and `FEATURES_TRAIN` Datasets so DAGs can be scheduled event-driven |

Existing files that require modification:

| File | Change |
|---|---|
| `airflow/dags/denttime_feature_engineering_dag.py` | Add `schedule=[RAW_DATA]` and outlet Dataset |
| `airflow/dags/denttime_retrain_dag.py` | Add `schedule=[FEATURES_TRAIN]` and outlet Dataset in export task |
| `Monitoring-Alerting/prometheus/prometheus.yml` | Add `alerting:` block pointing at Alertmanager |
| `Monitoring-Alerting/docker-compose.yml` | Add `alertmanager` and `retrain_trigger` services |

---

## 2. System Flow and All States

```
Alertmanager POST /alert
         │
         ▼
[A] Filter: any firing alerts not in SKIP_ALERTS?
    ├── NO  → respond {"status": "skipped", "reason": "no_retrain_worthy_alerts"}   [State: SKIP]
    └── YES ↓

[B] Gate: is data/raw/data.csv newer than features/features_train.parquet?
    ├── NO  → send engineer email
    │         trigger denttime_await_data DAG
    │         respond {"status": "waiting", "reason": "no_new_data"}                [State: WAITING]
    └── YES ↓

[C] Debounce: has a retrain been triggered within MIN_RETRAIN_INTERVAL_S?
    ├── YES → respond {"status": "debounced", "retry_in_minutes": N}                [State: DEBOUNCED]
    └── NO  ↓

[D] Trigger Feature Engineering DAG
    ├── API error → respond {"status": "error", "reason": "airflow_unreachable"}    [State: ERROR]
    └── OK ↓

[E] Poll Feature Engineering until complete (max 10 min)
    ├── FAILED → send engineer email (pipeline error)
    │            respond {"status": "error", "reason": "feature_engineering_failed"} [State: ERROR]
    └── SUCCESS ↓

[F] Trigger ML Retrain DAG
    ├── API error → respond {"status": "error", "reason": "retrain_trigger_failed"} [State: ERROR]
    └── OK → update _last_trigger_ts
              respond {"status": "triggered", "dag_run_id": "..."}                  [State: TRIGGERED]
```

### denttime_await_data DAG (parallel path when no new data)

```
[Triggered by retrain_trigger when state = WAITING]
         │
         ▼
FileSensor: watch data/raw/data.csv mtime
    ├── timeout (24h) → DAG FAILED → human must investigate               [State: TIMED_OUT]
    └── file updated ↓

TriggerDagRunOperator → denttime_feature_engineering
    └── [continues in main flow from step D above]
```

---

## 3. Component Specs

### 3.1 `retrain_trigger` FastAPI Service

**Files to create:**
```
Monitoring-Alerting/
  retrain_trigger/
    main.py
    requirements.txt
    Dockerfile
```

**Environment variables (all required):**

| Variable | Default | Description |
|---|---|---|
| `AIRFLOW_URL` | `http://host.docker.internal:8080` | Airflow webserver base URL |
| `AIRFLOW_USER` | `admin` | Airflow basic auth username |
| `AIRFLOW_PASS` | `admin` | Airflow basic auth password |
| `MIN_RETRAIN_INTERVAL_S` | `14400` | Debounce window in seconds (4 h). Must match `repeat_interval` in `alertmanager.yml`. |
| `RAW_DATA_PATH` | `/opt/airflow/project/data/raw/data.csv` | Path to raw data file inside container. Use Airflow's convention so it matches the FileSensor path in `denttime_await_data`. |
| `FEATURES_PATH` | `/opt/airflow/project/features/features_train.parquet` | Path to features file inside container. Same convention. |
| `SMTP_HOST` | — | SMTP server hostname |
| `SMTP_PORT` | `587` | SMTP port |
| `SMTP_USER` | — | SMTP login username |
| `SMTP_PASS` | — | SMTP login password |
| `ENGINEER_EMAIL` | — | Recipient address for escalation emails |

**Endpoints:**

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/alert` | Receives Alertmanager webhook payload |
| `GET` | `/health` | Liveness probe |

**POST `/alert` — request body (Alertmanager format):**
```json
{
  "alerts": [
    {
      "status": "firing",
      "labels": {
        "alertname": "DentTimeMacroF1Drop",
        "severity": "critical"
      }
    }
  ]
}
```

**POST `/alert` — all possible responses:**

| State | HTTP | Body |
|---|---|---|
| SKIP | 200 | `{"status": "skipped", "reason": "no_retrain_worthy_alerts"}` |
| WAITING | 200 | `{"status": "waiting", "reason": "no_new_data", "alerts": [...]}` |
| DEBOUNCED | 200 | `{"status": "debounced", "retry_in_minutes": N}` |
| TRIGGERED | 200 | `{"status": "triggered", "dag_run_id": "...", "fe_run_id": "...", "alerts": [...]}` |
| ERROR | 200 | `{"status": "error", "reason": "...", "detail": "..."}` |

> Return HTTP 200 for all states — Alertmanager retries on non-2xx, which would spam the trigger.

**Constants (define at module level in `main.py`):**
```python
# Alerts filtered out before any processing. Retrain cannot fix these — the root
# cause is upstream data quality, not model or feature distribution.
SKIP_ALERTS: set[str] = {"DentTimeMissingRateHigh"}
```

**Alert routing table:**

| Alert name | Severity in `alerts.yml` | Action |
|---|---|---|
| `DentTimeMacroF1Drop` | critical | Retrain-worthy → proceed to data freshness gate |
| `DentTimeUnderEstimationHigh` | critical | Retrain-worthy → proceed to data freshness gate |
| `DentTimeFeatureDriftHigh` | warning | Retrain-worthy → proceed to data freshness gate (needs explicit Alertmanager route — see Section 5) |
| `DentTimeMissingRateHigh` | warning | In `SKIP_ALERTS` — filtered at step [A], no retrain |

**Data freshness check logic:**
- If `RAW_DATA_PATH` does not exist → skip with reason `raw_data_missing`
- If `FEATURES_PATH` does not exist → treat as new data available (features never built)
- If `RAW_DATA_PATH.mtime > FEATURES_PATH.mtime` → new data available
- Otherwise → WAITING state, email + trigger `denttime_await_data`

**Polling model for step [E] — Feature Engineering wait:**

Use `async def` for the `/alert` endpoint and `asyncio.sleep(10)` between polls. Do NOT use synchronous `time.sleep()` — it blocks the Uvicorn event loop and prevents all other requests (including `/health`) from being served during the polling window.

```python
import asyncio

@app.post("/alert")
async def receive_alert(request: Request):
    ...
    # Step E — poll feature engineering (async, frees event loop between checks)
    for _ in range(60):            # max 10 min (60 × 10 s)
        await asyncio.sleep(10)
        state = (await _get_dag_run_state(fe_run_id)).get("state")
        if state == "success":
            break
        if state == "failed":
            return {"status": "error", "reason": "feature_engineering_failed"}
    ...

async def _get_dag_run_state(run_id: str) -> dict:
    import httpx
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{AIRFLOW_URL}/api/v1/dags/denttime_feature_engineering/dagRuns/{run_id}",
            auth=(AIRFLOW_USER, AIRFLOW_PASS), timeout=10,
        )
    return r.json()
```

Add `httpx` to `requirements.txt` alongside `requests` (used for the non-polling calls).

**Module-level state (in-memory):**
```python
_last_trigger_ts: float = 0.0   # Unix timestamp of last successful trigger
```
This resets on container restart. Acceptable for current scope — see edge cases section.

---

### 3.2 `denttime_await_data` Airflow DAG

**File to create:** `airflow/dags/denttime_await_data_dag.py`

```
DAG id:       denttime_await_data
Schedule:     None  (triggered externally by retrain_trigger)
Max active runs: 1  (prevent stacking multiple waits)
Tags:         ["monitoring", "human-in-loop", "sensor"]
```

**Tasks:**

| Task id | Operator | Config |
|---|---|---|
| `wait_for_raw_data` | `FileSensor` | `filepath`: `/opt/airflow/project/data/raw/data.csv`, `poke_interval`: 300 s, `timeout`: 86400 s (24 h), `mode`: `reschedule` |
| `trigger_feature_engineering` | `TriggerDagRunOperator` | `trigger_dag_id`: `denttime_feature_engineering`, `wait_for_completion`: False, `conf`: pass-through from this DAG's `dag_run.conf` |

**Dependency:** `wait_for_raw_data >> trigger_feature_engineering`

**Why `mode="reschedule"` on FileSensor:** In poke mode the task holds an Airflow worker slot for the full 24-hour window, blocking other DAGs from running. Reschedule mode releases the slot between poke intervals.

**Why `wait_for_completion=False` on TriggerDagRunOperator:** The feature engineering DAG is also wired to run via Dataset scheduling (Option B in ADR-003). Waiting for completion here could create a duplicate wait if both paths fire. Let the Dataset scheduling handle sequencing to the retrain DAG.

---

### 3.3 Airflow Dataset Definitions

**File to create:** `airflow/dags/datasets.py`

```python
from airflow import Dataset

# Canonical Dataset URIs — import this file in all DAGs that produce or consume these.
RAW_DATA      = Dataset("file:///opt/airflow/project/data/raw/data.csv")
FEATURES_TRAIN = Dataset("file:///opt/airflow/project/features/features_train.parquet")
```

**Changes to existing DAGs:**

`denttime_feature_engineering_dag.py`:
```python
from datasets import RAW_DATA, FEATURES_TRAIN

with DAG(
    dag_id="denttime_feature_engineering",
    schedule=[RAW_DATA],          # replaces schedule_interval=None
    ...
) as dag:
    ...
    # In task_transform_train, add outlets so Airflow marks FEATURES_TRAIN as updated:
    task_transform_train = PythonOperator(
        ...,
        outlets=[FEATURES_TRAIN],   # add this line
    )
```

`denttime_retrain_dag.py`:
```python
from datasets import FEATURES_TRAIN

with DAG(
    dag_id="denttime_retrain",
    schedule=[FEATURES_TRAIN],    # replaces schedule_interval=None
    ...
) as dag:
    ...
```

**How the data collection pipeline (other repo) signals completion:**
```bash
# Add this as the final step in the data collection pipeline
curl -X POST http://<airflow-host>:8080/api/v1/datasets/events \
  -H "Content-Type: application/json" \
  -u admin:admin \
  -d '{"dataset_uri": "file:///opt/airflow/project/data/raw/data.csv"}'
```
This is the only coupling between the two repos. No shared code, no shared scheduler.

---

### 3.4 Email Notification

Sent in two situations:

**Situation 1 — No new data (escalation to engineer):**

| Field | Value |
|---|---|
| Subject | `[DentTime] Model alert — data collection needed before retrain` |
| Trigger | WAITING state in `/alert` endpoint |
| Recipient | `ENGINEER_EMAIL` env var |

Body must include: alert names, current metric values (macro F1, under-estimation rate), instruction to run data collection pipeline, Airflow UI link, Grafana link.

**Source of metric values for the email:** Alertmanager's webhook payload only carries alert labels, not metric values. Read metric values from `monitoring/state.json` at the time the email is sent — it is updated every 15 s by `metrics_updater` and is available on the shared volume. Mount path inside `retrain_trigger`: `/opt/airflow/project/Monitoring-Alerting/monitoring/state.json`. If the file is missing or unparseable, omit the metric values from the email body and note "metrics unavailable" rather than failing the email send.

**Situation 2 — Pipeline error (feature engineering or retrain DAG failed):**

| Field | Value |
|---|---|
| Subject | `[DentTime] Retrain pipeline failed — manual review needed` |
| Trigger | ERROR state after DAG failure |
| Recipient | `ENGINEER_EMAIL` env var |

Body must include: which DAG failed, the Airflow run ID, a link to the Airflow UI run log.

**Situation 3 — Retrain complete (feedback loop):**  
Add to `task_export_artifacts` in `denttime_retrain_dag.py`. Body includes: new macro F1 vs. baseline, under-estimation rate vs. baseline, MLflow run link, whether model was promoted to Staging.

---

## 4. Runtime Wiring

The monitoring stack (`Monitoring-Alerting/docker-compose.yml`) and the Airflow stack (`docker/docker-compose.yml`) run as separate Docker Compose projects. Their containers are on different default bridge networks and cannot reach each other by service name.

```
Monitoring-Alerting network (denttime_default):
  denttime_api :8000
  denttime_prometheus :9090
  denttime_alertmanager :9093
  denttime_retrain_trigger :5001
  denttime_grafana :3000
  denttime_metrics_updater

Airflow network (docker_default):
  airflow-webserver :8080
  airflow-scheduler
  postgres
  mlflow :5000
```

**How `retrain_trigger` reaches Airflow:**  
Use `AIRFLOW_URL=http://host.docker.internal:8080` to route through the host machine. This works on Docker Desktop (Mac/Windows). On Linux, use the host's bridge IP: `http://172.17.0.1:8080`.

**How `retrain_trigger` reads the shared filesystem:**  
The project root is mounted into `retrain_trigger` at `/opt/airflow/project` (read-only). Both `data/raw/data.csv` and `features/features_train.parquet` are accessed through this mount. The Airflow containers mount the same project root at the same path, so file modification times are consistent.

**How Alertmanager reaches `retrain_trigger`:**  
Both are in the same `Monitoring-Alerting` compose network, so `http://retrain_trigger:5001/alert` resolves correctly by container name.

**How Prometheus reaches Alertmanager:**  
Same compose network — `alertmanager:9093` resolves correctly.

---

## 5. Docker Compose Changes

Add to `Monitoring-Alerting/docker-compose.yml`:

```yaml
alertmanager:
  image: prom/alertmanager:v0.27.0
  container_name: denttime_alertmanager
  ports:
    - "9093:9093"
  volumes:
    - ./alertmanager/alertmanager.yml:/etc/alertmanager/alertmanager.yml:ro
  depends_on:
    - api

retrain_trigger:
  build: ./retrain_trigger
  container_name: denttime_retrain_trigger
  ports:
    - "5001:5001"
  volumes:
    # Mount project root to /opt/airflow/project so RAW_DATA_PATH and FEATURES_PATH
    # resolve to the same physical files as the FileSensor path in denttime_await_data.
    - ../:/opt/airflow/project:ro
  environment:
    - AIRFLOW_URL=http://host.docker.internal:8080
    - AIRFLOW_USER=admin
    - AIRFLOW_PASS=admin
    - MIN_RETRAIN_INTERVAL_S=14400
    - RAW_DATA_PATH=/opt/airflow/project/data/raw/data.csv
    - FEATURES_PATH=/opt/airflow/project/features/features_train.parquet
    - SMTP_HOST=${SMTP_HOST}
    - SMTP_PORT=587
    - SMTP_USER=${SMTP_USER}
    - SMTP_PASS=${SMTP_PASS}
    - ENGINEER_EMAIL=${ENGINEER_EMAIL}
  depends_on:
    - alertmanager
```

Create `Monitoring-Alerting/.env` (gitignored) for secrets:
```
SMTP_HOST=smtp.gmail.com
SMTP_USER=denttime-alerts@example.com
SMTP_PASS=your-app-password
ENGINEER_EMAIL=engineer@example.com
```

---

## 5. Alertmanager Configuration

**File to create:** `Monitoring-Alerting/alertmanager/alertmanager.yml`

```yaml
global:
  resolve_timeout: 5m

route:
  group_by: ['alertname']
  group_wait: 30s
  group_interval: 5m
  # repeat_interval must stay in sync with MIN_RETRAIN_INTERVAL_S in retrain_trigger env vars.
  # Both are set to 4 h (14400 s). If you change one, change the other.
  repeat_interval: 4h
  receiver: 'retrain-trigger'
  routes:
    # DentTimeFeatureDriftHigh is severity: warning in alerts.yml but IS retrain-worthy.
    # Route it explicitly BEFORE the warning catch-all below, or it would be silenced.
    - match:
        alertname: DentTimeFeatureDriftHigh
      receiver: 'retrain-trigger'
    # DentTimeMissingRateHigh is warning and NOT retrain-worthy — routed to null.
    - match:
        severity: warning
      receiver: 'null'

receivers:
  - name: 'null'
  - name: 'retrain-trigger'
    webhook_configs:
      - url: 'http://retrain_trigger:5001/alert'
        send_resolved: false

inhibit_rules:
  - source_match:
      severity: 'critical'
    target_match:
      severity: 'warning'
    equal: ['alertname']
```

**Update `prometheus/prometheus.yml`** — add alerting block:
```yaml
alerting:
  alertmanagers:
    - static_configs:
        - targets:
          - alertmanager:9093

rule_files:
  - /etc/prometheus/alerts.yml

scrape_configs:
  - job_name: 'denttime'
    scrape_interval: 15s
    static_configs:
      - targets: ['api:8000']
```

---

## 6. Edge Cases

| Case | Expected behaviour |
|---|---|
| Alertmanager fires the same alert twice within debounce window | Second call returns `{"status": "debounced"}` — no duplicate DAG run |
| `data/raw/data.csv` does not exist at all | `check_new_data_available()` returns `False` → WAITING state, email sent |
| Feature Engineering DAG run times out (> 10 min) | Polling loop exits, returns `{"status": "error", "reason": "feature_engineering_timeout"}`, error email sent |
| Feature Engineering DAG fails mid-run | Polling detects `state == "failed"` → error email, ML retrain not triggered |
| ML retrain DAG's champion/challenger gate rejects the new model | `task_evaluate_model` raises `ValueError`, DAG is marked failed by Airflow. `task_export_artifacts` is never reached, so it cannot send an email. The failure notification must come from the DAG's `on_failure_callback`. Add a callback function to `denttime_retrain_dag.py`: `def on_retrain_failure(context): send_email(subject="[DentTime] Retrain rejected — new model below baseline", ...)`. Monitoring continues with the current model unmodified. |
| Both FileSensor path AND Dataset path fire (engineer runs pipeline and calls API) | Feature Engineering DAG has `max_active_runs=1` — second trigger is queued, not duplicated |
| `denttime_await_data` times out (no data after 24 h) | DAG fails with `AirflowSensorTimeout` — Airflow UI shows failure, no retrain triggered, alert remains active in Prometheus |
| `retrain_trigger` container restarts mid-debounce | `_last_trigger_ts` resets to 0 → next alert immediately re-triggers. Acceptable at current scale; mitigate by persisting timestamp to a file if this becomes a problem |
| Airflow is down when alert fires | `requests.post` raises `RequestException` → ERROR response logged, email sent, Alertmanager retries after `repeat_interval` (4 h) |
| Engineer triggers data collection but doesn't call Dataset API | FileSensor in `denttime_await_data` detects file mtime change and triggers Feature Engineering DAG anyway |
| Alert fires while a retrain DAG run is already in progress | Debounce check: `_last_trigger_ts` was set when the previous run was triggered, so the new alert is debounced for the remaining interval |
| `MissingRateHigh` alert fires alongside a critical alert | `MissingRateHigh` is in `SKIP_ALERTS` and filtered out; the critical alert is still processed normally |

---

## 7. Testing Checklist

**Unit tests (`retrain_trigger/test_main.py`):**
- [ ] `check_new_data_available()` returns `True` when raw data mtime > features mtime
- [ ] `check_new_data_available()` returns `False` when raw data mtime ≤ features mtime
- [ ] `check_new_data_available()` returns `False` when `RAW_DATA_PATH` does not exist
- [ ] `check_new_data_available()` returns `True` when `FEATURES_PATH` does not exist
- [ ] `/alert` returns `skipped` when all alerts are in `SKIP_ALERTS`
- [ ] `/alert` returns `waiting` when no new data, and email is sent
- [ ] `/alert` returns `debounced` within `MIN_RETRAIN_INTERVAL_S`

**Integration tests (manual — run with monitoring stack up):**
- [ ] `curl http://localhost:5001/health` returns `{"status": "ok"}`
- [ ] Send test payload with `MissingRateHigh` only → returns `skipped`
- [ ] Send test payload with `MacroF1Drop`, no new data → returns `waiting`, engineer email arrives, `denttime_await_data` appears in Airflow UI
- [ ] `touch data/raw/data.csv` while `denttime_await_data` is running → FileSensor unblocks, Feature Engineering DAG triggers
- [ ] Send test payload with `MacroF1Drop`, with new data → Feature Engineering runs, then ML Retrain runs
- [ ] Send same payload again within 4 h → returns `debounced`
- [ ] Run `scripts/run_critical_alert_demo.ps1` end-to-end → Prometheus alerts fire, Alertmanager calls trigger, full pipeline runs

---

## 8. File Tree After Implementation

```
DentTime/
├── airflow/
│   └── dags/
│       ├── datasets.py                          ← NEW
│       ├── denttime_await_data_dag.py           ← NEW
│       ├── denttime_feature_engineering_dag.py  ← MODIFIED (add Dataset schedule + outlet)
│       └── denttime_retrain_dag.py              ← MODIFIED (add Dataset schedule)
│
└── Monitoring-Alerting/
    ├── alertmanager/
    │   └── alertmanager.yml                     ← NEW
    ├── retrain_trigger/
    │   ├── Dockerfile                           ← NEW
    │   ├── main.py                              ← NEW
    │   └── requirements.txt                     ← NEW
    ├── prometheus/
    │   └── prometheus.yml                       ← MODIFIED (add alerting block)
    ├── .env                                     ← NEW (gitignored — SMTP secrets)
    └── docker-compose.yml                       ← MODIFIED (add alertmanager, retrain_trigger)
```

---

## 9. Acceptance Criteria

The feature is complete when:

1. Running `scripts/run_critical_alert_demo.ps1` causes a `denttime_retrain` DAG run to appear in Airflow UI **without any manual intervention**, given that `data/raw/data.csv` has been recently updated.

2. When `data/raw/data.csv` has NOT been updated, the same script causes an email to arrive at `ENGINEER_EMAIL` and a `denttime_await_data` DAG run to appear in Airflow UI in a waiting state.

3. After the engineer runs `touch data/raw/data.csv` (simulating data collection), the `denttime_await_data` DAG unblocks and `denttime_feature_engineering` starts automatically within `poke_interval` (≤ 5 min).

4. Sending the same alert payload twice within 4 hours results in the second call returning `{"status": "debounced"}` with no duplicate DAG runs.

5. `GET http://localhost:9090/alerts` shows alerts in Firing state after running the demo script for at least 1 minute.
