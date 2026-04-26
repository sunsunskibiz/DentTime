# ADR-001: Airflow Feature Engineering Pipeline in Docker

**Status:** Proposed  
**Date:** 2026-04-21  
**Deciders:** DentTime Team  
**Context:** SE for ML Term Project — Feature Engineering MLOps

---

## Context

The current feature engineering logic lives in a single monolithic script (`feature_engineering.py`). It runs top-to-bottom with no ability to:

- Rerun only one step (e.g., rebuild `doctor_profile` after new clinic data arrives without re-splitting the raw data)
- Schedule automatic nightly/weekly reruns
- Track run history, failures, or retries
- Run reproducibly on any machine with a single command

The pipeline has 8 logically distinct steps that form a clear dependency graph. Wrapping them in Airflow makes each step independently retriable, observable, and schedulable — core MLOps requirements for the project grading rubric.

### Current Pipeline Steps (from `feature_engineering.py`)

```
Load CSV + Time-based Split + Drop Leakage Columns   [single step]
   │
   ├─────────────────────┬───────────────────────┐
   ▼                     ▼                       ▼
Build Doctor Profile  Build Clinic Profile  Build Treatment Encoding
(train only)          (train only)          (static dict, no train leak)
   │                     │                       │
   └─────────────────────┴───────────────────────┘
                          │
                     ┌────┴────┐
                     ▼         ▼
             Transform Train  Transform Test
             Features         Features
                     │         │
                     └────┬────┘
                          ▼
               Compute Feature Stats (drift baseline)
                          │
                          ▼
                    DVC Add + Push   ← data versioning
```

---

## Decision

**Migrate `feature_engineering.py` to an Apache Airflow DAG with 8 discrete tasks (load+split, doctor profile, clinic profile, treatment encoding, transform train, transform test, feature stats, DVC push), run inside Docker Compose for full portability.**

Each step in the current script becomes a standalone Airflow `PythonOperator` task. Intermediate outputs (split data, profiles, encoded features) are written to a shared mounted volume so tasks are decoupled and individually rerunnable.

---

## Options Considered

### Option A: Airflow DAG with PythonOperator + Docker Compose (Recommended)

| Dimension | Assessment |
|-----------|------------|
| Complexity | Medium |
| Portability | High — single `docker-compose up` on any machine |
| Selective rerun | ✅ Native Airflow "Clear Task" UI |
| Learning curve | Low — stays in Python, no new operator syntax |
| MLflow integration | Easy — call MLflow inside PythonOperator |
| Team familiarity | High — we already write Python tasks |

**Pros:**
- Zero code rewrite — existing functions (`build_doctor_profile`, `FeatureTransformer`) drop in as-is
- Airflow's "Clear" button reruns any single task + its downstream automatically
- Docker Compose makes it fully reproducible on any computer with Docker installed
- Easy to add scheduling (e.g., `schedule_interval="@weekly"`) for batch retraining

**Cons:**
- Requires Docker and 4–8 GB RAM to run Airflow locally
- Airflow setup has boilerplate (docker-compose.yml, config files)

---

### Option B: DockerOperator (one container per task)

Each Airflow task spawns its own Docker container running a Python script.

| Dimension | Assessment |
|-----------|------------|
| Isolation | Very High |
| Complexity | High — Dockerfile per step or per service |
| Portability | High |
| Setup time | 3–4x Option A |

**Cons:** Over-engineered for a dataset of this size. Adds Docker-in-Docker complexity with no benefit for the current scale. Option A achieves the same portability with much less setup.

---

### Option C: Keep single script + cron job

| Dimension | Assessment |
|-----------|------------|
| Complexity | Very Low |
| Selective rerun | ❌ Manual code edits required |
| Observability | ❌ No run history, no retry logic |
| MLOps maturity | Low — doesn't meet course rubric |

**Rejected** — cannot satisfy the "rerun specific steps" requirement and fails the MLOps observability criterion.

---

## Trade-off Analysis

The core tension is **setup complexity vs. operational flexibility**. Option A wins because:

1. **Selective reruns are the explicit requirement.** Airflow's "Clear Task + Downstream" button is exactly this feature — no custom code needed.
2. **Docker Compose is already the deployment strategy** for the FastAPI inference container, so the team gains no new tooling.
3. **Option B's isolation benefit is irrelevant** at this scale — all tasks share the same Python environment and data anyway.

---

## Proposed DAG: `denttime_feature_engineering`

### Task Graph

```
task_load_and_split
(load CSV + time split + drop leakage — all one atomic step)
        │
   ┌────┼────────────────────────┐
   ▼    ▼                        ▼
task_build_doctor_  task_build_clinic_  task_build_treatment_
profile             profile             encoding
(train only)        (train only)        (static dict, no train leak)
   │                    │                    │
   └────────────────────┴────────────────────┘
                         │
             ┌───────────┴───────────┐
             ▼                       ▼
  task_transform_train    task_transform_test
             │                       │
             └───────────┬───────────┘
                         ▼
             task_compute_feature_stats
                         │
                         ▼
                   task_dvc_push
          (dvc add + dvc push → denttime-dvc-store)
```

