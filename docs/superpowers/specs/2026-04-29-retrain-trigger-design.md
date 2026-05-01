# Design: Retrain Trigger with Human-in-the-Loop

**Date:** 2026-04-29  
**Branch:** sun/retrain-trigger  
**Based on:** `report/HANDOFF-retrain-trigger.md` (three-pass reviewed spec)  
**Status:** Adapted for actual repo structure

---

## 1. What We're Building

Three new components wired together so Prometheus alerts automatically trigger model retraining (or escalate to a human when new data is needed):

| Component | Type | Purpose |
|---|---|---|
| `retrain_trigger` | FastAPI service | Receives Alertmanager webhooks, gates on data freshness, emails engineer or fires full pipeline |
| `denttime_await_data` DAG | Airflow DAG | FileSensor fallback — waits for `data/raw/data.csv` to update, then chains to Feature Engineering |
| `datasets.py` | Airflow constants | Declares `RAW_DATA` and `FEATURES_TRAIN` Datasets for event-driven DAG scheduling |

---

## 2. System Flow

```
Alertmanager POST /alert
     │
     ▼
[A] Filter SKIP_ALERTS → SKIP if no retrain-worthy alerts
     │
[B] check_new_data_available()
     ├── InfrastructureError (raw data missing) → SKIP (log, no email)
     ├── False (no new data)    → email engineer + trigger denttime_await_data → WAITING
     └── True ↓
[C] Debounce (asyncio.Lock, < MIN_RETRAIN_INTERVAL_S) → DEBOUNCED
     │
[D] Trigger denttime_feature_engineering DAG → ERROR on API failure
     │
[E] Poll Feature Engineering (60× asyncio.sleep(10)) → ERROR on fail/timeout
     │
[F] Trigger denttime_retrain DAG → TRIGGERED
```

All states return HTTP 200 (non-2xx causes Alertmanager to retry and spam).

---

## 3. Directory Structure (adapted for this repo)

The spec assumed a `Monitoring-Alerting/` directory that doesn't exist. All paths are adapted to the actual repo layout:

```
DentTime/
├── retrain_trigger/              ← NEW (at root, alongside prometheus/, monitoring/)
│   ├── main.py
│   ├── requirements.txt
│   └── Dockerfile
├── alertmanager/                 ← NEW (at root, alongside prometheus/)
│   └── alertmanager.yml
├── airflow/dags/
│   ├── datasets.py               ← NEW
│   ├── denttime_await_data_dag.py← NEW
│   ├── feature_engineering_dag.py← MODIFIED (Dataset schedule + outlet + max_active_runs=1)
│   └── denttime_retrain_dag.py   ← MODIFIED (Dataset schedule + callbacks)
├── tests/
│   ├── test_retrain_trigger.py             ← NEW (unit tests for FastAPI service)
│   ├── dags/
│   │   ├── test_denttime_await_data_dag.py ← NEW (DAG structure tests)
│   │   └── test_modified_dags.py           ← NEW (Dataset wiring on modified DAGs)
│   └── integration/
│       └── test_retrain_trigger_integration.py ← NEW (@pytest.mark.integration, needs stack)
├── prometheus/
│   └── prometheus.yml            ← MODIFIED (add alerting block + evaluation_interval)
├── docker-compose.yml            ← MODIFIED (add alertmanager + retrain_trigger to serving profile)
└── .env                          ← MODIFIED (append SMTP vars — already gitignored)
```

---

## 4. Component Specs

### 4.1 `retrain_trigger` FastAPI Service

**`requirements.txt`** (pinned, no `requests` or synchronous `smtplib`):
```
fastapi==0.111.1
uvicorn[standard]==0.30.1
httpx==0.27.0
aiosmtplib==3.0.1
```

