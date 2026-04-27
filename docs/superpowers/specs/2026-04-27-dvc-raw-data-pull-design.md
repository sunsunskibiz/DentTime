# DVC Raw Data Pull — Design Spec
**Date:** 2026-04-27
**Status:** Approved

---

## Goal

Automatically sync the upstream team's published data at the start of each `feature_engineering_dag` run using DVC, for reproducible versioning. The pipeline must remain runnable when DagsHub is unavailable (403, network error), falling back to the existing local `data/raw/data.csv`.

This is a **reproducible versioning** goal — not live data ingestion. `dvc pull` fetches the team's currently agreed-upon snapshot (the version pointed to by the committed `.dvc` pointer file), not necessarily the freshest upstream data.

---

## Upstream Data Structure (DagsHub)

The upstream team (`natchyunicorn/denttime`) publishes data via DVC at:
- **DVC remote:** `https://dagshub.com/natchyunicorn/denttime.dvc`
- **Tracked path:** `data/published/` — a directory with 2 files (~37 MB total)
- **Pointer file:** `data/published.dvc`

The pointer file is copied from the upstream repo (`nutchy/data/published.dvc`) into DentTime. **No `dvc add` or `dvc push` is needed** — the data is already on DagsHub.

---

## Architecture

One new task is prepended to the existing `feature_engineering_dag`. All downstream tasks are unchanged.

```
task_pull_raw_data          ← NEW (PythonOperator, skippable)
    │
    ▼
task_load_and_split         ← existing, trigger_rule updated
    │
    ├── task_build_treatment_encoding
    ├── task_build_doctor_profile
    └── task_build_clinic_profile
            │
            ├── task_transform_train
            └── task_transform_test
                    │
                    └── task_compute_feature_stats
```

Two DVC remotes exist side-by-side:

| Remote name | URL | Tracks |
|---|---|---|
| `localremote` | `/tmp/dvc-store-denttime` | features + artifacts (existing) |
| `dagshub-raw` | `https://dagshub.com/natchyunicorn/denttime.dvc` | `data/published/` (new) |

`origin` is intentionally left unused — reserved for a future artifacts remote.

---

## DVC Configuration Changes

### `.dvc/config`
Add `dagshub-raw` remote alongside the existing `localremote`:

```ini
[core]
    remote = localremote
['remote "localremote"']
    url = /tmp/dvc-store-denttime
['remote "dagshub-raw"']
    url = https://dagshub.com/natchyunicorn/denttime.dvc
    auth = basic
```

### `data/published.dvc` (copied from upstream, not created)
Copy the pointer file from the upstream repo into DentTime. One-time setup (run from project root, outside Docker):

```bash
# Copy the pointer file from the upstream repo
cp /path/to/nutchy/data/published.dvc data/published.dvc

# Add the data directory to .gitignore (pointer file is committed, not the data)
echo "data/published/" >> .gitignore

# Commit only the pointer file
git add data/published.dvc .gitignore
git commit -m "feat: track upstream published data with DVC (dagshub-raw remote)"
```

The `.dvc` pointer file is committed to git. The actual data directory (`data/published/`) is gitignored.

### `docker/.env` (new file, never committed)
```
DAGSHUB_USER=sunsunskibiz
DAGSHUB_TOKEN=<your-dagshub-token>
```

Credentials are never written to any committed file. `docker/.env` must be added to `.gitignore`.

The `x-airflow-common` block in `docker/docker-compose.yml` must gain an `env_file:` directive:
```yaml
env_file:
  - .env
```

The task reads credentials from env at runtime and applies them via `dvc remote modify --local` (writes to `.dvc/config.local` which is gitignored).

---

## How the Path Works

After `dvc pull data/published.dvc --remote dagshub-raw`, DVC writes the 2 upstream files into `data/published/`. The existing DAG reads from `RAW_CSV = Path("/opt/airflow/data/raw/data.csv")`.

