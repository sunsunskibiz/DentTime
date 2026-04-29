# Handoff Spec: Retrain Trigger with Human-in-the-Loop

**Feature:** Automated model retraining triggered by Prometheus alerts, with human-in-the-loop escalation when no new data is available.  
**Stack:** Python 3.11 · FastAPI · Apache Airflow 2.4+ · Alertmanager · SMTP  
**Based on:** ADR-002 (pipeline scope), ADR-003 (human-in-loop + wait strategy)  
**Status:** Ready for implementation (revised after three critique passes — see changelog at bottom)

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
    ├── InfrastructureError (RAW_DATA_PATH missing)
    │         → respond {"status": "skipped", "reason": "raw_data_missing"}         [State: SKIP]
    │           Log prominent error. Do NOT email — this is a volume-mount problem.
    ├── NO  → send engineer email
    │         trigger denttime_await_data DAG
    │         respond {"status": "waiting", "reason": "no_new_data"}                [State: WAITING]
    └── YES ↓

[C] Debounce: has a retrain been triggered within MIN_RETRAIN_INTERVAL_S?
    │   (checked AFTER the data gate so engineers are still notified about
    │    missing data even when a retrain was recently triggered)
    ├── YES → respond {"status": "debounced", "retry_in_minutes": N}                [State: DEBOUNCED]
    └── NO  ↓

[D] Trigger Feature Engineering DAG
    ├── API error → respond {"status": "error", "reason": "airflow_unreachable"}    [State: ERROR]
    └── OK ↓

[E] Poll Feature Engineering until complete (max 10 min)
    ├── FAILED → send engineer email (pipeline error)
    │            respond {"status": "error", "reason": "feature_engineering_failed"} [State: ERROR]
    ├── TIMEOUT → respond {"status": "error", "reason": "feature_engineering_timeout"} [State: ERROR]
    └── SUCCESS ↓

[F] Trigger ML Retrain DAG
    ├── API error → respond {"status": "error", "reason": "retrain_trigger_failed"} [State: ERROR]
    └── OK → update _last_trigger_ts (inside _debounce_lock)
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

**`requirements.txt` — pinned content:**
```
fastapi==0.111.1
uvicorn[standard]==0.30.1
httpx==0.27.0
aiosmtplib==3.0.1
```
Do not add `requests` or `smtplib` — both are synchronous and block the event loop. `aiosmtplib` is the async SMTP client; `smtplib` (stdlib) must not be used in an async context. Pin versions explicitly; this system is evaluated for reproducibility.

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
| SKIP | 200 | `{"status": "skipped", "reason": "raw_data_missing"}` |
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
| `DentTimeFeatureDriftHigh` | warning | Retrain-worthy → proceed to data freshness gate (needs explicit Alertmanager route — see Section 6) |
| `DentTimeMissingRateHigh` | warning | In `SKIP_ALERTS` — filtered at step [A], no retrain |

**Data freshness check — `check_new_data_available()` return contract:**

This function has three distinct outcomes; callers must handle all three:

```python
class InfrastructureError(Exception):
    """Raised when RAW_DATA_PATH does not exist — volume mount problem, not a data gap."""
    pass

def check_new_data_available() -> bool:
    """
    Returns:
        True  — raw data is newer than features (or features have never been built)
        False — raw data is not newer than features (data gap — trigger WAITING flow)
    Raises:
        InfrastructureError — RAW_DATA_PATH does not exist at all (bad mount, ops problem)
    """
    if not os.path.exists(RAW_DATA_PATH):
        raise InfrastructureError("raw_data_missing")
    if not os.path.exists(FEATURES_PATH):
        return True   # features never built — treat as new data available
    return os.path.getmtime(RAW_DATA_PATH) > os.path.getmtime(FEATURES_PATH)
```

The caller in `/alert` catches `InfrastructureError` → SKIP (log prominently, no email). `False` → WAITING (email + `denttime_await_data`). This keeps the two very different failure modes — infrastructure fault vs. genuine data gap — from collapsing into the same response branch.

**Polling model for step [E] — Feature Engineering wait:**

Use `async def` for the `/alert` endpoint and `asyncio.sleep(10)` between polls. Do NOT use synchronous `time.sleep()` — it blocks the Uvicorn event loop and prevents all other requests (including `/health`) from being served during the polling window.

All outbound HTTP calls use `httpx.AsyncClient`. Do **not** use `requests` — it is synchronous and blocks the event loop on every DAG-trigger call. Remove `requests` from `requirements.txt` entirely.

