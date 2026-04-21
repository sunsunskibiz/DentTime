# Airflow Feature Engineering Pipeline — Design Spec

**Date:** 2026-04-21  
**Status:** Approved  
**Supersedes:** ADR-001 (fixes 4 issues found in review)

---

## Goal

Migrate `feature_engineering.py` into an Apache Airflow DAG with 7 discrete, independently-rerunnable tasks, running inside Docker Compose for full portability. DVC data versioning remains a host-side workflow — not inside Airflow.

---

## ADR-001 Issues Fixed

| # | Original ADR | Fix |
|---|---|---|
| 1 | `schedule_interval=None` (deprecated) | `schedule=None` (Airflow 2.4+) |
| 2 | "versions 6 artifacts" but diagram showed 8 | `task_dvc_push` removed; DVC is a host-side concern |
| 3 | `.dvc/` config mounting strategy unclear | Project root bind-mount gives container `.dvc/` naturally |
| 4 | `treatment_dict.json` copy is a fragile manual step | Covered automatically by project root bind-mount |
| 5 | `task_dvc_push` runs DVC+git inside Airflow | Dropped entirely — Airflow and DVC should not mix |

---

## Architecture

### Separation of Concerns

| Tool | Responsibility |
|---|---|
| Airflow | Orchestrate the 7-task feature pipeline, write outputs to host paths |
| DVC | Version and push data files — runs on host after pipeline, not inside DAG |
| Git | Track DVC pointer files and source code |

### Why No `task_dvc_commit`

`dvc add` rewrites `.dvc` pointer files and `.gitignore` (Git working tree side-effects), copies files into `.dvc/cache/` (slow hidden I/O), and creates atomicity risk if `dvc push` fails after `dvc add` succeeds. Airflow tasks should produce data, not modify version control. A `make dvc-commit` target on the host is the correct boundary.

---

## Deliverables

```
DentTime/
├── requirements-airflow.txt          NEW
├── docker/
│   ├── Dockerfile.airflow            NEW
│   └── docker-compose.yml            NEW
├── airflow/
│   └── dags/
│       └── feature_engineering_dag.py  NEW
└── .claude/
    └── agents/
        └── denttime-airflow-critic.md  NEW
```

`feature_engineering.py` is unchanged — still works standalone.

---

## Section 1: Docker Infrastructure

### `requirements-airflow.txt`

```
pandas>=2.0
pyarrow>=14.0
rapidfuzz>=3.0
dvc>=3.0
mlflow>=2.12
```

No `apache-airflow` pin — provided by the base image.

### `docker/Dockerfile.airflow`

- Extends `apache/airflow:2.9.0-python3.11`
- `USER root`: `apt-get install -y git libgomp1` (DVC needs git; LightGBM needs libgomp1)
- `USER airflow`: `pip install -r /requirements-airflow.txt`
- `RUN mkdir -p /opt/airflow/project`
- `ENV PYTHONPATH=/opt/airflow/project`
- Does **not** `COPY` any source files — source comes entirely from bind mounts

### `docker/docker-compose.yml`

**Services:** `postgres`, `airflow-init`, `airflow-webserver`, `airflow-scheduler`

**Named volumes:**

| Volume | Mount path | Purpose |
|---|---|---|
| `postgres-db` | postgres container | Airflow metadata DB |
| `denttime-interim` | `/opt/airflow/data/interim` | Intermediate train/test splits — pipeline scratch, not DVC-tracked |
| `denttime-dvc-store` | `/opt/airflow/dvc-store` | DVC remote — persists pushed artifacts across `docker compose down/up` |

**Bind mounts:**

| Host path | Container path | Mode | Rationale |
|---|---|---|---|
| `../` | `/opt/airflow/project` | `rw` | Full project root: `src/`, `.git/`, `.dvc/`, `features/`, `artifacts/` in one mount |
| `../Data Collection` | `/opt/airflow/data/raw` | `ro` | Raw CSV input — never overwritten |
| `../airflow/dags` | `/opt/airflow/dags` | `rw` | DAG hot-reload without image rebuild |