### Selective Rerun Guide

| "I want to re-run…" | Clear this task | Tasks that re-run |
|---|---|---|
| Everything | Trigger DAG manually | All 8 |
| Doctor profile + downstream | `task_build_doctor_profile` | doctor profile → both transforms → stats → dvc push |
| Clinic profile + downstream | `task_build_clinic_profile` | clinic profile → both transforms → stats → dvc push |
| Treatment encoding + downstream | `task_build_treatment_encoding` | encoding → both transforms → stats → dvc push |
| Both transforms + stats + push | `task_transform_train` | train transform → stats → dvc push |
| Just stats + push | `task_compute_feature_stats` | stats → dvc push |
| Just re-push to DVC | `task_dvc_push` | dvc push only |

> **How it works:** Clearing a task in Airflow automatically marks it and all downstream tasks as "to be re-run." Upstream tasks are unaffected — they keep their previous successful outputs. This is the core reason we chose Airflow over a cron script.

### Data Flow Between Tasks (Shared Volume)

Tasks communicate via files written to a shared volume (not Airflow XCom, to keep large DataFrames out of the metadata DB):

```
/opt/airflow/data/                      ← mounted shared volume
  ├── raw/
  │   └── data.csv                      ← input (mounted read-only)
  ├── interim/
  │   ├── train_split.parquet           ← output of task_load_and_split
  │   └── test_split.parquet            ← output of task_load_and_split
  ├── artifacts/
  │   ├── doctor_profile.json           ← output of task_build_doctor_profile
  │   ├── clinic_profile.json           ← output of task_build_clinic_profile
  │   ├── treatment_dict.json           ← static input (checked into repo)
  │   └── treatment_encoding.json       ← output of task_build_treatment_encoding
  └── features/
      ├── features_train.parquet        ← output of task_transform_train
      ├── features_test.parquet         ← output of task_transform_test
      └── feature_stats.json           ← output of task_compute_feature_stats
```

---

## Implementation Plan

### Phase 1: Project Structure ✅

Create these new files/folders in the repo:

```
DentTime/
├── airflow/
│   ├── dags/
│   │   └── feature_engineering_dag.py   ← NEW: the Airflow DAG
│   └── plugins/                         ← empty, required by Airflow
├── docker/
│   ├── Dockerfile.airflow               ← NEW: extends official Airflow image
│   └── docker-compose.yml               ← NEW: Airflow + Postgres + volume mounts
├── src/
│   └── features/                        ← EXISTING: no changes needed
│       ├── build_profiles.py
│       ├── feature_transformer.py
│       ├── treatment_mapper.py
│       └── tooth_parser.py
├── requirements-airflow.txt             ← NEW: Airflow + project deps
└── feature_engineering.py              ← KEEP: still works standalone
```

> **Design principle:** Keep `feature_engineering.py` working as a standalone script. The DAG wraps it — it doesn't replace it. This means you can always run `python feature_engineering.py` for quick local tests without Docker.

---

### Phase 2: Write the DAG ✅

> **Implementation is complete.** See [`airflow/dags/feature_engineering_dag.py`](../airflow/dags/feature_engineering_dag.py) for the full 8-task DAG with all `task_` functions, path constants, and dependency wiring.

Key design points reflected in the implementation:

- Each task function does a `sys.path.insert(0, "/opt/airflow/project")` so it can import from `src.features.*` inside the container
- The DVC remote is set to `/opt/airflow/dvc-store` (the named Docker volume `denttime-dvc-store`) — `airflow-init` in docker-compose configures this on first run with `dvc remote add -d localremote /opt/airflow/dvc-store`
- `schedule_interval=None` keeps this as a manual-trigger DAG; change to `"@monthly"` when connecting to the batch retraining pipeline
- All tasks use `PythonOperator` — no new Airflow operator syntax required

---

### Phase 3: Dockerfile ✅

> **Implementation is complete.** See [`docker/Dockerfile.airflow`](../docker/Dockerfile.airflow) and [`requirements-airflow.txt`](../requirements-airflow.txt).

Key design decisions reflected in the implementation:

- Extends `apache/airflow:2.9.0-python3.11` — pins both Airflow and Python versions for reproducibility
- Installs `git` and `libgomp1` at the system level (`apt-get`) so DVC and LightGBM/XGBoost work inside the container
- Source code is copied to `/opt/airflow/project/src/` and `PYTHONPATH` is set to `/opt/airflow/project` — matching the path used by `sys.path.insert` in every task function
- `dvc>=3.0` is included in `requirements-airflow.txt` alongside `mlflow>=2.12`, `pandas`, `pyarrow`, and `rapidfuzz`
- The Dockerfile does **not** bundle the data CSV — data is injected at runtime via the shared Docker volume (see Phase 5)

---

### Phase 4: Docker Compose ✅

> **Implementation is complete.** See [`docker/docker-compose.yml`](../docker/docker-compose.yml).

Key design decisions reflected in the implementation:

- **Three named volumes** — `postgres-db` (Airflow metadata), `denttime-data` (shared pipeline data: interim splits, features, stats), and `denttime-dvc-store` (local DVC remote — persists versioned artifacts across `docker compose down/up`). The `denttime-dvc-store` volume is what makes `task_dvc_push` work; without it, every restart would orphan the DVC cache.
- **`airflow-init` configures the DVC remote** on first run via `dvc remote add -d localremote /opt/airflow/dvc-store`, bridging the repo's `.dvc/config` to the Docker volume path.
- **DAG hot-reload** — `../airflow/dags` is bind-mounted so editing the DAG file takes effect immediately without rebuilding the image.
- **Raw data is mounted read-only** (`"../Data Collection:/opt/airflow/data/raw:ro"`) — Airflow can never overwrite source data.
- **LocalExecutor** is used (not CeleryExecutor) — sufficient for single-machine use and avoids the Redis dependency. Switch to KubernetesExecutor if the pipeline scales beyond one machine.
- **Default credentials are `admin / admin`** — acceptable for local development only. Change before running in any shared or cloud environment.

---

### Phase 5: Place Input Data

Before running, copy the anonymized CSV into the shared volume:

```bash
# One-time: copy data into the Docker volume
docker compose -f docker/docker-compose.yml run --rm airflow-scheduler \
  bash -c "mkdir -p /opt/airflow/data/raw && \
           cp /opt/airflow/src/features/artifacts/treatment_dict.json /opt/airflow/data/artifacts/"

# Then copy your CSV (run from project root)
docker cp "Data Collection/data.csv" \
  $(docker compose -f docker/docker-compose.yml ps -q airflow-scheduler):/opt/airflow/data/raw/data.csv
```

---

### Phase 6: Running the Pipeline

```bash
# 1. Start everything (first time takes ~3 min to pull images)
cd docker/
docker compose up --build -d

# 2. Wait for Airflow to be healthy, then open the UI
open http://localhost:8080
# Login: admin / admin

# 3. Trigger the full pipeline from the UI
#    DAGs → denttime_feature_engineering → Trigger DAG ▶

# 4. To rerun only doctor_profile + downstream:
#    Click the run → Grid view → click "build_doctor_profile" task
#    → Clear → Confirm (checks "Also clear downstream")

# 5. Shut down
docker compose down
```

---

## Planned Enhancements

These are open items that would significantly strengthen the submission but are not blockers for the current implementation:

### 1. MLflow Run Logging (High Value)
Wrap the transform tasks in an MLflow run to log feature stats as metrics — turning each pipeline run into a tracked experiment. This directly satisfies the "Experiment Tracking" rubric criterion.

```python
import mlflow
with mlflow.start_run(run_name="feature_engineering"):
    mlflow.log_metrics({"train_rows": len(train_features), "unknown_treatment_rate": unknown_rate})
    mlflow.log_artifact(str(FEATURE_STATS))
```

### 2. Data Validation Task (Medium Value)
Add a `task_validate_inputs` step between `task_load_and_split` and the parallel profile tasks. It should assert the column schema, check null rates against thresholds, and flag if class distribution has shifted. This is the concrete hook for the data drift monitoring story — the feature stats JSON from `task_compute_feature_stats` becomes the baseline to compare future runs against.

### 3. Scheduled Retraining Trigger (Medium Value)
Change `schedule_interval=None` to `schedule_interval="@monthly"` and wire a downstream `denttime_model_retraining` DAG that picks up the new feature Parquets and runs champion vs. challenger evaluation. The feature engineering DAG becomes Stage 1 of the full batch retraining pipeline.

---

## Consequences

**What becomes easier:**
- Any team member can rerun a single broken step without re-running the full pipeline
- Full pipeline history is visible in Airflow UI (who ran what, when, success/failure)
- Adding new feature engineering steps = adding one more `PythonOperator` task
- Running on a new machine = `docker compose up --build`

**What becomes harder:**
- Local development now requires Docker running (mitigated by keeping `feature_engineering.py` working standalone)
- Debugging task failures requires checking Airflow logs UI rather than a simple terminal

**What we'll need to revisit:**
- If data volume grows significantly (>1M rows), replace PythonOperator file I/O with a proper data lake (S3/GCS) and switch to KubernetesExecutor
- The `treatment_dict.json` is currently a static file — future work should make it a versioned artifact managed by DVC

---

## Action Items

1. [x] Create `airflow/dags/feature_engineering_dag.py`
2. [x] Create `docker/Dockerfile.airflow` and `docker/docker-compose.yml`
3. [x] Create `requirements-airflow.txt`
4. [x] Add `task_dvc_push` as the final DAG task (versions all 6 artifacts)
5. [ ] Test `docker compose up --build` locally (allocate ≥6 GB RAM to Docker)
6. [ ] Trigger full DAG run and verify all 8 tasks succeed (including dvc_push)
7. [ ] Test selective rerun: clear `build_doctor_profile` and verify only downstream tasks re-run
8. [ ] (Bonus) Add MLflow logging inside transform tasks
9. [ ] Document the rerun procedure in the presentation slides ("Operations" section)
