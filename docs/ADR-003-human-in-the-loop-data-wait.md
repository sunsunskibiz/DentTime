# ADR-003: Human-in-the-Loop Data Collection and Airflow Wait Strategy

**Status:** Proposed  
**Date:** 2026-04-28  
**Deciders:** MLOps team (P3/P4/P5)  
**Builds on:** ADR-002 (pipeline scope decision — full pipeline gated on new data)

---

## Context

ADR-002 concluded: when a Prometheus alert fires but `data/raw/data.csv` has not been updated since the last feature run, there is no point triggering a retrain — the model produced would be identical to the current one. Instead, the system should escalate to a human.

The proposed flow is:

```
Alert fires + no new data
      │
      ▼
Notify engineer by email
      │
      ▼
Engineer runs data collection pipeline (separate repository)
      │
      ▼
New data lands in data/raw/data.csv
      │
      ▼
Airflow detects new data and automatically proceeds:
Feature Engineering → ML Retrain
```

The open questions are:
1. What Airflow mechanism should wait for the data to arrive?
2. How does the separate data collection pipeline signal completion to DentTime's Airflow?
3. What are the best practices for this human-in-the-loop + cross-repo pattern?

---

## Key Constraint: The Cross-Repo Gap

The data collection pipeline lives in a separate repository and is run by the engineer manually (or on its own schedule). DentTime's Airflow has no direct visibility into when it starts or finishes. The only shared artifact between the two systems is the file `data/raw/data.csv` on the filesystem (or a shared volume).

This makes the **file** the natural, lowest-coupling integration point — no shared message bus, no shared Airflow instance required. Any Airflow waiting mechanism must ultimately key on something the data collection pipeline leaves behind when it finishes.

---

## Decision

**Use Airflow Data-Aware Scheduling (Datasets) as the primary mechanism, with a FileSensor fallback for simpler setups. The email notification goes out immediately when the alert fires with no new data; the engineer's only obligation is to run the data collection pipeline, which produces the file that unblocks the waiting DAG.**

The full flow becomes:

```
Alert fires + no new data
      │
      ├─► Email to engineer (sent immediately by retrain_trigger service)
      │
      └─► Airflow: trigger a "waiting" DAG (denttime_await_data)
                │
                ▼ FileSensor or Dataset trigger
                waits for data/raw/data.csv to be updated
                │
                ▼ on data arrival (automatic)
          Feature Engineering DAG
                │
                ▼
          ML Retrain DAG
```

The engineer does not interact with Airflow at all — they just run the data collection pipeline in its own repo. The file update is the signal.

---

## Airflow Waiting Mechanisms: Options Compared

### Option A: FileSensor

A `FileSensor` polls a file path on a configurable interval until the file's modification time changes (or the file appears).

```python
from airflow.sensors.filesystem import FileSensor

wait_for_data = FileSensor(
    task_id="wait_for_new_raw_data",
    filepath="/opt/airflow/project/data/raw/data.csv",
    poke_interval=300,      # check every 5 minutes
    timeout=60 * 60 * 24,   # give up after 24 hours
    mode="reschedule",      # frees the worker slot between checks
    soft_fail=False,
)
```

**Key point: use `mode="reschedule"` not `mode="poke`.** In poke mode the task holds a worker slot the entire time it waits — on a small Airflow setup this blocks other DAGs. Reschedule mode releases the slot between checks.

**Pros:** Simple, no extra infrastructure, works with any data collection pipeline that writes to the shared filesystem.  
**Cons:** Polling adds latency (up to `poke_interval` delay after data arrives). Does not distinguish "file was touched" from "file has genuinely new content".

---

### Option B: Airflow Data-Aware Scheduling (Datasets) — recommended

Airflow 2.4+ introduced the concept of **Datasets**: named logical data assets that DAGs can produce and consume. A DAG scheduled on a Dataset automatically runs when that Dataset is marked as updated.

```python
from airflow import Dataset

# Define the dataset once (can be in a shared constants file)
RAW_DATA = Dataset("file:///opt/airflow/project/data/raw/data.csv")

# Feature Engineering DAG — triggered when RAW_DATA is updated
with DAG(
    dag_id="denttime_feature_engineering",
    schedule=[RAW_DATA],     # replaces schedule_interval
    ...
) as fe_dag:
    ...

# ML Retrain DAG — triggered when feature files are updated
FEATURES_TRAIN = Dataset("file:///opt/airflow/project/features/features_train.parquet")

with DAG(
    dag_id="denttime_retrain",
    schedule=[FEATURES_TRAIN],
    ...
) as retrain_dag:
    ...
```

When the data collection pipeline finishes writing `data/raw/data.csv`, it calls the Airflow REST API to mark the Dataset as updated:

```bash
# Called at the end of the data collection pipeline (other repo)
curl -X POST http://airflow-host:8080/api/v1/datasets/events \
  -H "Content-Type: application/json" \
  -u admin:admin \
  -d '{"dataset_uri": "file:///opt/airflow/project/data/raw/data.csv"}'
```