**`Dockerfile`:**
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .
EXPOSE 5001
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5001"]
```

**Environment variables:**

| Variable | Value |
|---|---|
| `AIRFLOW_URL` | `http://host.docker.internal:8080` |
| `AIRFLOW_USER` | `admin` |
| `AIRFLOW_PASS` | `admin` |
| `MIN_RETRAIN_INTERVAL_S` | `14400` (4 h — must match `repeat_interval` in alertmanager.yml) |
| `RAW_DATA_PATH` | `/opt/airflow/project/data/raw/data.csv` |
| `FEATURES_PATH` | `/opt/airflow/project/features/features_train.parquet` |
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | `sunalert0383@gmail.com` |
| `SMTP_PASS` | Gmail App Password (from `.env`) |
| `ENGINEER_EMAIL` | `6870038321@student.chula.ac.th` |

**Module-level constants:**
```python
SKIP_ALERTS: set[str] = {"DentTimeMissingRateHigh"}
_last_trigger_ts: float = 0.0
_debounce_lock: asyncio.Lock = asyncio.Lock()   # module-level, safe in Python 3.10+
```

**`check_new_data_available()` — three outcomes:**
```python
class InfrastructureError(Exception): pass

def check_new_data_available() -> bool:
    if not os.path.exists(RAW_DATA_PATH):
        raise InfrastructureError("raw_data_missing")
    if not os.path.exists(FEATURES_PATH):
        return True
    return os.path.getmtime(RAW_DATA_PATH) > os.path.getmtime(FEATURES_PATH)
```

**Critical lock scope:** Lock wraps only the check + timestamp update. Released *before* polling starts. Polling inside the lock would queue concurrent requests for up to 10 min — Alertmanager times out at 10 s.

**Email — two situations from FastAPI:**
- Situation 1 (WAITING): Subject `[DentTime] Model alert — data collection needed before retrain`. Reads metric values from `/opt/airflow/project/monitoring/state.json`; if missing, notes "metrics unavailable".
- Situation 2 (pipeline ERROR): Subject `[DentTime] Retrain pipeline failed — manual review needed`. Includes DAG name, run ID, Airflow UI link.

Uses `aiosmtplib` (async) — never `smtplib` inside FastAPI.

**Endpoints:** `POST /alert` and `GET /health`.

---

### 4.2 `denttime_await_data` DAG

```
DAG id:          denttime_await_data
Schedule:        None (triggered by retrain_trigger)
Max active runs: 1
on_failure_callback: on_await_data_timeout (sends email via smtplib — sync is fine in Airflow workers)

Tasks:
  wait_for_raw_data (FileSensor)
    filepath:       /opt/airflow/project/data/raw/data.csv
    poke_interval:  300 s
    timeout:        86400 s (24 h)
    mode:           reschedule  ← releases worker slot between pokes
  
  trigger_feature_engineering (TriggerDagRunOperator)
    trigger_dag_id:        denttime_feature_engineering
    wait_for_completion:   False  ← Dataset scheduling handles sequencing
    
Dependency: wait_for_raw_data >> trigger_feature_engineering
```

---

### 4.3 Airflow Dataset Definitions (`datasets.py`)

```python
from airflow import Dataset
RAW_DATA       = Dataset("file:///opt/airflow/project/data/raw/data.csv")
FEATURES_TRAIN = Dataset("file:///opt/airflow/project/features/features_train.parquet")
```

**`feature_engineering_dag.py` changes:**
- `schedule=[RAW_DATA]` (replaces `schedule_interval=None`)
- `max_active_runs=1` (prevents concurrent writes to features/ parquets)
- `outlets=[FEATURES_TRAIN]` on `task_transform_train`

**`denttime_retrain_dag.py` changes:**
- `schedule=[FEATURES_TRAIN]` (replaces `schedule=None`)
- `on_success_callback=on_retrain_success` — email with macro F1, MLflow run link
- `on_failure_callback=on_retrain_failure` — email with rejection reason

DAG callbacks use `smtplib` (sync is fine — runs in Airflow worker, not FastAPI event loop).

---

### 4.4 Alertmanager Configuration (`alertmanager/alertmanager.yml`)

All four alert names from `prometheus/alerts.yml` are routed explicitly so the intent is
unambiguous. Alertmanager evaluates routes top-to-bottom, first-match wins.