One root mount covers `src/`, `features/`, `src/features/artifacts/`, `.git/`, and `.dvc/` — no duplicate or overlapping mounts needed.

**`airflow-init` command (idempotent — safe on every `docker compose up`):**

```bash
airflow db migrate
airflow users create \
  --username admin --password admin --role Admin \
  --email admin@example.com --firstname Admin --lastname Admin
cd /opt/airflow/project && \
  dvc remote add -d localremote /opt/airflow/dvc-store --local --force
```

**Executor:** `LocalExecutor` (sufficient for single-machine; no Redis dependency)

**Credentials:** `admin / admin` — local development only

---

## Section 2: Airflow DAG

**File:** `airflow/dags/feature_engineering_dag.py`

### Path Constants (module-level)

```python
PROJECT_ROOT = Path("/opt/airflow/project")
ARTIFACTS    = PROJECT_ROOT / "src/features/artifacts"
FEATURES     = PROJECT_ROOT / "features"
INTERIM      = Path("/opt/airflow/data/interim")
RAW_CSV      = Path("/opt/airflow/data/raw/data.csv")
```

Mirrors the standalone `feature_engineering.py` directory conventions, remapped to container paths.

### DAG Configuration

```python
dag = DAG(
    dag_id="denttime_feature_engineering",
    schedule=None,           # manual trigger only — NOT schedule_interval (deprecated)
    catchup=False,
    tags=["feature-engineering"],
)
```

### Task Graph

```
task_load_and_split
  writes → INTERIM/train_split.parquet
           INTERIM/test_split.parquet
        │
   ┌────┼──────────────────────────────┐
   ▼    ▼                              ▼
task_build_doctor_  task_build_clinic_  task_build_treatment_
profile             profile             encoding
→ ARTIFACTS/        → ARTIFACTS/        → ARTIFACTS/
  doctor_profile      clinic_profile      treatment_encoding
  .json               .json               .json
   └──────────────────┴───────────────────┘
                       │
           ┌───────────┴───────────┐
           ▼                       ▼
task_transform_train    task_transform_test
→ FEATURES/             → FEATURES/
  features_train          features_test
  .parquet                .parquet
           └───────────┬───────────┘
                       ▼
           task_compute_feature_stats
           → FEATURES/feature_stats.json
                  ← pipeline ends here →
```

### Task Implementations

Each task function:
1. Calls `sys.path.insert(0, "/opt/airflow/project")` as first line
2. Imports from `src.features.*` inside the function body
3. Creates output directories with `mkdir(parents=True, exist_ok=True)`
4. Reads from path constants defined at module level (never hardcoded strings)

**`task_load_and_split`**
- Reads `RAW_CSV`
- Time-based split: train `<= 2025-02`, test `== 2025-04`
- Drops `LEAKAGE_COLUMNS`
- Writes `INTERIM/train_split.parquet` and `INTERIM/test_split.parquet`

**`task_build_doctor_profile`**
- Reads `INTERIM/train_split.parquet`
- Calls `build_profiles.build_doctor_profile(train_df)`
- Writes `ARTIFACTS/doctor_profile.json`

**`task_build_clinic_profile`**
- Reads `INTERIM/train_split.parquet`
- Calls `build_profiles.build_clinic_profile(train_df)`
- Writes `ARTIFACTS/clinic_profile.json`

**`task_build_treatment_encoding`**
- Reads `ARTIFACTS/treatment_dict.json` (static — already on host, covered by bind mount)
- Calls `build_treatment_encoding(treatment_dict)`
- Writes `ARTIFACTS/treatment_encoding.json`

**`task_transform_train` / `task_transform_test`**
- Reads all 4 artifacts from `ARTIFACTS/`
- Instantiates `FeatureTransformer`
- Reads respective split from `INTERIM/`
- Writes to `FEATURES/features_train.parquet` or `FEATURES/features_test.parquet`

**`task_compute_feature_stats`**
- Reads `FEATURES/features_train.parquet`
- Computes null rates, means, top-5 distributions (same logic as `feature_engineering.py`)
- Writes `FEATURES/feature_stats.json`

### No XCom
All inter-task communication is via files on shared volumes/mounts. No Airflow XCom — keeps large DataFrames out of the metadata DB.

