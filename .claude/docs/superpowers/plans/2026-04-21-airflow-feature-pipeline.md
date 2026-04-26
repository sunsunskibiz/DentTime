# Airflow Feature Engineering Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wrap `feature_engineering.py` into a 7-task Airflow DAG running inside Docker Compose, with DVC versioning remaining a host-side workflow.

**Architecture:** Project root is bind-mounted into the container so the DAG imports directly from `src/features/` and writes outputs to `features/` and `src/features/artifacts/` — the same paths DVC already tracks on the host. Two named volumes handle ephemeral data (interim splits, DVC remote). The critic agent validates each section before merging.

**Tech Stack:** Apache Airflow 2.9.0, Python 3.11, Docker Compose, DVC 3+, pandas, pyarrow, rapidfuzz

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `.claude/agents/denttime-airflow-critic.md` | Create | Reviews Docker infra + DAG against this spec |
| `requirements-airflow.txt` | Create | Python deps for Airflow image (no airflow pin) |
| `docker/Dockerfile.airflow` | Create | Airflow image — env only, no source COPY |
| `docker/docker-compose.yml` | Create | 4 services, 3 named volumes, 3 bind mounts |
| `airflow/dags/feature_engineering_dag.py` | Create | 7-task DAG with path constants + task functions |
| `tests/dags/test_feature_engineering_dag.py` | Create | Structural tests (syntax, task names, no XCom) |
| `Makefile` | Create | `dvc-commit` target for post-pipeline DVC tracking |

---

## Task 1: Create `denttime-airflow-critic` Agent

**Files:**
- Create: `.claude/agents/denttime-airflow-critic.md`

- [ ] **Step 1: Write the critic agent file**

```markdown
---
name: denttime-airflow-critic
description: Reviews implemented Airflow/Docker code against the DentTime Airflow pipeline spec
---

You are a strict code reviewer for the DentTime Airflow feature engineering pipeline.

Given: a git diff and a section's acceptance criteria from
`docs/superpowers/specs/2026-04-21-airflow-feature-pipeline-design.md`.

## Section 1 Criteria: Docker Infrastructure

### `requirements-airflow.txt`
- Must contain exactly these 5 packages: pandas>=2.0, pyarrow>=14.0, rapidfuzz>=3.0, dvc>=3.0, mlflow>=2.12
- Must NOT contain `apache-airflow` (provided by base image)

### `docker/Dockerfile.airflow`
- Must extend `apache/airflow:2.9.0-python3.11`
- Must install `git` and `libgomp1` via `apt-get` as USER root
- Must NOT `COPY src/` or any project source files
- Must `RUN mkdir -p /opt/airflow/project`
- Must set `ENV PYTHONPATH=/opt/airflow/project`

### `docker/docker-compose.yml`
- Must define exactly 3 named volumes: `postgres-db`, `denttime-interim`, `denttime-dvc-store`
- Must define exactly 3 bind mounts:
  - `../` → `/opt/airflow/project` (rw)
  - `../Data Collection` → `/opt/airflow/data/raw` (ro)
  - `../airflow/dags` → `/opt/airflow/dags` (rw)
- `airflow-init` command must include `dvc remote add -d localremote /opt/airflow/dvc-store --local --force`
- Must use `LocalExecutor`
- Raw data bind mount must have `:ro` flag
- Must NOT use individual subdirectory mounts like `../src` or `../features`

## Section 2 Criteria: Airflow DAG

### `airflow/dags/feature_engineering_dag.py`
- Must use `schedule=None` — NOT `schedule_interval` (deprecated since Airflow 2.4)
- Must define exactly 7 `PythonOperator` tasks with these exact task_ids:
  `task_load_and_split`, `task_build_doctor_profile`, `task_build_clinic_profile`,
  `task_build_treatment_encoding`, `task_transform_train`, `task_transform_test`,
  `task_compute_feature_stats`
- Path constants `PROJECT_ROOT`, `ARTIFACTS`, `FEATURES`, `INTERIM`, `RAW_CSV`
  must be defined at module level (not inside task functions)
- Every task function must call `sys.path.insert(0, str(PROJECT_ROOT))` as its first line
- Must NOT use XCom (`xcom_push` or `xcom_pull`)
- Must NOT contain `task_dvc_commit` or any `dvc`/`git` subprocess calls
- Dependency wiring must match exactly:
  - `load_and_split >> [build_doctor_profile, build_clinic_profile, build_treatment_encoding]`
  - `[build_doctor_profile, build_clinic_profile, build_treatment_encoding] >> transform_train`
  - `[build_doctor_profile, build_clinic_profile, build_treatment_encoding] >> transform_test`
  - `[transform_train, transform_test] >> compute_feature_stats`

Respond ONLY in this exact format:

VERDICT: PASS

or

VERDICT: FAIL
ISSUES:
- <specific issue 1>
- <specific issue 2>
```