```python
import asyncio
import httpx

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
    else:
        # Loop exhausted all 60 iterations without a success break.
        # Do NOT fall through to trigger the ML retrain — abort here.
        return {"status": "error", "reason": "feature_engineering_timeout"}
    ...

async def _get_dag_run_state(dag_id: str, run_id: str) -> dict:
    """Pass dag_id explicitly — do not hardcode, this helper is reused for both DAGs."""
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{AIRFLOW_URL}/api/v1/dags/{dag_id}/dagRuns/{run_id}",
            auth=(AIRFLOW_USER, AIRFLOW_PASS), timeout=10,
        )
        r.raise_for_status()  # raises httpx.HTTPStatusError on 4xx/5xx (avoids JSONDecodeError on HTML error pages)
    return r.json()

async def _trigger_dag(dag_id: str, conf: dict) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{AIRFLOW_URL}/api/v1/dags/{dag_id}/dagRuns",
            json={"conf": conf},
            auth=(AIRFLOW_USER, AIRFLOW_PASS), timeout=10,
        )
        r.raise_for_status()  # raises httpx.HTTPStatusError on 4xx/5xx (avoids JSONDecodeError on HTML error pages)
    return r.json()
```

**Module-level state (in-memory):**
```python
_last_trigger_ts: float = 0.0        # Unix timestamp of last successful trigger
_debounce_lock: asyncio.Lock = asyncio.Lock()
# asyncio.Lock() is safe at module level in Python 3.10+. Do not move it into a
# startup event — that would create a new lock per worker, breaking the debounce guarantee.
```

**Lock scope — critical:** The lock wraps only the debounce check and the timestamp update. It must be released **before** the polling loop starts. If the polling loop ran inside the lock, concurrent `/alert` requests would queue behind it for up to 10 minutes — Alertmanager's default webhook timeout is 10 s, so every queued call would time out and trigger retries.

Full endpoint skeleton showing the correct nesting:
```python
@app.post("/alert")
async def receive_alert(request: Request):
    payload = await request.json()
    # [A] Extract firing alerts and filter SKIP_ALERTS
    firing_alerts = [
        a["labels"]["alertname"]
        for a in payload.get("alerts", [])
        if a.get("status") == "firing" and a["labels"].get("alertname") not in SKIP_ALERTS
    ]
    if not firing_alerts:
        return {"status": "skipped", "reason": "no_retrain_worthy_alerts"}

    # [B] check_new_data_available() — WAITING or SKIP
    # ... (InfrastructureError → SKIP, False → WAITING with firing_alerts in response) ...

    # [C] Debounce — atomic check-and-stamp, lock released before any I/O
    async with _debounce_lock:
        now = time.time()
        if now - _last_trigger_ts < MIN_RETRAIN_INTERVAL_S:
            retry_min = int((MIN_RETRAIN_INTERVAL_S - (now - _last_trigger_ts)) / 60)
            return {"status": "debounced", "retry_in_minutes": retry_min}
        # Stamp immediately so a second concurrent request that arrives
        # while we are still in this block sees the updated timestamp.
        _last_trigger_ts = time.time()
    # Lock is released here — polling runs outside the lock

    # [D] Trigger Feature Engineering DAG
    try:
        fe_result = await _trigger_dag("denttime_feature_engineering", conf={})
    except (httpx.HTTPError, httpx.HTTPStatusError) as exc:
        return {"status": "error", "reason": "airflow_unreachable", "detail": str(exc)}
    if "dag_run_id" not in fe_result:
        # Airflow returns {"detail": "..."} on auth failure, 404, etc.
        return {"status": "error", "reason": "airflow_bad_response", "detail": fe_result.get("detail")}
    fe_run_id = fe_result["dag_run_id"]

    # [E] Poll Feature Engineering — runs outside _debounce_lock
    for _ in range(60):            # max 10 min (60 × 10 s)
        await asyncio.sleep(10)
        state = (await _get_dag_run_state("denttime_feature_engineering", fe_run_id)).get("state")
        if state == "success":
            break
        if state == "failed":
            # send pipeline-failure email here — see §3.4 Situation 2
            return {"status": "error", "reason": "feature_engineering_failed"}
    else:
        # send pipeline-failure email here — see §3.4 Situation 2
        return {"status": "error", "reason": "feature_engineering_timeout"}

    # [F] Trigger ML Retrain DAG
    try:
        retrain_result = await _trigger_dag("denttime_retrain", conf={})
    except (httpx.HTTPError, httpx.HTTPStatusError) as exc:
        return {"status": "error", "reason": "retrain_trigger_failed", "detail": str(exc)}
    if "dag_run_id" not in retrain_result:
        return {"status": "error", "reason": "airflow_bad_response", "detail": retrain_result.get("detail")}
    return {
        "status": "triggered",
        "dag_run_id": retrain_result["dag_run_id"],
        "fe_run_id": fe_run_id,
        "alerts": firing_alerts,   # matches the TRIGGERED response contract in the table above
    }
```