```yaml
global:
  resolve_timeout: 5m

route:
  group_by: ['alertname']
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h   # must stay in sync with MIN_RETRAIN_INTERVAL_S=14400
  receiver: 'retrain-trigger'   # fallback; should never be reached with explicit routes below
  routes:
    # Critical alerts — retrain-worthy, sent to webhook
    - match: {alertname: DentTimeMacroF1Drop}
      receiver: 'retrain-trigger'
    - match: {alertname: DentTimeUnderEstimationHigh}
      receiver: 'retrain-trigger'
    # Warning but retrain-worthy — must appear BEFORE the severity:warning catch-all
    - match: {alertname: DentTimeFeatureDriftHigh}
      receiver: 'retrain-trigger'
    # Warning and NOT retrain-worthy — silence (data quality issue, retrain won't help)
    - match: {alertname: DentTimeMissingRateHigh}
      receiver: 'null'

receivers:
  - name: 'null'
  - name: 'retrain-trigger'
    webhook_configs:
      - url: 'http://retrain_trigger:5001/alert'
        send_resolved: false   # resolved notifications are not retrain-worthy

inhibit_rules:
  # Suppress warning noise when a critical alert is already firing for the same instance.
  # equal:[] means any active critical silences any warning (all DentTime alerts are distinct names).
  - source_match: {severity: 'critical'}
    target_match: {severity: 'warning'}
    equal: []
```

**Alert routing summary:**

| Alert | Severity | Route | Action in `retrain_trigger` |
|---|---|---|---|
| `DentTimeMacroF1Drop` | critical | `retrain-trigger` | Retrain-worthy → data gate → pipeline |
| `DentTimeUnderEstimationHigh` | critical | `retrain-trigger` | Retrain-worthy → data gate → pipeline |
| `DentTimeFeatureDriftHigh` | warning | `retrain-trigger` | Retrain-worthy → data gate → pipeline |
| `DentTimeMissingRateHigh` | warning | `null` | Never reaches service (filtered at Alertmanager) — also in `SKIP_ALERTS` as defense-in-depth |

---

### 4.5 `docker-compose.yml` additions (serving profile)

```yaml
alertmanager:
  image: prom/alertmanager:v0.27.0
  container_name: denttime_alertmanager
  profiles: [serving]
  ports:
    - "9093:9093"
  volumes:
    - ./alertmanager/alertmanager.yml:/etc/alertmanager/alertmanager.yml:ro

retrain_trigger:
  build: ./retrain_trigger
  container_name: denttime_retrain_trigger
  profiles: [serving]
  ports:
    - "5001:5001"
  extra_hosts:
    - "host.docker.internal:host-gateway"   # Linux (Docker 20.10+); Mac resolves automatically
  volumes:
    - ./:/opt/airflow/project:ro
  env_file:
    - .env
  environment:
    - AIRFLOW_URL=http://host.docker.internal:8080
    - AIRFLOW_USER=admin
    - AIRFLOW_PASS=admin
    - MIN_RETRAIN_INTERVAL_S=14400
    - RAW_DATA_PATH=/opt/airflow/project/data/raw/data.csv
    - FEATURES_PATH=/opt/airflow/project/features/features_train.parquet
    - SMTP_PORT=587
  depends_on:
    - alertmanager
```

SMTP_HOST, SMTP_USER, SMTP_PASS, ENGINEER_EMAIL come from `.env` via `env_file`.

**`prometheus/prometheus.yml` changes:**
- Add `evaluation_interval: 15s` under `global`
- Add `alerting:` block pointing at `alertmanager:9093`

---

## 5. `.env` additions (appended, not replaced)

```
SMTP_HOST=smtp.gmail.com
SMTP_USER=sunalert0383@gmail.com
SMTP_PASS=kfeh gmmo scnl gpoj
ENGINEER_EMAIL=6870038321@student.chula.ac.th
```

`.env` is already in `.gitignore` — safe to add secrets here.

---

## 6. Tests

### 6.1 Unit tests — `tests/test_retrain_trigger.py`

No Docker needed. Uses `httpx.AsyncClient` with `app` directly (ASGI test client) and `unittest.mock` to patch filesystem + Airflow calls.