- [ ] **Step 2: Commit**

```bash
git add .claude/agents/denttime-airflow-critic.md
git commit -m "feat: add denttime-airflow-critic agent"
```

---

## Task 2: `requirements-airflow.txt`

**Files:**
- Create: `requirements-airflow.txt`

- [ ] **Step 1: Create the file**

```text
pandas>=2.0
pyarrow>=14.0
rapidfuzz>=3.0
dvc>=3.0
mlflow>=2.12
```

- [ ] **Step 2: Verify no apache-airflow line present**

```bash
grep "apache-airflow" requirements-airflow.txt
```

Expected: no output (grep finds nothing).

- [ ] **Step 3: Commit**

```bash
git add requirements-airflow.txt
git commit -m "feat: add requirements-airflow.txt"
```

---

## Task 3: `docker/Dockerfile.airflow`

**Files:**
- Create: `docker/Dockerfile.airflow`

- [ ] **Step 1: Create the Dockerfile**

```dockerfile
FROM apache/airflow:2.9.0-python3.11

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends git libgomp1 \
    && rm -rf /var/lib/apt/lists/*

USER airflow
COPY requirements-airflow.txt /requirements-airflow.txt
RUN pip install --no-cache-dir -r /requirements-airflow.txt

RUN mkdir -p /opt/airflow/project
ENV PYTHONPATH=/opt/airflow/project
```

- [ ] **Step 2: Validate the image builds (from project root)**

```bash
docker build --no-cache -f docker/Dockerfile.airflow -t denttime-airflow-test .
```

Expected: `Successfully tagged denttime-airflow-test:latest` with no errors.

- [ ] **Step 3: Verify PYTHONPATH is set inside the image**

```bash
docker run --rm denttime-airflow-test python -c "import os; print(os.environ['PYTHONPATH'])"
```

Expected output: `/opt/airflow/project`

- [ ] **Step 4: Clean up test image**

```bash
docker rmi denttime-airflow-test
```

- [ ] **Step 5: Commit**

```bash
git add docker/Dockerfile.airflow
git commit -m "feat: add Dockerfile.airflow"
```

---

## Task 4: `docker/docker-compose.yml`

**Files:**
- Create: `docker/docker-compose.yml`

- [ ] **Step 1: Create the docker-compose file**