`_last_trigger_ts` resets on container restart. Acceptable for current scope — see edge cases section.

---

### 3.2 `denttime_await_data` Airflow DAG

**File to create:** `airflow/dags/denttime_await_data_dag.py`

```
DAG id:            denttime_await_data
Schedule:          None  (triggered externally by retrain_trigger)
Max active runs:   1     (prevent stacking multiple waits)
on_failure_callback: on_await_data_timeout  (see below)
Tags:              ["monitoring", "human-in-loop", "sensor"]
```

**Tasks:**

| Task id | Operator | Config |
|---|---|---|
| `wait_for_raw_data` | `FileSensor` | `filepath`: `/opt/airflow/project/data/raw/data.csv`, `poke_interval`: 300 s, `timeout`: 86400 s (24 h), `mode`: `reschedule` |
| `trigger_feature_engineering` | `TriggerDagRunOperator` | `trigger_dag_id`: `denttime_feature_engineering`, `wait_for_completion`: False, `conf`: pass-through from this DAG's `dag_run.conf` |

**Dependency:** `wait_for_raw_data >> trigger_feature_engineering`

**Why `mode="reschedule"` on FileSensor:** In poke mode the task holds an Airflow worker slot for the full 24-hour window, blocking other DAGs from running. Reschedule mode releases the slot between poke intervals.

**`on_failure_callback` for timeout:** When the FileSensor exceeds its 24 h timeout, Airflow marks the DAG failed with `AirflowSensorTimeout`. Without a callback, this failure is silent to the engineer — the alert stays active in Prometheus but no email fires. Add:

```python
def on_await_data_timeout(context: dict) -> None:
    import smtplib  # sync is fine here — this runs in an Airflow worker, not the FastAPI event loop
    from email.message import EmailMessage
    msg = EmailMessage()
    msg["Subject"] = "[DentTime] Data wait timed out — 24h sensor expired"
    msg["From"] = SMTP_USER
    msg["To"] = ENGINEER_EMAIL
    msg.set_content(
        "The denttime_await_data DAG timed out after 24h waiting for data/raw/data.csv to update.\n"
        f"DAG run: {context['dag_run'].run_id}\n"
        "Action required: supply new data and manually trigger denttime_feature_engineering."
    )
    # ... send via smtplib ...
```

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
    max_active_runs=1,            # prevents concurrent runs writing to the same features/ parquet files
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
Add as `on_success_callback` on the `denttime_retrain` DAG in `denttime_retrain_dag.py`. Body includes: new macro F1 vs. baseline, under-estimation rate vs. baseline, MLflow run link, whether model was promoted to Staging.

Use DAG-level callbacks for both outcomes for consistency: `on_success_callback` for the completion email, `on_failure_callback` for the champion/challenger rejection email (see edge case table). Do **not** place the email in the task body — if `task_export_artifacts` raises mid-execution, an in-body call never fires.

**`send_email()` helper stub** — module-level async helper in `main.py`. Used by both the `/alert` endpoint (Situations 1 and 2) and the DAG callbacks (Situation 3). Must be async; `smtplib` (stdlib) is synchronous and blocks the event loop.

```python
import aiosmtplib
from email.message import EmailMessage

async def send_email(subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = SMTP_USER
    msg["To"] = ENGINEER_EMAIL
    msg["Subject"] = subject
    msg.set_content(body)
    await aiosmtplib.send(
        msg,
        hostname=SMTP_HOST,
        port=SMTP_PORT,
        username=SMTP_USER,
        password=SMTP_PASS,
        start_tls=True,
    )
```

> DAG callbacks (`on_success_callback`, `on_failure_callback`) run inside Airflow workers, not inside the `retrain_trigger` service. For the DAG-side callbacks, use a synchronous SMTP call (`smtplib`) or delegate to a separate Airflow email operator — `aiosmtplib` applies only within the FastAPI service.