**`check_new_data_available()` — 4 cases:**
- Returns `True` when raw data mtime > features mtime
- Returns `False` when raw data mtime ≤ features mtime
- Raises `InfrastructureError` when `RAW_DATA_PATH` does not exist
- Returns `True` when `FEATURES_PATH` does not exist (features never built)

**`POST /alert` — happy path:**
- All alerts in `SKIP_ALERTS` → `{"status": "skipped", "reason": "no_retrain_worthy_alerts"}`
- `RAW_DATA_PATH` missing → `{"status": "skipped", "reason": "raw_data_missing"}` (no email sent)
- No new data → `{"status": "waiting"}` + email sent (mock `send_email`)
- New data, first call → `{"status": "triggered", "dag_run_id": "..."}` (mock Airflow)
- Same call within debounce window → `{"status": "debounced", "retry_in_minutes": N}`

**`POST /alert` — error paths:**
- `_trigger_dag` raises `httpx.HTTPError` → `{"status": "error", "reason": "airflow_unreachable"}`
- Airflow returns `{"detail": "DAG not found"}` (no `dag_run_id`) → `{"status": "error", "reason": "airflow_bad_response"}`
- Feature Engineering polling returns `state == "failed"` → `{"status": "error", "reason": "feature_engineering_failed"}`
- Feature Engineering polling exhausts 60 iterations → `{"status": "error", "reason": "feature_engineering_timeout"}`

**Concurrency:**
- Two concurrent `/alert` requests with valid new data: use `asyncio.gather` → exactly one `triggered` and one `debounced` response

**Email content:**
- WAITING email body contains alert name(s)
- `state.json` missing → email still sends with "metrics unavailable" note (no exception)
- `state.json` present → email body contains metric values from file

### 6.2 DAG structure tests — `tests/dags/test_denttime_await_data_dag.py`

No Airflow running needed (import DAG module directly).

- DAG loads without import errors
- `dag_id == "denttime_await_data"`
- `schedule is None`
- `max_active_runs == 1`
- Task count == 2: `wait_for_raw_data`, `trigger_feature_engineering`
- `wait_for_raw_data` is a `FileSensor` with `mode="reschedule"` and `timeout=86400`
- `on_failure_callback` is set on the DAG

### 6.3 DAG structure tests — `tests/dags/test_modified_dags.py`

Verify Dataset wiring on modified DAGs:

- `feature_engineering_dag` has `schedule == [RAW_DATA]`
- `feature_engineering_dag` has `max_active_runs == 1`
- `task_transform_train` has `outlets` containing `FEATURES_TRAIN`
- `denttime_retrain_dag` has `schedule == [FEATURES_TRAIN]`
- `denttime_retrain_dag` has `on_success_callback` set
- `denttime_retrain_dag` has `on_failure_callback` set

### 6.4 Integration tests — `tests/integration/test_retrain_trigger_integration.py`

Marked `@pytest.mark.integration` — skipped unless stack is running. Run with:
```bash
pytest tests/integration/ -m integration -v
```

These tests hit `http://localhost:5001` directly (the real running container):

- `GET /health` → `{"status": "ok"}` with HTTP 200
- `POST /alert` with `DentTimeMissingRateHigh` only → `{"status": "skipped"}`
- `POST /alert` with `DentTimeMacroF1Drop`, Airflow unreachable → `{"status": "error"}` (run when Airflow stack is down)
- `POST /alert` with `DentTimeMacroF1Drop`, valid stack → `{"status": "triggered"}` OR `"debounced"` (either is correct depending on timing)
- Second identical POST within 1 s → `{"status": "debounced"}` (verifies debounce in real process state)

---

## 7. Acceptance Criteria

1. `scripts/run_critical_alert_demo.ps1` causes `denttime_retrain` DAG run without manual intervention (data updated case).
2. No updated data → engineer email arrives + `denttime_await_data` appears in Airflow UI waiting.
3. `touch data/raw/data.csv` unblocks `denttime_await_data` → Feature Engineering starts within 5 min.
4. Second alert within 4 h → `{"status": "debounced"}`, no duplicate DAG runs.
5. `GET http://localhost:9090/alerts` shows Firing alerts after 1 min of demo script.
