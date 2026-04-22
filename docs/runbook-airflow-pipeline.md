# DentTime Feature Engineering Pipeline — Runbook

## Prerequisites

- Docker Desktop running with ≥ 6 GB RAM allocated
- Project cloned at any path (all mounts are relative to `docker/`)
- Raw data CSV at `data/raw/data.csv` (relative to project root)

---

## 1. Start the Stack

```bash
cd docker/
docker compose up --build -d
```

First run pulls `apache/airflow:2.9.0-python3.11` (~1 GB) and installs pip packages — takes ~3–5 min. Subsequent starts take ~30 s.

Wait for the webserver to become healthy:

```bash
docker compose ps
# airflow-webserver should show: Up (healthy)
```

---

## 2. Open the Airflow UI

Open [http://localhost:8080](http://localhost:8080) and log in with `admin / admin`.

---

## 3. Trigger the Full Pipeline

1. Click **DAGs** in the top nav
2. Find `denttime_feature_engineering` — toggle it **On** if paused
3. Click the DAG name → **Trigger DAG** (▶ button, top right)
4. Switch to **Grid** view to watch task progress

All 7 tasks should turn green in order:

```
task_load_and_split
  ├── task_build_doctor_profile
  ├── task_build_clinic_profile
  └── task_build_treatment_encoding
        ├── task_transform_train
        └── task_transform_test
              └── task_compute_feature_stats
```

Expected runtime: ~1–2 min on a typical laptop.

---

## 4. Selective Rerun (rerun one step + downstream)

Use this when source data or an artifact changes and you only need to rerun part of the pipeline.

| Goal | Task to clear |
|---|---|
| Rebuild everything | Trigger DAG fresh |
| New doctor data | `task_build_doctor_profile` |
| New clinic data | `task_build_clinic_profile` |
| New treatment dict | `task_build_treatment_encoding` |
| Re-transform only | `task_transform_train` |
| Recompute stats only | `task_compute_feature_stats` |

**How to clear a task:**
Grid view → click the coloured square for the task → **Clear** → confirm with **Also clear downstream ✓**

---

## 5. Version the Outputs (after a successful run)

From the project root:

```bash
make dvc-commit
git commit -m "feat: update features $(date +%Y-%m-%d)"
```

`make dvc-commit` runs `dvc add` on the 6 output files and stages the `.dvc` pointer files for git. The commit message is intentionally left to you — add context about what changed.

**Output files tracked:**

| File | Produced by |
|---|---|
| `features/features_train.parquet` | `task_transform_train` |
| `features/features_test.parquet` | `task_transform_test` |
| `features/feature_stats.json` | `task_compute_feature_stats` |
| `src/features/artifacts/doctor_profile.json` | `task_build_doctor_profile` |
| `src/features/artifacts/clinic_profile.json` | `task_build_clinic_profile` |
| `src/features/artifacts/treatment_encoding.json` | `task_build_treatment_encoding` |

---

## 6. Stop the Stack

```bash
cd docker/
docker compose down
```

Named volumes (`denttime-interim`, `denttime-dvc-store`, `postgres-db`) are preserved across restarts. To also delete volumes:

```bash
docker compose down -v   # WARNING: deletes interim splits and DVC store
```

---

## 7. Troubleshooting

### Task fails with `Permission denied` on `/opt/airflow/data/interim`

The `denttime-interim` volume was created before the permission fix. Restart the stack — `airflow-init` now runs `chown -R 50000:0` on the volumes at startup.

```bash
docker compose down && docker compose up -d
```

### Logs show `403 FORBIDDEN` / `secret_key` mismatch

The webserver and scheduler started with different random keys. The compose file pins `AIRFLOW__WEBSERVER__SECRET_KEY` — if you see this after a fresh `up`, check that all containers are using the same compose file version.

### `airflow-init` exits immediately with non-zero code

Check logs:

```bash
docker compose logs airflow-init
```

Common causes:
- Postgres not yet healthy (usually self-resolves — retry `docker compose up -d`)
- DVC not installed in image (rebuild: `docker compose up --build -d`)

### DAG does not appear in the UI

The `airflow/dags/` directory is bind-mounted to `/opt/airflow/dags`. If the DAG file has a syntax error, the scheduler silently drops it. Check:

```bash
docker compose exec airflow-scheduler airflow dags list
docker compose logs airflow-scheduler | grep ERROR
```

### `make dvc-commit` errors on `.gitignore`

DVC only updates `.gitignore` on first `dvc add`. Subsequent runs leave it unchanged, which causes `git add .gitignore` to fail. The Makefile already omits this — if you see it, pull the latest Makefile.

### Raw data not found (`/opt/airflow/data/raw/data.csv`)

The `Data Collection/` directory is bind-mounted read-only. Verify the file exists on the host:

```bash
ls "data/raw/data.csv"
```

---

## 8. Directory Reference (inside container)

| Container path | Source |
|---|---|
| `/opt/airflow/project` | Project root (`../`) — bind mount rw |
| `/opt/airflow/dags` | `airflow/dags/` — bind mount rw |
| `/opt/airflow/data/raw` | `Data Collection/` — bind mount **ro** |
| `/opt/airflow/data/interim` | Named volume `denttime-interim` |
| `/opt/airflow/dvc-store` | Named volume `denttime-dvc-store` |