**Callback skeleton** (the signature is not obvious to first-time Airflow users):
```python
def on_retrain_success(context: dict) -> None:
    """Called by Airflow when the entire denttime_retrain DAG completes successfully."""
    dag_run = context["dag_run"]
    # Retrieve metrics logged to MLflow in task_export_artifacts via XCom or direct MLflow query
    send_email(
        subject="[DentTime] Retrain complete",
        body=f"DAG run: {dag_run.run_id}\nAirflow UI: {AIRFLOW_URL}/dags/denttime_retrain/...",
    )

def on_retrain_failure(context: dict) -> None:
    """Called by Airflow when any task in denttime_retrain DAG fails (including champion/challenger rejection)."""
    dag_run = context["dag_run"]
    exception = context.get("exception")
    send_email(
        subject="[DentTime] Retrain rejected — new model below baseline",
        body=f"DAG run: {dag_run.run_id}\nReason: {exception}",
    )

with DAG(
    dag_id="denttime_retrain",
    on_success_callback=on_retrain_success,
    on_failure_callback=on_retrain_failure,
    ...
) as dag:
    ...
```

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
Use `AIRFLOW_URL=http://host.docker.internal:8080` to route through the host machine. On Docker Desktop (Mac/Windows) this resolves automatically. On Linux, `host.docker.internal` is not automatically resolvable — the `extra_hosts` entry in the compose snippet (Section 5) adds it via `host-gateway` (Docker 20.10+).

**How `retrain_trigger` reads the shared filesystem:**  
The project root is mounted into `retrain_trigger` at `/opt/airflow/project` (read-only). Both `data/raw/data.csv` and `features/features_train.parquet` are accessed through this mount. The Airflow containers mount the same project root at the same path, so file modification times are consistent.

**How Alertmanager reaches `retrain_trigger`:**  
Both are in the same `Monitoring-Alerting` compose network, so `http://retrain_trigger:5001/alert` resolves correctly by container name.

**How Prometheus reaches Alertmanager:**  
Same compose network — `alertmanager:9093` resolves correctly.

**Design rationale — webhook over polling:** The Prometheus → Alertmanager → webhook pattern was chosen over a simpler cron job querying the Prometheus API directly because: (1) Alertmanager handles deduplication, grouping, and `repeat_interval` natively — replicating this in a cron script adds scope; (2) the webhook fires within one `evaluation_interval` (15 s) of threshold crossing, whereas a polling cron has minimum 1-minute granularity; (3) Alertmanager's routing rules let us filter `MissingRateHigh` before it ever reaches `retrain_trigger`, keeping the service stateless about alert policy.

---

## 5. Docker Compose Changes

> Config details — skip to §7 if you're reviewing the logic first.

Add to `Monitoring-Alerting/docker-compose.yml`:

```yaml
alertmanager:
  image: prom/alertmanager:v0.27.0
  container_name: denttime_alertmanager
  ports:
    - "9093:9093"
  volumes:
    - ./alertmanager/alertmanager.yml:/etc/alertmanager/alertmanager.yml:ro
  # No depends_on: api — alertmanager has no runtime dependency on the inference API.
  # Keeping it would mask alertmanager startup failures if the API is unhealthy.

retrain_trigger:
  build: ./retrain_trigger
  container_name: denttime_retrain_trigger
  ports:
    - "5001:5001"
  extra_hosts:
    - "host.docker.internal:host-gateway"  # Linux compatibility (Docker 20.10+). Mac/Windows resolve this automatically. See Section 4.
  volumes:
    # Mount project root to /opt/airflow/project so RAW_DATA_PATH and FEATURES_PATH
    # resolve to the same physical files as the FileSensor path in denttime_await_data.
    - ../:/opt/airflow/project:ro
  environment:
    - AIRFLOW_URL=http://host.docker.internal:8080
    - AIRFLOW_USER=${AIRFLOW_USER}
    - AIRFLOW_PASS=${AIRFLOW_PASS}
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

Create `Monitoring-Alerting/.env` for secrets, and verify it is listed in `.gitignore` before committing — SMTP and Airflow credentials will ship in git on first commit if this entry is missing:
```
AIRFLOW_USER=admin
AIRFLOW_PASS=admin
SMTP_HOST=smtp.gmail.com
SMTP_USER=denttime-alerts@example.com
SMTP_PASS=your-app-password
ENGINEER_EMAIL=engineer@example.com
```

---

## 6. Alertmanager Configuration

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
        send_resolved: false  # resolved notifications are not retrain-worthy — omitting them prevents spurious trigger calls

inhibit_rules:
  # Intent: suppress warning-severity noise when a critical alert is already firing
  # for the same underlying problem (e.g. MacroF1Drop fires alongside FeatureDriftHigh).
  # All four DentTime alerts have distinct names, so equal: ['alertname'] would never match.
  # Using equal: [] (no label equality required) means: any active critical silences any warning.
  - source_match:
      severity: 'critical'
    target_match:
      severity: 'warning'
    equal: []
```