---

## Section 3: Agent Loop

### New Agent: `.claude/agents/denttime-airflow-critic.md`

Reviews Airflow/Docker implementations. Same PASS/FAIL verdict format as `denttime-critic.md`.

**Section 1 (Docker infra) criteria:**
- `requirements-airflow.txt`: 5 packages, no `apache-airflow` pin
- Dockerfile: correct base image, `git`+`libgomp1` installed, no `COPY src/`, `PYTHONPATH` set, project dir created
- docker-compose: 3 named volumes, 3 bind mounts with correct modes (`ro`/`rw`), `LocalExecutor`, `airflow-init` runs `dvc remote add --local --force` (writes to `.dvc/config.local`, not `.dvc/config`)
- `../Data Collection` mounted `ro`
- Project root `../` mounted `rw` (not individual subdirectories)

**Section 2 (DAG) criteria:**
- `schedule=None` — not `schedule_interval` (deprecated)
- 7 `PythonOperator` tasks with exact names matching spec
- Path constants at module level, never hardcoded inside task functions
- `sys.path.insert(0, "/opt/airflow/project")` inside every task function
- Dependency wiring matches task graph exactly
- No XCom usage
- `task_build_treatment_encoding` depends only on `task_load_and_split` (runs in parallel with profile tasks)
- No `task_dvc_commit` or any git/dvc operations inside the DAG

### Coordinator Prompt

```
Implement the DentTime Airflow feature pipeline in two sections.
Plan: docs/superpowers/specs/2026-04-21-airflow-feature-pipeline-design.md

For each section:
1. Dispatch an implementer subagent (isolation: worktree) with the section spec
2. Dispatch denttime-airflow-critic with the git diff and section acceptance criteria
3. PASS → merge worktree to main, proceed to next section
4. FAIL → discard worktree, retry up to 3 attempts feeding critic issues back
5. Still FAIL after 3 → escalate to user with full issue list

Section 1: requirements-airflow.txt + docker/Dockerfile.airflow + docker/docker-compose.yml
Section 2: airflow/dags/feature_engineering_dag.py
```

### Escalation Format

```
ESCALATION — Section <N> failed after 3 attempts.

Critic issues from final attempt:
- <issue 1>
- <issue 2>

Options:
A) Give me a specific fix direction and I will retry
B) Lower the acceptance bar for this section and move on
C) Skip this section for now
```

---

## Post-Pipeline DVC Workflow (Host)

After a successful DAG run, files are already on the host at their correct paths (via bind mount). One `make` target versions everything:

```makefile
dvc-commit:
	dvc add features/features_train.parquet \
	        features/features_test.parquet \
	        features/feature_stats.json \
	        src/features/artifacts/doctor_profile.json \
	        src/features/artifacts/clinic_profile.json \
	        src/features/artifacts/treatment_encoding.json
	dvc push
	git add features/*.dvc src/features/artifacts/*.dvc .gitignore
	@echo "Run: git commit -m 'feat: update features $(shell date +%Y-%m-%d)'"
```

The `git commit` is intentionally left to the user — DVC pointer files should be committed with full context of what changed and why.

---

## Running the Pipeline

```bash
# 1. Build and start (first time ~3 min)
cd docker/
docker compose up --build -d

# 2. Open Airflow UI
open http://localhost:8080   # admin / admin

# 3. Trigger: DAGs → denttime_feature_engineering → ▶ Trigger DAG

# 4. Selective rerun example — rebuild doctor profile + downstream:
#    Grid view → click task_build_doctor_profile → Clear → Also clear downstream ✓

# 5. After pipeline completes — version the outputs
cd ..  # back to project root
make dvc-commit
git commit -m "feat: update features $(date +%Y-%m-%d)"

# 6. Shut down
cd docker/ && docker compose down
```

---

## What Stays Unchanged

- `feature_engineering.py` — still works standalone with `python feature_engineering.py --input ... --output ...`
- All `src/features/` Python modules — no modifications
- Existing DVC tracking setup — `.dvc` pointer files are updated by `make dvc-commit`, not by Airflow