Airflow then automatically queues the Feature Engineering DAG. When that DAG completes and its tasks write the output parquet files, they mark `FEATURES_TRAIN` as updated, which automatically queues the ML Retrain DAG. **No sensor polling needed at all — the system is fully event-driven.**

**Pros:**
- No polling latency — DAG starts the moment the Dataset event arrives.
- The dependency chain (raw data → features → model) is visible in the Airflow UI as a Dataset lineage graph.
- The data collection pipeline's only coupling to DentTime is one `curl` call at completion — no shared code, no shared scheduler.
- This is the modern MLOps standard and is what most production ML platforms (Vertex AI Pipelines, SageMaker Pipelines, etc.) implement conceptually.

**Cons:**
- Requires Airflow 2.4+ (verify your Docker image version).
- The data collection pipeline must be modified to call the Airflow API at the end — a small but real cross-team coordination step.
- Dataset events are stored in the Airflow metadata DB; if Airflow is down when the event fires, the trigger is missed (mitigated by the FileSensor fallback in Option C below).

---

### Option C: FileSensor as fallback inside the waiting DAG (belt-and-suspenders)

For the DentTime course project, a pragmatic hybrid is to use both: the Feature Engineering DAG is scheduled on the Dataset (Option B), but a separate `denttime_await_data` DAG triggered by the retrain service contains a FileSensor as a backup. This means the retrain loop works even if the data collection pipeline forgets to call the Airflow API.

```
retrain_trigger fires:
  ├─ emails engineer
  └─ triggers denttime_await_data DAG
           │
           ▼ FileSensor (reschedule mode, 24h timeout)
           waits for data/raw/data.csv mtime to change
           │
           ▼ on file change:
     TriggerDagRunOperator → denttime_feature_engineering
```

Meanwhile, if the data collection pipeline does call the Dataset API, `denttime_feature_engineering` starts independently via Dataset scheduling, which is fine — Airflow deduplicates concurrent runs via `max_active_runs`.

---

### Option D: ExternalTaskSensor (for shared Airflow instances only)

If the data collection pipeline is also an Airflow DAG in the **same** Airflow instance, `ExternalTaskSensor` can watch it directly:

```python
from airflow.sensors.external_task import ExternalTaskSensor

wait_for_collection = ExternalTaskSensor(
    task_id="wait_for_data_collection",
    external_dag_id="data_collection_pipeline",
    external_task_id=None,   # waits for the whole DAG to succeed
    mode="reschedule",
    timeout=60 * 60 * 24,
)
```

**Not recommended for DentTime** because the data collection pipeline is in a separate repo and likely a separate system. Coupling both pipelines to the same Airflow instance creates an operational dependency that is harder to manage across teams.

---

## Email Notification Design

The email goes out from the `retrain_trigger` service immediately when the alert fires with no new data. It should include enough context for the engineer to act without needing to log into any system first.

**Email content:**

```
Subject: [DentTime] Model alert — data collection needed before retrain

Alert(s) fired: DentTimeMacroF1Drop, DentTimeUnderEstimationHigh
Fired at: 2026-04-28T14:32:00Z

Current model metrics:
  Macro F1:           0.347  (baseline: 0.52, threshold: 0.47)
  Under-estimation:   26.6%  (baseline: 12%, threshold: 17%)

Action required:
  Raw data has not been updated since the last feature run.
  Retraining on current data will not improve the model.

  Please run the data collection pipeline from the [data-collection repo].
  When the pipeline completes and data/raw/data.csv is updated,
  the DentTime retrain pipeline will start automatically.

Links:
  Grafana dashboard:  http://localhost:3000/d/denttime-prometheus/...
  Airflow UI:         http://localhost:8080
  Prometheus alerts:  http://localhost:9090/alerts
```

Sending email from the trigger service:

```python
import smtplib
from email.mime.text import MIMEText

def send_engineer_notification(alert_names: list, metrics: dict) -> None:
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASS", "")
    to_addr   = os.getenv("ENGINEER_EMAIL", "")

    body = f"""
Alert(s) fired: {', '.join(alert_names)}

Current model metrics:
  Macro F1:          {metrics.get('macro_f1', 'N/A')}
  Under-estimation:  {metrics.get('under_estimation_rate', 'N/A')}

Raw data has not changed since last feature run.
Please run the data collection pipeline.
DentTime retrain will start automatically when new data arrives.

Airflow UI: http://localhost:8080
    """.strip()

    msg = MIMEText(body)
    msg["Subject"] = f"[DentTime] Model alert — data collection needed"
    msg["From"] = smtp_user
    msg["To"] = to_addr

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.send_message(msg)

    log.info("Engineer notification sent to %s", to_addr)
```

---

## Full Architecture with Human-in-the-Loop