To avoid changing `RAW_CSV` in the DAG, `dvc_utils.pull_raw_data()` finds the `.csv` file inside `data/published/` and copies it to `data/raw/data.csv` after a successful pull. This keeps the DAG's path constant stable and the fallback path consistent.

```
dvc pull data/published.dvc
    └── data/published/<file1>.csv   ← upstream CSV
    └── data/published/<file2>       ← other upstream file

dvc_utils copies CSV → data/raw/data.csv  ← DAG reads this (RAW_CSV unchanged)
```

If `data/published/` contains no `.csv` file after pull, the task raises `RuntimeError`.

---

## Task: `task_pull_raw_data`

**Type:** `PythonOperator` (thin wrapper — delegates to `src/features/dvc_utils.py`)
**Position:** First task in `feature_engineering_dag`

### Logic (in `src/features/dvc_utils.py`)

```python
def pull_raw_data(dvc_file, local_csv, remote, project_root):
    """
    Returns "pulled" or "skipped". Raises RuntimeError if neither succeeds.
    Pure Python — no Airflow imports, fully unit-testable.
    """
    import subprocess, os, shutil, glob, logging

    user = os.environ.get("DAGSHUB_USER")
    token = os.environ.get("DAGSHUB_TOKEN")

    if user and token:
        subprocess.run(
            ["dvc", "remote", "modify", remote, "--local", "user", user],
            cwd=project_root, check=True
        )
        subprocess.run(
            ["dvc", "remote", "modify", remote, "--local", "password", token],
            cwd=project_root, check=True
        )

    try:
        subprocess.run(
            ["dvc", "pull", dvc_file, "--remote", remote],
            cwd=project_root, check=True
        )
        # Find the CSV in data/published/ and copy to data/raw/data.csv
        published_dir = os.path.join(project_root, "data", "published")
        csv_files = glob.glob(os.path.join(published_dir, "*.csv"))
        if not csv_files:
            raise RuntimeError(f"DVC pull succeeded but no .csv found in {published_dir}")
        shutil.copy(csv_files[0], local_csv)
        logging.info(f"DVC pull succeeded — copied {csv_files[0]} → {local_csv}")
        return "pulled"

    except subprocess.CalledProcessError as e:
        logging.warning(f"DVC pull failed: {e}")
        if os.path.exists(local_csv):
            logging.warning(f"Falling back to existing local file: {local_csv}")
            return "skipped"
        raise RuntimeError(
            f"DVC pull failed AND no local file at {local_csv}. Cannot proceed."
        )
```

### DAG wrapper (`airflow/dags/feature_engineering_dag.py`)

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

**Why `cwd=PROJECT_ROOT`:** `data/raw` is mounted read-only at `/opt/airflow/data/raw` in the container. DVC runs from `/opt/airflow/project` (writable) so it writes `data/published/` via the writable project mount. The copy step then writes `data/raw/data.csv` via the writable project mount too. Both mounts point to the same host directories, so files are visible at their respective container paths.

### State transitions

| Outcome | Airflow task colour | Pipeline continues? |
|---|---|---|
| DVC pull succeeds, CSV copied | Green | Yes, with fresh data |
| DVC pull fails, local file exists | Yellow (skipped) | Yes, with existing data |
| DVC pull fails, no local file | Red (failed) | No — hard stop |

---

## DAG Wiring Change

`task_load_and_split` must use `TriggerRule.NONE_FAILED_MIN_ONE_SUCCESS` so it runs even when `task_pull_raw_data` is skipped:

```python
from airflow.utils.trigger_rule import TriggerRule

task_load_and_split = PythonOperator(
    ...
    trigger_rule=TriggerRule.NONE_FAILED_MIN_ONE_SUCCESS,
)

task_pull_raw_data >> task_load_and_split
```

---

## Files Changed