```yaml
version: "3.8"

x-airflow-common: &airflow-common
  build:
    context: ..
    dockerfile: docker/Dockerfile.airflow
  environment:
    AIRFLOW__CORE__EXECUTOR: LocalExecutor
    AIRFLOW__DATABASE__SQL_ALCHEMY_CONN: postgresql+psycopg2://airflow:airflow@postgres/airflow
    AIRFLOW__CORE__FERNET_KEY: ''
    AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION: 'true'
    AIRFLOW__CORE__LOAD_EXAMPLES: 'false'
  volumes:
    - ../:/opt/airflow/project
    - ../airflow/dags:/opt/airflow/dags
    - "../Data Collection:/opt/airflow/data/raw:ro"
    - denttime-interim:/opt/airflow/data/interim
    - denttime-dvc-store:/opt/airflow/dvc-store
  depends_on:
    postgres:
      condition: service_healthy

services:
  postgres:
    image: postgres:15
    environment:
      POSTGRES_USER: airflow
      POSTGRES_PASSWORD: airflow
      POSTGRES_DB: airflow
    volumes:
      - postgres-db:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD", "pg_isready", "-U", "airflow"]
      interval: 5s
      retries: 5

  airflow-init:
    <<: *airflow-common
    command: >
      bash -c "
        airflow db migrate &&
        airflow users create
          --username admin
          --password admin
          --role Admin
          --email admin@example.com
          --firstname Admin
          --lastname Admin &&
        cd /opt/airflow/project &&
        dvc remote add -d localremote /opt/airflow/dvc-store --local --force
      "
    restart: on-failure

  airflow-webserver:
    <<: *airflow-common
    command: airflow webserver
    ports:
      - "8080:8080"
    healthcheck:
      test: ["CMD", "curl", "--fail", "http://localhost:8080/health"]
      interval: 30s
      retries: 3
    depends_on:
      airflow-init:
        condition: service_completed_successfully

  airflow-scheduler:
    <<: *airflow-common
    command: airflow scheduler
    depends_on:
      airflow-init:
        condition: service_completed_successfully

volumes:
  postgres-db:
  denttime-interim:
  denttime-dvc-store:
```

- [ ] **Step 2: Validate compose file syntax (from docker/ directory)**

```bash
docker compose -f docker/docker-compose.yml config > /dev/null
```

Expected: no errors printed to stderr.

- [ ] **Step 3: Verify 3 named volumes are declared**

```bash
docker compose -f docker/docker-compose.yml config | grep -A 10 "^volumes:"
```

Expected output includes: `postgres-db`, `denttime-interim`, `denttime-dvc-store`.

- [ ] **Step 4: Commit — Section 1 complete**

```bash
git add docker/docker-compose.yml
git commit -m "feat: add docker-compose.yml — Section 1 complete"
```

---

## Task 5: DAG Structure Tests (write before implementation)

**Files:**
- Create: `tests/dags/test_feature_engineering_dag.py`
- Create: `tests/dags/__init__.py`

- [ ] **Step 1: Create `tests/dags/__init__.py`**

```bash
touch tests/dags/__init__.py
```

- [ ] **Step 2: Write the failing DAG structure tests**

```python
# tests/dags/test_feature_engineering_dag.py
import ast
from pathlib import Path

DAG_PATH = Path("airflow/dags/feature_engineering_dag.py")

EXPECTED_TASKS = [
    "task_load_and_split",
    "task_build_doctor_profile",
    "task_build_clinic_profile",
    "task_build_treatment_encoding",
    "task_transform_train",
    "task_transform_test",
    "task_compute_feature_stats",
]

EXPECTED_CONSTANTS = ["PROJECT_ROOT", "ARTIFACTS", "FEATURES", "INTERIM", "RAW_CSV"]


def test_dag_file_exists():
    assert DAG_PATH.exists(), f"DAG file not found at {DAG_PATH}"


def test_dag_file_valid_syntax():
    source = DAG_PATH.read_text()
    ast.parse(source)


def test_uses_schedule_not_schedule_interval():
    source = DAG_PATH.read_text()
    assert "schedule_interval" not in source, \
        "schedule_interval is deprecated in Airflow 2.4+, use schedule="
    assert "schedule=None" in source


def test_has_all_seven_tasks():
    source = DAG_PATH.read_text()
    for task_id in EXPECTED_TASKS:
        assert task_id in source, f"Missing task_id: {task_id}"


def test_no_xcom_usage():
    source = DAG_PATH.read_text()
    assert "xcom_push" not in source, "XCom not allowed — use file-based communication"
    assert "xcom_pull" not in source, "XCom not allowed — use file-based communication"


def test_no_dvc_or_git_in_dag():
    source = DAG_PATH.read_text()
    assert "dvc" not in source.lower(), "No DVC operations inside DAG tasks"
    assert "subprocess" not in source, "No subprocess calls — DVC is a host-side concern"


def test_path_constants_at_module_level():
    source = DAG_PATH.read_text()
    for const in EXPECTED_CONSTANTS:
        assert const in source, f"Missing path constant: {const}"


def test_sys_path_insert_in_each_task():
    source = DAG_PATH.read_text()
    tree = ast.parse(source)
    task_func_names = [f"_task_{t.replace('task_', '')}" for t in EXPECTED_TASKS]
    funcs_with_insert = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("_task_"):
            func_src = ast.unparse(node)
            if "sys.path.insert" in func_src:
                funcs_with_insert.add(node.name)
    for fn in task_func_names:
        assert fn in funcs_with_insert, \
            f"{fn} is missing sys.path.insert(0, str(PROJECT_ROOT))"
```