```
┌─────────────────────────────────────────────────────────────┐
│  Monitoring Stack                                           │
│                                                             │
│  SQLite → metrics_updater → state.json → /metrics          │
│                │                                            │
│                ▼                                            │
│         Prometheus                                          │
│         DentTimeMacroF1Drop fires                           │
│                │                                            │
│                ▼                                            │
│         Alertmanager → retrain_trigger:5001/alert           │
│                              │                              │
│                              ▼                              │
│                   check_new_data_available()                │
│                         │           │                       │
│                   YES   │           │  NO                   │
│                         ▼           ▼                       │
│              trigger full        send email to engineer     │
│              pipeline            trigger denttime_await_data│
└─────────────────────────────────────────────────────────────┘
                                        │
                        Engineer receives email
                                        │
                        Runs data collection pipeline
                        (separate repo, separate system)
                                        │
                        data/raw/data.csv updated
                                        │
              ┌─────────────────────────┴───────────────────────┐
              │                                                  │
              ▼  (via Dataset event API call)          ▼  (via FileSensor polling)
     denttime_feature_engineering               denttime_await_data
     auto-triggered by Dataset                 FileSensor detects mtime change
              │                                         │
              └──────────────┬──────────────────────────┘
                             │  (first to complete wins)
                             ▼
                  denttime_feature_engineering runs
                  (7 tasks, ~2 min)
                             │
                             ▼  (Dataset: features_train.parquet updated)
                  denttime_retrain runs
                  (5 tasks, ~10 min)
                             │
                             ▼
                  New model in Staging (MLflow)
                  Engineer notified: retrain complete
```

---

## Why Not Require Engineer to Approve Inside Airflow?

An alternative is to have the engineer approve inside the Airflow UI before the retrain starts (clicking "mark task as success" on a waiting task, or using a `@task.sensor` that checks an approval flag). This adds an explicit human gate before model promotion.

**Rejected for DentTime for three reasons:**

1. The engineer's job is already defined as running the data collection pipeline. Requiring a second action (Airflow UI approval) adds friction without adding safety — the retrain champion/challenger gate in `task_evaluate_model` already prevents a worse model from being promoted automatically.

2. The Airflow UI approval pattern requires the engineer to have Airflow credentials and understand the Airflow UI, which is a higher operational burden than just running the pipeline they own.

3. For the course project, the cleaner separation is: data team owns data, ML team owns training. The file update is the contract between them.

If model promotion to Production (not just Staging) is added later, that is the right place for an explicit human approval gate — not the retrain trigger.

---

## Consequences

**What becomes easier:**
- The engineer's workflow is unchanged: they run the data collection pipeline as they would anyway. DentTime's retrain happens automatically as a downstream effect.
- The Dataset lineage in the Airflow UI makes the data → features → model dependency chain visible and auditable.
- The 24-hour FileSensor timeout creates a natural SLA: if no new data arrives within 24 hours, the waiting DAG fails and the team is notified that the alert remains unresolved.

**What becomes harder:**
- The data collection pipeline (another repo) needs one addition: a `curl` call to the Airflow Dataset API at the end of its run. This requires cross-team coordination.
- SMTP configuration is needed in the trigger service for email delivery (or switch to Alertmanager's native email receiver to avoid adding SMTP to a new service).
- If the data collection pipeline partially succeeds (writes a corrupted or incomplete CSV), the FileSensor triggers anyway. The Feature Engineering DAG should validate the file at `task_load_and_split` and fail fast if data quality is insufficient.

**What we'll need to revisit:**
- When data collection becomes fully automated (scheduled, not human-triggered), the email step disappears and the Dataset scheduling alone drives the full pipeline end-to-end with no human involvement needed.
- If the retrain improves the model, a separate notification back to the engineer ("retrain complete, model in Staging, metrics improved from X to Y") closes the feedback loop and builds trust in the automated system.

---

## Action Items

1. [ ] Upgrade Airflow Docker image to 2.4+ if not already (check: `docker exec airflow-scheduler airflow version`).
2. [ ] Define `RAW_DATA` and `FEATURES_TRAIN` Dataset objects in a shared constants file (e.g., `airflow/dags/datasets.py`).
3. [ ] Add `schedule=[RAW_DATA]` to `denttime_feature_engineering` DAG.
4. [ ] Add `schedule=[FEATURES_TRAIN]` to `denttime_retrain` DAG, and mark feature parquet outputs as producing `FEATURES_TRAIN` Dataset in `task_export_artifacts`.
5. [ ] Create `denttime_await_data` DAG with a FileSensor (reschedule mode, 24h timeout) as fallback, triggered by `retrain_trigger` when no new data is found.
6. [ ] Add `send_engineer_notification()` to `retrain_trigger/main.py` for the no-new-data path.
7. [ ] Add SMTP env vars to the `retrain_trigger` Docker service in `docker-compose.yml`.
8. [ ] Document the one-line API call that the data collection pipeline must add at the end of its run, and share it with the data collection team.
9. [ ] Add a "retrain complete" notification email in `task_export_artifacts` to close the feedback loop to the engineer.
