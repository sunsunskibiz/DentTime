# DVC Raw Data Pull Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `task_pull_raw_data` first task to `feature_engineering_dag` that pulls `data/published/` from DagsHub via DVC and copies the CSV to `data/raw/data.csv`, falling back gracefully to the existing local file when DagsHub is unavailable.

**Architecture:** DVC pull logic lives in `src/features/dvc_utils.py` (pure Python, no Airflow) so it can be unit-tested without Airflow. The DAG task is a thin wrapper that maps the return status to `AirflowSkipException`. `task_load_and_split` gets `TriggerRule.NONE_FAILED_MIN_ONE_SUCCESS` so it continues even when the pull task is skipped.

**Tech Stack:** Python 3.11, Apache Airflow 2.x, DVC 3.x, pytest, unittest.mock

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `.dvc/config` | Modify | Add `dagshub-raw` remote pointing to DagsHub |
| `data/published.dvc` | Create (copy) | DVC pointer to upstream published data directory |
| `.gitignore` | Modify | Ignore `data/published/` and `docker/.env` |
| `src/features/dvc_utils.py` | Create | `pull_raw_data()` — pure Python DVC pull + CSV copy + fallback |
| `tests/test_dvc_utils.py` | Create | Unit tests for all 5 state transitions of `pull_raw_data` |
| `airflow/dags/feature_engineering_dag.py` | Modify | Add `_task_pull_raw_data`, update `load_and_split` trigger rule, wire dependency |
| `tests/dags/test_feature_engineering_dag.py` | Modify | Update task list, replace stale test, add 2 structural tests |
| `docker/docker-compose.yml` | Modify | Add `env_file: - .env` to `x-airflow-common` |
| `docker/.env` | Create (not committed) | `DAGSHUB_USER` + `DAGSHUB_TOKEN` credentials |

---

## Task 1: DVC Remote Config and Pointer File

**Files:**
- Modify: `.dvc/config`
- Create: `data/published.dvc`
- Modify: `.gitignore`

- [ ] **Step 1: Add `dagshub-raw` remote to `.dvc/config`**

Open `.dvc/config` and replace its contents with:

```ini
[core]
    remote = localremote
['remote "localremote"']
    url = /tmp/dvc-store-denttime
['remote "dagshub-raw"']
    url = https://dagshub.com/natchyunicorn/denttime.dvc
    auth = basic
```

- [ ] **Step 2: Copy the pointer file from the upstream repo**

Run from the project root:
```bash
cp /Users/sunsun/Desktop/nutchy/data/published.dvc data/published.dvc
```

Verify it looks like this:
```bash
cat data/published.dvc
```
Expected output:
```yaml
outs:
- md5: 268b7c1ae98edf9d4d5b97be5678df70.dir
  size: 38686286
  nfiles: 2
  hash: md5
  path: published
```

- [ ] **Step 3: Update `.gitignore`**

Add two lines to the root `.gitignore`:
```
data/published/
docker/.env
```

- [ ] **Step 4: Commit**

```bash
git add .dvc/config data/published.dvc .gitignore
git commit -m "feat: add dagshub-raw DVC remote and published.dvc pointer"
```

---

## Task 2: Write Failing Unit Tests for `dvc_utils`

**Files:**
- Create: `tests/test_dvc_utils.py`

- [ ] **Step 1: Create the test file**

Create `tests/test_dvc_utils.py` with the following content:

```python
import pytest
from subprocess import CalledProcessError
from unittest.mock import patch, call

from src.features.dvc_utils import pull_raw_data

ARGS = {
    "dvc_file": "data/published.dvc",
    "local_csv": "/opt/airflow/data/raw/data.csv",
    "remote": "dagshub-raw",
    "project_root": "/opt/airflow/project",
}

PUBLISHED_CSV = "/opt/airflow/project/data/published/data.csv"


@patch("src.features.dvc_utils.shutil.copy")
@patch("src.features.dvc_utils.glob.glob", return_value=[PUBLISHED_CSV])
@patch("src.features.dvc_utils.subprocess.run")
@patch.dict("os.environ", {"DAGSHUB_USER": "", "DAGSHUB_TOKEN": ""})
def test_pull_success(mock_run, mock_glob, mock_copy):
    result = pull_raw_data(**ARGS)
    assert result == "pulled"


@patch("src.features.dvc_utils.shutil.copy")
@patch("src.features.dvc_utils.glob.glob", return_value=[PUBLISHED_CSV])
@patch("src.features.dvc_utils.subprocess.run")
@patch.dict("os.environ", {"DAGSHUB_USER": "", "DAGSHUB_TOKEN": ""})
def test_pull_success_csv_copied(mock_run, mock_glob, mock_copy):
    pull_raw_data(**ARGS)
    mock_copy.assert_called_once_with(PUBLISHED_CSV, "/opt/airflow/data/raw/data.csv")


@patch("src.features.dvc_utils.os.path.exists", return_value=True)
@patch("src.features.dvc_utils.subprocess.run", side_effect=CalledProcessError(1, "dvc"))
@patch.dict("os.environ", {"DAGSHUB_USER": "", "DAGSHUB_TOKEN": ""})
def test_pull_fails_local_exists(mock_run, mock_exists):
    result = pull_raw_data(**ARGS)
    assert result == "skipped"


@patch("src.features.dvc_utils.os.path.exists", return_value=False)
@patch("src.features.dvc_utils.subprocess.run", side_effect=CalledProcessError(1, "dvc"))
@patch.dict("os.environ", {"DAGSHUB_USER": "", "DAGSHUB_TOKEN": ""})
def test_pull_fails_no_local(mock_run, mock_exists):
    with pytest.raises(RuntimeError, match="Cannot proceed"):
        pull_raw_data(**ARGS)


@patch("src.features.dvc_utils.glob.glob", return_value=[])
@patch("src.features.dvc_utils.subprocess.run")
@patch.dict("os.environ", {"DAGSHUB_USER": "", "DAGSHUB_TOKEN": ""})
def test_pull_success_no_csv_in_published(mock_run, mock_glob):
    with pytest.raises(RuntimeError, match="no .csv found"):
        pull_raw_data(**ARGS)
```

- [ ] **Step 2: Run the tests and confirm they all fail with ImportError**

```bash
pytest tests/test_dvc_utils.py -v
```

Expected: 5 errors, all `ModuleNotFoundError: No module named 'src.features.dvc_utils'`

- [ ] **Step 3: Commit the failing tests**

```bash
git add tests/test_dvc_utils.py
git commit -m "test: add failing unit tests for dvc_utils.pull_raw_data"
```

---

## Task 3: Implement `src/features/dvc_utils.py`

**Files:**
- Create: `src/features/dvc_utils.py`

- [ ] **Step 1: Create the module**

Create `src/features/dvc_utils.py`:

```python
import glob
import logging
import os
import shutil
import subprocess


def pull_raw_data(dvc_file, local_csv, remote, project_root):
    """
    Returns "pulled" or "skipped". Raises RuntimeError if neither succeeds.
    Pure Python — no Airflow imports.
    """
    user = os.environ.get("DAGSHUB_USER")
    token = os.environ.get("DAGSHUB_TOKEN")

    if user and token:
        subprocess.run(
            ["dvc", "remote", "modify", remote, "--local", "user", user],
            cwd=project_root, check=True,
        )
        subprocess.run(
            ["dvc", "remote", "modify", remote, "--local", "password", token],
            cwd=project_root, check=True,
        )

    try:
        subprocess.run(
            ["dvc", "pull", dvc_file, "--remote", remote],
            cwd=project_root, check=True,
        )
        published_dir = os.path.join(project_root, "data", "published")
        csv_files = glob.glob(os.path.join(published_dir, "*.csv"))
        if not csv_files:
            raise RuntimeError(f"DVC pull succeeded but no .csv found in {published_dir}")
        shutil.copy(csv_files[0], local_csv)
        logging.info("DVC pull succeeded — copied %s → %s", csv_files[0], local_csv)
        return "pulled"

    except subprocess.CalledProcessError as e:
        logging.warning("DVC pull failed: %s", e)
        if os.path.exists(local_csv):
            logging.warning("Falling back to existing local file: %s", local_csv)
            return "skipped"
        raise RuntimeError(
            f"DVC pull failed AND no local file at {local_csv}. Cannot proceed."
        )
```