- [ ] **Step 3: Run tests — verify they fail with "DAG file not found"**

```bash
pytest tests/dags/test_feature_engineering_dag.py -v
```

Expected: `FAILED tests/dags/test_feature_engineering_dag.py::test_dag_file_exists`

- [ ] **Step 4: Commit the tests**

```bash
git add tests/dags/
git commit -m "test: add DAG structure tests (failing)"
```

---

## Task 6: DAG Skeleton — Path Constants + Empty Stubs

**Files:**
- Create: `airflow/dags/feature_engineering_dag.py`

- [ ] **Step 1: Create the DAG skeleton with path constants and empty task stubs**

```python
"""
DentTime feature engineering DAG.

7 tasks that wrap feature_engineering.py as independently-rerunnable steps.
Outputs are written to the project root bind mount so DVC tracking on the
host works without any file copying.
"""
import sys
from pathlib import Path

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.utils.dates import days_ago

# ---------------------------------------------------------------------------
# Path constants — defined at module level, referenced by all task functions
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path("/opt/airflow/project")
ARTIFACTS    = PROJECT_ROOT / "src/features/artifacts"
FEATURES     = PROJECT_ROOT / "features"
INTERIM      = Path("/opt/airflow/data/interim")
RAW_CSV      = Path("/opt/airflow/data/raw/data.csv")


# ---------------------------------------------------------------------------
# Task functions (implementations added in Tasks 7–9)
# ---------------------------------------------------------------------------

def _task_load_and_split():
    sys.path.insert(0, str(PROJECT_ROOT))


def _task_build_doctor_profile():
    sys.path.insert(0, str(PROJECT_ROOT))


def _task_build_clinic_profile():
    sys.path.insert(0, str(PROJECT_ROOT))


def _task_build_treatment_encoding():
    sys.path.insert(0, str(PROJECT_ROOT))


def _task_transform_train():
    sys.path.insert(0, str(PROJECT_ROOT))


def _task_transform_test():
    sys.path.insert(0, str(PROJECT_ROOT))


def _task_compute_feature_stats():
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# DAG definition
# ---------------------------------------------------------------------------
with DAG(
    dag_id="denttime_feature_engineering",
    schedule=None,
    start_date=days_ago(1),
    catchup=False,
    tags=["feature-engineering"],
) as dag:

    load_and_split = PythonOperator(
        task_id="task_load_and_split",
        python_callable=_task_load_and_split,
    )
    build_doctor_profile = PythonOperator(
        task_id="task_build_doctor_profile",
        python_callable=_task_build_doctor_profile,
    )
    build_clinic_profile = PythonOperator(
        task_id="task_build_clinic_profile",
        python_callable=_task_build_clinic_profile,
    )
    build_treatment_encoding = PythonOperator(
        task_id="task_build_treatment_encoding",
        python_callable=_task_build_treatment_encoding,
    )
    transform_train = PythonOperator(
        task_id="task_transform_train",
        python_callable=_task_transform_train,
    )
    transform_test = PythonOperator(
        task_id="task_transform_test",
        python_callable=_task_transform_test,
    )
    compute_feature_stats = PythonOperator(
        task_id="task_compute_feature_stats",
        python_callable=_task_compute_feature_stats,
    )

    # Dependency wiring — added in Task 9
```