**Update `prometheus/prometheus.yml`** — add alerting block:

> The `rule_files` entry below references `/etc/prometheus/alerts.yml`. Ensure the existing `prometheus` service in `docker-compose.yml` already has a volume mount: `- ./prometheus/alerts.yml:/etc/prometheus/alerts.yml:ro`. If it doesn't, add it alongside the existing `prometheus.yml` mount.
```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s  # set explicitly to reduce demo timing jitter; Prometheus default is 1m

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

## 7. Edge Cases

| Case | Expected behaviour |
|---|---|
| Alertmanager fires the same alert twice within debounce window | Second call returns `{"status": "debounced"}` — no duplicate DAG run. Protected by `asyncio.Lock` so concurrent requests cannot both pass the check. |
| `data/raw/data.csv` does not exist at all | `check_new_data_available()` raises `InfrastructureError` → **SKIP** with reason `raw_data_missing`. Infrastructure problem (bad mount), not a data-collection gap — no email sent, no `denttime_await_data` triggered. Log a prominent error for ops investigation. |
| Feature Engineering DAG run times out (> 10 min) | Polling loop's `else` clause fires, returns `{"status": "error", "reason": "feature_engineering_timeout"}`, error email sent |
| Feature Engineering DAG fails mid-run | Polling detects `state == "failed"` → error email, ML retrain not triggered |
| ML retrain DAG's champion/challenger gate rejects the new model | `task_evaluate_model` in `denttime_retrain_dag.py` raises `ValueError` to signal rejection. Airflow marks the DAG failed and the DAG-level `on_failure_callback` fires, sending `[DentTime] Retrain rejected — new model below baseline`. Monitoring continues with the current model unmodified. |
| Both FileSensor path AND Dataset path fire (engineer runs pipeline and calls API) | Feature Engineering DAG has `max_active_runs=1` — second trigger is queued, not duplicated |
| `denttime_await_data` times out (no data after 24 h) | DAG fails with `AirflowSensorTimeout` — Airflow UI shows failure, no retrain triggered, alert remains active in Prometheus |
| `retrain_trigger` container restarts mid-debounce | `_last_trigger_ts` resets to 0 → next alert immediately re-triggers. Acceptable at current scale; mitigate by persisting timestamp to a file if this becomes a problem |
| Airflow is down when alert fires | `httpx` raises `ConnectError` → ERROR response logged, email sent, Alertmanager retries after `repeat_interval` (4 h) |
| Engineer triggers data collection but doesn't call Dataset API | FileSensor in `denttime_await_data` detects file mtime change and triggers Feature Engineering DAG anyway |
| Alert fires while a retrain DAG run is already in progress | Debounce check: `_last_trigger_ts` was set when the previous run was triggered, so the new alert is debounced for the remaining interval |
| Feature Engineering or retrain DAG fails after debounce stamp | `_last_trigger_ts` was already updated before polling started (intentional — see lock skeleton). A pipeline failure triggers a 4-hour lockout with no successful retrain. This is the correct behavior: it prevents alert-storm thrashing. The engineer email on failure provides the manual escape hatch. Do not "fix" this by resetting `_last_trigger_ts` on error. |
| `MissingRateHigh` alert fires alongside a critical alert | `MissingRateHigh` is in `SKIP_ALERTS` and filtered out; the critical alert is still processed normally |

---

## 8. Testing Checklist

**Unit tests (`retrain_trigger/test_main.py`):**
- [ ] `check_new_data_available()` returns `True` when raw data mtime > features mtime
- [ ] `check_new_data_available()` returns `False` when raw data mtime ≤ features mtime
- [ ] `check_new_data_available()` raises `InfrastructureError` when `RAW_DATA_PATH` does not exist
- [ ] `check_new_data_available()` returns `True` when `FEATURES_PATH` does not exist
- [ ] `/alert` returns `skipped` with `reason: no_retrain_worthy_alerts` when all alerts are in `SKIP_ALERTS`
- [ ] `/alert` returns `skipped` with `reason: raw_data_missing` (not `waiting`) when `RAW_DATA_PATH` does not exist
- [ ] `/alert` returns `waiting` when no new data, and email is sent
- [ ] `/alert` returns `debounced` within `MIN_RETRAIN_INTERVAL_S`
- [ ] Two concurrent `/alert` requests with valid new data produce exactly one `triggered` and one `debounced` response — verified with `asyncio.gather`
- [ ] `/alert` returns `error` with `reason: airflow_bad_response` when `_trigger_dag` returns a response without `dag_run_id` (mock Airflow returning `{"detail": "DAG not found"}`)
- [ ] `/alert` returns `error` with `reason: airflow_unreachable` when `_trigger_dag` raises `httpx.HTTPError` (mock connection failure)

**DAG structure tests (`tests/dags/test_denttime_await_data_dag.py`):**
- [ ] DAG loads without import errors
- [ ] `dag_id == "denttime_await_data"`
- [ ] Task count == 2 (`wait_for_raw_data`, `trigger_feature_engineering`)
- [ ] `wait_for_raw_data` is a `FileSensor` with `mode="reschedule"` and `timeout=86400`
- [ ] `on_failure_callback` is set on the DAG

**Integration tests (manual — run with monitoring stack up):**
- [ ] `curl http://localhost:5001/health` returns `{"status": "ok"}`
- [ ] Send test payload with `MissingRateHigh` only → returns `skipped`
- [ ] Send test payload with `MacroF1Drop`, no new data → returns `waiting`, engineer email arrives, `denttime_await_data` appears in Airflow UI
- [ ] `touch data/raw/data.csv` while `denttime_await_data` is running → FileSensor unblocks, Feature Engineering DAG triggers
- [ ] Send test payload with `MacroF1Drop`, with new data → Feature Engineering runs, then ML Retrain runs
- [ ] Send same payload again within 4 h → returns `debounced`
- [ ] Run `scripts/run_critical_alert_demo.ps1` end-to-end → Prometheus alerts fire, Alertmanager calls trigger, full pipeline runs