| File | Change |
|---|---|
| `.dvc/config` | Add `dagshub-raw` remote |
| `.gitignore` | Add `data/published/` and `docker/.env` |
| `data/published.dvc` | Copied from upstream nutchy repo (committed to git) |
| `src/features/dvc_utils.py` | New — `pull_raw_data()` function (pure Python, no Airflow) |
| `airflow/dags/feature_engineering_dag.py` | Add `_task_pull_raw_data` wrapper, update `task_load_and_split` trigger rule |
| `docker/docker-compose.yml` | Add `env_file: - .env` to `x-airflow-common` block |
| `docker/.env` | New file — `DAGSHUB_USER` + `DAGSHUB_TOKEN` (not committed) |
| `tests/test_dvc_utils.py` | New — unit tests for `pull_raw_data` |
| `tests/dags/test_feature_engineering_dag.py` | Update task list, replace + add structural tests |

---

## Testing

### Design constraint
`tests/dags/test_feature_engineering_dag.py` currently contains `test_no_dvc_or_git_in_dag`, which asserts that `subprocess` and `dvc` do not appear anywhere in the DAG file. The new task uses both.

**Solution:** Extract all DVC logic into `src/features/dvc_utils.py`. The DAG wrapper imports and calls it — no subprocess in the DAG file. This matches the project's existing pattern (business logic in `src/features/`, orchestration in DAGs) and keeps unit tests free of Airflow dependency.

---

### New module: `src/features/dvc_utils.py`

`pull_raw_data(dvc_file, local_csv, remote, project_root)` returns a string status:
- `"pulled"` — DVC pull succeeded, CSV copied to `local_csv`
- `"skipped"` — DVC pull failed but `local_csv` exists (fallback)

Raises `RuntimeError` when DVC pull fails and no local file exists, or when pull succeeds but no CSV is found in `data/published/`.

---

### Unit tests: `tests/test_dvc_utils.py`

| Test | subprocess.run | glob / os.path.exists | Expected result |
|---|---|---|---|
| `test_pull_success` | returns 0 | CSV found in published dir | returns `"pulled"` |
| `test_pull_success_csv_copied` | returns 0 | CSV found | `shutil.copy` called with correct paths |
| `test_pull_fails_local_exists` | raises `CalledProcessError` | `local_csv` exists | returns `"skipped"` |
| `test_pull_fails_no_local` | raises `CalledProcessError` | `local_csv` missing | raises `RuntimeError` |
| `test_pull_success_no_csv_in_published` | returns 0 | no CSV in published dir | raises `RuntimeError` |

Uses `unittest.mock.patch` on `subprocess.run`, `glob.glob`, `os.path.exists`, `shutil.copy`. No Airflow dependency needed.

---

### Updated DAG structure tests: `tests/dags/test_feature_engineering_dag.py`

| Test | Change |
|---|---|
| `EXPECTED_TASKS` | Add `"task_pull_raw_data"` (8 tasks total) |
| `test_has_all_seven_tasks` | Rename → `test_has_all_eight_tasks` |
| `test_no_dvc_or_git_in_dag` | Replace with `test_pull_task_calls_dvc_utils` — asserts `dvc_utils` is imported in the DAG, no direct `subprocess` anywhere |
| `test_pull_task_is_first` | New — asserts `task_pull_raw_data >> task_load_and_split` in source |
| `test_load_and_split_trigger_rule` | New — asserts `NONE_FAILED_MIN_ONE_SUCCESS` in source |

---

## What This Does NOT Do

- Does not pull "live" or "latest" data — `dvc pull` fetches the version pointed to by the committed `.dvc` pointer. When the team pushes a new data version, the repo owner must commit a new pointer, and you must `git pull` before the new version is available.
- Does not track features or artifacts on DagsHub — those remain on `localremote`.
- Does not push data to DagsHub — the upstream team owns that side.

---

## Future: `origin` Remote

When artifact versioning on a shared remote is needed, add:
```ini
['remote "origin"']
    url = https://dagshub.com/natchyunicorn/denttime.dvc
```
and update `make dvc-commit` to push to `origin` instead of `localremote`.