- [ ] **Step 2: Run the structural tests — most should now pass**

```bash
pytest tests/dags/test_feature_engineering_dag.py -v
```

Expected: all tests pass except `test_sys_path_insert_in_each_task` (stubs only have insert, no body yet — this may pass or need dependency wiring check).

- [ ] **Step 3: Commit**

```bash
git add airflow/dags/feature_engineering_dag.py
git commit -m "feat: DAG skeleton with path constants and empty task stubs"
```

---

## Task 7: Implement Load/Split + Profile Tasks

**Files:**
- Modify: `airflow/dags/feature_engineering_dag.py`

- [ ] **Step 1: Implement `_task_load_and_split`**

Replace the stub:

```python
def _task_load_and_split():
    sys.path.insert(0, str(PROJECT_ROOT))
    import pandas as pd
    from src.features.feature_transformer import LEAKAGE_COLUMNS

    INTERIM.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(RAW_CSV)
    train_df = df[df["appt_year_month"] <= "2025-02"].copy()
    test_df  = df[df["appt_year_month"] == "2025-04"].copy()

    if len(train_df) == 0:
        raise ValueError("Train split is empty — check appt_year_month column")
    if len(test_df) == 0:
        raise ValueError("Test split is empty — check appt_year_month column")

    leakage_present = [c for c in LEAKAGE_COLUMNS if c in train_df.columns]
    if leakage_present:
        train_df = train_df.drop(columns=leakage_present)
        test_df  = test_df.drop(columns=leakage_present)

    train_df.to_parquet(INTERIM / "train_split.parquet", index=False)
    test_df.to_parquet(INTERIM  / "test_split.parquet",  index=False)
```

- [ ] **Step 2: Implement `_task_build_doctor_profile`**

Replace the stub:

```python
def _task_build_doctor_profile():
    sys.path.insert(0, str(PROJECT_ROOT))
    import json
    import pandas as pd
    from src.features.build_profiles import build_doctor_profile

    train_df = pd.read_parquet(INTERIM / "train_split.parquet")
    profile  = build_doctor_profile(train_df)

    with open(ARTIFACTS / "doctor_profile.json", "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2, ensure_ascii=False)
```

- [ ] **Step 3: Implement `_task_build_clinic_profile`**

Replace the stub:

```python
def _task_build_clinic_profile():
    sys.path.insert(0, str(PROJECT_ROOT))
    import json
    import pandas as pd
    from src.features.build_profiles import build_clinic_profile

    train_df = pd.read_parquet(INTERIM / "train_split.parquet")
    profile  = build_clinic_profile(train_df)

    with open(ARTIFACTS / "clinic_profile.json", "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2, ensure_ascii=False)
```

- [ ] **Step 4: Implement `_task_build_treatment_encoding`**

Replace the stub:

```python
def _task_build_treatment_encoding():
    sys.path.insert(0, str(PROJECT_ROOT))
    import json
    from src.features.feature_transformer import build_treatment_encoding, load_treatment_dict

    treatment_dict = load_treatment_dict(str(ARTIFACTS / "treatment_dict.json"))
    encoding       = build_treatment_encoding(treatment_dict)

    with open(ARTIFACTS / "treatment_encoding.json", "w", encoding="utf-8") as f:
        json.dump(encoding, f, indent=2, sort_keys=True)
```

- [ ] **Step 5: Run syntax check**