---

## 9. File Tree After Implementation

```
DentTime/
├── airflow/
│   └── dags/
│       ├── datasets.py                          ← NEW
│       ├── denttime_await_data_dag.py           ← NEW
│       ├── denttime_feature_engineering_dag.py  ← MODIFIED (add Dataset schedule + outlet + max_active_runs=1)
│       └── denttime_retrain_dag.py              ← MODIFIED (add Dataset schedule; on_success_callback for completion email; on_failure_callback for champion/challenger rejection email)
├── tests/
│   └── dags/
│       └── test_denttime_await_data_dag.py      ← NEW (DAG structure tests)
│
└── Monitoring-Alerting/
    ├── alertmanager/
    │   └── alertmanager.yml                     ← NEW
    ├── retrain_trigger/
    │   ├── Dockerfile                           ← NEW
    │   ├── main.py                              ← NEW
    │   ├── requirements.txt                     ← NEW (httpx only — no requests)
    │   └── test_main.py                         ← NEW
    ├── prometheus/
    │   └── prometheus.yml                       ← MODIFIED (add global evaluation_interval + alerting block)
    ├── .env                                     ← NEW (gitignored — SMTP secrets)
    └── docker-compose.yml                       ← MODIFIED (add alertmanager, retrain_trigger with extra_hosts)
```

---

## 10. Acceptance Criteria

The feature is complete when:

1. Running `scripts/run_critical_alert_demo.ps1` causes a `denttime_retrain` DAG run to appear in Airflow UI **without any manual intervention**, given that `data/raw/data.csv` has been recently updated.

2. When `data/raw/data.csv` has NOT been updated, the same script causes an email to arrive at `ENGINEER_EMAIL` and a `denttime_await_data` DAG run to appear in Airflow UI in a waiting state.

3. After the engineer runs `touch data/raw/data.csv` (simulating data collection), the `denttime_await_data` DAG unblocks and `denttime_feature_engineering` starts automatically within `poke_interval` (≤ 5 min).

4. Sending the same alert payload twice within 4 hours results in the second call returning `{"status": "debounced"}` with no duplicate DAG runs.

5. `GET http://localhost:9090/alerts` shows alerts in Firing state after running the demo script for at least 1 minute.