- [ ] **Step 2: Run the tests and confirm all 5 pass**

```bash
pytest tests/test_dvc_utils.py -v
```

Expected output:
```
PASSED tests/test_dvc_utils.py::test_pull_success
PASSED tests/test_dvc_utils.py::test_pull_success_csv_copied
PASSED tests/test_dvc_utils.py::test_pull_fails_local_exists
PASSED tests/test_dvc_utils.py::test_pull_fails_no_local
PASSED tests/test_dvc_utils.py::test_pull_success_no_csv_in_published
5 passed
```

- [ ] **Step 3: Commit**

```bash
git add src/features/dvc_utils.py
git commit -m "feat: add dvc_utils.pull_raw_data with DagsHub pull and local fallback"
```

---

## Task 4: Write Failing DAG Structural Tests

**Files:**
- Modify: `tests/dags/test_feature_engineering_dag.py`

- [ ] **Step 1: Update the test file**

Replace the entire contents of `tests/dags/test_feature_engineering_dag.py` with:

```python
import ast
from pathlib import Path

DAG_PATH = Path("airflow/dags/feature_engineering_dag.py")

EXPECTED_TASKS = [
    "task_pull_raw_data",
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


def test_has_all_eight_tasks():
    source = DAG_PATH.read_text()
    for task_id in EXPECTED_TASKS:
        assert task_id in source, f"Missing task_id: {task_id}"


def test_no_xcom_usage():
    source = DAG_PATH.read_text()
    assert "xcom_push" not in source, "XCom not allowed — use file-based communication"
    assert "xcom_pull" not in source, "XCom not allowed — use file-based communication"


def test_pull_task_calls_dvc_utils():
    source = DAG_PATH.read_text()
    assert "subprocess" not in source, \
        "No subprocess calls in DAG — delegate to src.features.dvc_utils"
    assert "dvc_utils" in source, \
        "_task_pull_raw_data must import from src.features.dvc_utils"


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


def test_pull_task_is_first():
    source = DAG_PATH.read_text()
    assert "pull_raw_data >> load_and_split" in source, \
        "task_pull_raw_data must be wired before task_load_and_split"


def test_load_and_split_trigger_rule():
    source = DAG_PATH.read_text()
    assert "NONE_FAILED_MIN_ONE_SUCCESS" in source, \
        "task_load_and_split must use TriggerRule.NONE_FAILED_MIN_ONE_SUCCESS"


def test_dependency_wiring():
    source = DAG_PATH.read_text()
    assert "load_and_split >> [build_doctor_profile" in source or \
           "load_and_split >> [build_clinic_profile" in source, \
           "load_and_split must fan out to profile/encoding tasks"
    assert "[transform_train, transform_test] >> compute_feature_stats" in source, \
           "Both transforms must complete before feature stats"
```

- [ ] **Step 2: Run the tests and confirm 3 fail**

```bash
pytest tests/dags/test_feature_engineering_dag.py -v
```

Expected: 4 tests FAIL — `test_has_all_eight_tasks`, `test_pull_task_calls_dvc_utils`, `test_pull_task_is_first`, `test_load_and_split_trigger_rule`. All 7 others PASS.

- [ ] **Step 3: Commit the failing tests**

```bash
git add tests/dags/test_feature_engineering_dag.py
git commit -m "test: update DAG structural tests for task_pull_raw_data"
```

---

## Task 5: Add `task_pull_raw_data` to the DAG

**Files:**
- Modify: `airflow/dags/feature_engineering_dag.py`

- [ ] **Step 1: Add `TriggerRule` import**

At the top of `airflow/dags/feature_engineering_dag.py`, add one import after the existing Airflow imports:

```python
from airflow.utils.trigger_rule import TriggerRule
```

The imports block should now read:
```python
import sys
from pathlib import Path

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.utils.dates import days_ago
from airflow.utils.trigger_rule import TriggerRule
```

- [ ] **Step 2: Add `_task_pull_raw_data` function**

Add the following function immediately before `_task_load_and_split` (after the path constants block, around line 21):

```python
def _task_pull_raw_data():
    sys.path.insert(0, str(PROJECT_ROOT))
    from airflow.exceptions import AirflowSkipException
    from src.features.dvc_utils import pull_raw_data

    status = pull_raw_data(
        dvc_file="data/published.dvc",
        local_csv=str(RAW_CSV),
        remote="dagshub-raw",
        project_root=str(PROJECT_ROOT),
    )
    if status == "skipped":
        raise AirflowSkipException("DVC pull failed; using existing local data/raw/data.csv")
```

- [ ] **Step 3: Add the `pull_raw_data` operator and update `load_and_split` trigger rule**

In the `with DAG(...) as dag:` block, add the new operator and update `load_and_split`:

Replace:
```python
    load_and_split = PythonOperator(
        task_id="task_load_and_split",
        python_callable=_task_load_and_split,
    )
```

With:
```python
    pull_raw_data = PythonOperator(
        task_id="task_pull_raw_data",
        python_callable=_task_pull_raw_data,
    )
    load_and_split = PythonOperator(
        task_id="task_load_and_split",
        python_callable=_task_load_and_split,
        trigger_rule=TriggerRule.NONE_FAILED_MIN_ONE_SUCCESS,
    )
```

- [ ] **Step 4: Update dependency wiring**

Replace:
```python
    # Dependency wiring
    load_and_split >> [build_doctor_profile, build_clinic_profile, build_treatment_encoding]
```

With:
```python
    # Dependency wiring
    pull_raw_data >> load_and_split
    load_and_split >> [build_doctor_profile, build_clinic_profile, build_treatment_encoding]
```

- [ ] **Step 5: Run all tests and confirm everything passes**

```bash
pytest tests/test_dvc_utils.py tests/dags/test_feature_engineering_dag.py -v
```

Expected: all 16 tests PASS (5 unit + 11 structural).

- [ ] **Step 6: Run the full test suite to check for regressions**

```bash
pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add airflow/dags/feature_engineering_dag.py
git commit -m "feat: add task_pull_raw_data to feature_engineering_dag with DagsHub fallback"
```

---

## Task 6: Docker Credential Wiring

**Files:**
- Modify: `docker/docker-compose.yml`
- Create: `docker/.env` (not committed)

- [ ] **Step 1: Add `env_file` to `docker/docker-compose.yml`**

In `docker/docker-compose.yml`, the `x-airflow-common` anchor starts at line 4. Add `env_file` after the `build` block:

Replace:
```yaml
x-airflow-common: &airflow-common
  build:
    context: ..
    dockerfile: docker/Dockerfile.airflow
  environment:
```

With:
```yaml
x-airflow-common: &airflow-common
  build:
    context: ..
    dockerfile: docker/Dockerfile.airflow
  env_file:
    - .env
  environment:
```

- [ ] **Step 2: Create `docker/.env`**

Create the file `docker/.env` (this file must NOT be committed):

```
DAGSHUB_USER=sunsunskibiz
DAGSHUB_TOKEN=<your-dagshub-token>
```

Confirm it is gitignored:
```bash
git check-ignore -v docker/.env
```

Expected: `.gitignore:... docker/.env` (shows it is ignored). If not, add `docker/.env` to the root `.gitignore` and re-run.

- [ ] **Step 3: Commit only the compose change — never the `.env` file**

```bash
git add docker/docker-compose.yml
git commit -m "feat: add env_file to Airflow compose for DagsHub credentials"
```

---

## Done — Verify End-to-End

- [ ] **Final check: run the full test suite**

```bash
pytest tests/ -v
```

Expected: all tests pass with no failures or errors.