```bash
python -c "import ast; ast.parse(open('airflow/dags/feature_engineering_dag.py').read()); print('OK')"
```

Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add airflow/dags/feature_engineering_dag.py
git commit -m "feat: implement load_and_split and profile tasks"
```

---

## Task 8: Implement Transform + Stats Tasks

**Files:**
- Modify: `airflow/dags/feature_engineering_dag.py`

- [ ] **Step 1: Implement `_task_transform_train`**

Replace the stub:

```python
def _task_transform_train():
    sys.path.insert(0, str(PROJECT_ROOT))
    import pandas as pd
    from src.features.feature_transformer import FeatureTransformer

    FEATURES.mkdir(parents=True, exist_ok=True)

    transformer = FeatureTransformer(
        doctor_profile_path=str(ARTIFACTS / "doctor_profile.json"),
        clinic_profile_path=str(ARTIFACTS / "clinic_profile.json"),
        treatment_dict_path=str(ARTIFACTS / "treatment_dict.json"),
        treatment_encoding_path=str(ARTIFACTS / "treatment_encoding.json"),
    )
    train_df = pd.read_parquet(INTERIM / "train_split.parquet")
    transformer.transform(train_df).to_parquet(
        FEATURES / "features_train.parquet", index=False
    )
```

- [ ] **Step 2: Implement `_task_transform_test`**

Replace the stub:

```python
def _task_transform_test():
    sys.path.insert(0, str(PROJECT_ROOT))
    import pandas as pd
    from src.features.feature_transformer import FeatureTransformer

    transformer = FeatureTransformer(
        doctor_profile_path=str(ARTIFACTS / "doctor_profile.json"),
        clinic_profile_path=str(ARTIFACTS / "clinic_profile.json"),
        treatment_dict_path=str(ARTIFACTS / "treatment_dict.json"),
        treatment_encoding_path=str(ARTIFACTS / "treatment_encoding.json"),
    )
    test_df = pd.read_parquet(INTERIM / "test_split.parquet")
    transformer.transform(test_df).to_parquet(
        FEATURES / "features_test.parquet", index=False
    )
```

- [ ] **Step 3: Implement `_task_compute_feature_stats`**

Replace the stub:

```python
def _task_compute_feature_stats():
    sys.path.insert(0, str(PROJECT_ROOT))
    import json
    import pandas as pd

    train_features = pd.read_parquet(FEATURES / "features_train.parquet")

    with open(ARTIFACTS / "treatment_encoding.json", encoding="utf-8") as f:
        encoding = json.load(f)
    unknown_int = encoding["UNKNOWN"]

    stats = {}
    for col in train_features.columns:
        col_stats: dict = {"null_rate": float(train_features[col].isna().mean())}
        if col == "treatment_class":
            col_stats["unknown_rate"] = float(
                (train_features[col] == unknown_int).sum() / len(train_features)
            )
            col_stats["mean"] = float(train_features[col].mean())
        elif train_features[col].dtype == object:
            top5 = train_features[col].value_counts().head(5).to_dict()
            col_stats["top5"] = {str(k): int(v) for k, v in top5.items()}
        else:
            col_stats["mean"] = float(train_features[col].mean())
            if col == "appt_hour_bucket":
                col_stats["pct_sentinel"] = float(
                    (train_features[col] == -1).sum() / len(train_features)
                )
        stats[col] = col_stats

    with open(FEATURES / "feature_stats.json", "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2)
```

- [ ] **Step 4: Run syntax check**

```bash
python -c "import ast; ast.parse(open('airflow/dags/feature_engineering_dag.py').read()); print('OK')"
```

Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add airflow/dags/feature_engineering_dag.py
git commit -m "feat: implement transform and feature stats tasks"
```

---

## Task 9: Wire Task Dependencies + Final Tests

**Files:**
- Modify: `airflow/dags/feature_engineering_dag.py`

- [ ] **Step 1: Replace the `# Dependency wiring` comment with the actual wiring**

```python
    # Dependency wiring
    load_and_split >> [build_doctor_profile, build_clinic_profile, build_treatment_encoding]
    [build_doctor_profile, build_clinic_profile, build_treatment_encoding] >> transform_train
    [build_doctor_profile, build_clinic_profile, build_treatment_encoding] >> transform_test
    [transform_train, transform_test] >> compute_feature_stats
```

- [ ] **Step 2: Add dependency wiring test to the test file**

Add to `tests/dags/test_feature_engineering_dag.py`:

```python
def test_dependency_wiring():
    source = DAG_PATH.read_text()
    # load_and_split fans out to the three parallel tasks
    assert "load_and_split >> [build_doctor_profile" in source or \
           "load_and_split >> [build_clinic_profile" in source, \
           "load_and_split must fan out to profile/encoding tasks"
    # both transforms must gate feature stats
    assert "[transform_train, transform_test] >> compute_feature_stats" in source, \
           "Both transforms must complete before feature stats"
```

- [ ] **Step 3: Run the full test suite**

```bash
pytest tests/dags/test_feature_engineering_dag.py -v
```

Expected: all 9 tests PASS.

- [ ] **Step 4: Run syntax check one final time**

```bash
python -c "import ast; ast.parse(open('airflow/dags/feature_engineering_dag.py').read()); print('OK')"
```

Expected: `OK`

- [ ] **Step 5: Commit — Section 2 complete**

```bash
git add airflow/dags/feature_engineering_dag.py tests/dags/test_feature_engineering_dag.py
git commit -m "feat: wire DAG dependencies — Section 2 complete"
```

---

## Task 10: Makefile `dvc-commit` Target

**Files:**
- Create: `Makefile`

- [ ] **Step 1: Create the Makefile**

```makefile
.PHONY: dvc-commit

dvc-commit:
	dvc add features/features_train.parquet \
	        features/features_test.parquet \
	        features/feature_stats.json \
	        src/features/artifacts/doctor_profile.json \
	        src/features/artifacts/clinic_profile.json \
	        src/features/artifacts/treatment_encoding.json
	dvc push
	git add features/*.dvc \
	        src/features/artifacts/doctor_profile.json.dvc \
	        src/features/artifacts/clinic_profile.json.dvc \
	        src/features/artifacts/treatment_encoding.json.dvc \
	        .gitignore
	@echo ""
	@echo "DVC push complete. Now run:"
	@echo "  git commit -m 'feat: update features $(shell date +%Y-%m-%d)'"
```

Note: Makefile indentation MUST use tabs, not spaces.

- [ ] **Step 2: Verify Makefile syntax**

```bash
make --dry-run dvc-commit
```

Expected: prints the `dvc add` and `dvc push` commands without running them. No `Makefile:N: *** missing separator` error.

- [ ] **Step 3: Commit**

```bash
git add Makefile
git commit -m "feat: add Makefile with dvc-commit target"
```

---

## Coordinator Prompt

Paste this into a Claude Code session to run the agent loop:

```
Implement the DentTime Airflow feature engineering pipeline in two sections.
Spec: docs/superpowers/specs/2026-04-21-airflow-feature-pipeline-design.md
Plan: docs/superpowers/plans/2026-04-21-airflow-feature-pipeline.md

For each section:
1. Dispatch an implementer subagent (isolation: worktree) with the section tasks from the plan
2. Dispatch the denttime-airflow-critic subagent with the git diff and the section's acceptance criteria
3. PASS → merge worktree to main, proceed to next section
4. FAIL → discard worktree, retry up to 3 attempts, feeding critic issue list back to the implementer
5. Still FAIL after 3 attempts → escalate to user

Section 1 (Tasks 1–4): denttime-airflow-critic agent + requirements-airflow.txt + docker/Dockerfile.airflow + docker/docker-compose.yml
Section 2 (Tasks 5–10): DAG tests + DAG implementation + Makefile
```

---

## Running the Full Pipeline (After Implementation)

```bash
# 1. Start everything (first run pulls ~1 GB of images — allow 5 min)
cd docker/
docker compose up --build -d

# 2. Wait for healthy, then open UI
open http://localhost:8080   # admin / admin

# 3. Trigger: DAGs → denttime_feature_engineering → ▶ Trigger DAG

# 4. Monitor in Grid view — all 7 tasks should turn green

# 5. After pipeline completes — version outputs with DVC
cd ..
make dvc-commit
git commit -m "feat: update features $(date +%Y-%m-%d)"

# 6. Selective rerun example (rebuild doctor profile + downstream):
#    Grid view → click task_build_doctor_profile → Clear → Also clear downstream ✓

# 7. Shut down
cd docker/ && docker compose down
```
