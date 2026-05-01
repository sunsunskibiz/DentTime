## Project Overview

DentTime predicts dental appointment duration (classification into {15, 30, 45, 60, 75, 90, 105} minutes) using an XGBoost model trained on 17 engineered features.

> 📊 **Presentation:** For a comprehensive overview of the project architecture, design decisions, and final results, please check out our [Final Presentation](report/presentation/DentTime-final.odp).

---

## Architecture Overview

### System Context

```mermaid
C4Context
    title DentTime — System Context
    Person(clinicStaff, "Clinic Staff", "Books appointments and fills patient symptoms to schedule appointments")
    Person_Ext(dentist, "Dentist", "Reviews balanced schedule and benefits from accurate time slots")
    Person_Ext(clinicAdmin, "Clinic Admin", "Monitors scheduling efficiency and resource utilization")
    System(denttime, "DentTime System", "Predicts appointment duration via XGBoost. Classifies into 7 slots: 15/30/45/60/75/90/105 min. p99 < 1s.")
    System_Ext(cms, "Clinic Management System", "Source of historical appointment and treatment records (~1M rows)")
    System_Ext(gcs, "Google Cloud Storage", "Stores versioned ML model artifacts")
    System_Ext(gke, "GKE Autopilot", "Managed Kubernetes platform for production deployment")
    Rel(clinicStaff, denttime, "Fills symptoms, receives predicted time window", "HTTPS")
    Rel(dentist, denttime, "Views scheduled slots and workload summary", "HTTPS")
    Rel(clinicAdmin, denttime, "Monitors performance metrics", "HTTPS")
    Rel(cms, denttime, "Provides training records", "SQL / CSV Export")
    Rel(denttime, gcs, "Loads and updates versioned model artifacts", "GCS API")
    Rel(denttime, gke, "Deployed and auto-scaled on", "Kubernetes")
```

### CI/CD Pipeline

![DentTime CI/CD Pipeline](c4/denttime_cicd_simplified_v2.svg)

---

## Project Structure

```
DentTime/
├── data/
│   └── raw/                          # Anonymized input data (git-ignored)
│       └── data.csv                  # ← place file here before running
├── feature_engineering.py            # Standalone feature engineering script
├── src/features/                     # Feature engineering modules
│   ├── feature_transformer.py        # FeatureTransformer + FEATURE_COLUMNS
│   ├── build_profiles.py             # Doctor & clinic profile builders
│   ├── treatment_mapper.py
│   ├── tooth_parser.py
│   └── artifacts/                    # Fitted artifacts (DVC-tracked)
│       ├── doctor_profile.json
│       ├── clinic_profile.json
│       └── treatment_encoding.json
├── features/                         # Pipeline outputs (DVC-tracked)
│   ├── features_train.parquet
│   ├── features_test.parquet
│   └── feature_stats.json
├── airflow/dags/
│   ├── feature_engineering_dag.py    # 7-task feature engineering DAG
│   └── denttime_retrain_dag.py       # 5-task retrain DAG (with MLflow)
├── Trianing/
│   └── train.py                      # Standalone training script
├── backend/app/                      # FastAPI inference service
│   ├── main.py
│   ├── routers/
│   └── services/
├── frontend/                         # React 19 + Vite + Chakra UI + Tailwind
├── monitoring/                       # Metrics state (state.json)
├── prometheus/                       # Alert rules
├── grafana/                          # Dashboard provisioning
├── artifacts/
│   └── model.joblib                  # Serialized model bundle
├── docker-compose.yml                # Unified compose (profiles: training, serving)
├── Makefile
└── docs/
    ├── runbook-airflow-pipeline.md
    └── ADR-001-airflow-feature-pipeline.md
```

---

## Data

Raw data is produced by a separate, access-controlled pipeline maintained by [@natchyunicorn](https://github.com/natchyunicorn). Place the anonymized output at `data/raw/data.csv` before running the pipeline. Contact the data owner for access.

This repo contains no patient data and no PII.

---

## Quick Start

All services are defined in the root `docker-compose.yml` and activated via profiles.

| Profile | Make target | Services | Ports |
|---|---|---|---|
| `training` | `make up-train` | Airflow, MLflow, Postgres | :8080, :5008 |
| `serving` | `make up-serve` | Backend, Frontend, Prometheus, Grafana | :8001, :5173, :9090, :3000 |
| _(both)_ | `make up` | All of the above | all ports |

```bash
# Feature engineering + model retraining stack
make up-train

# Web app + monitoring stack
make up-serve

# Full stack (demo mode)
make up

# Stop everything
make down

# Validate compose syntax
make validate
```

---

## Feature Engineering Pipeline — Airflow

Runs as 7 independent tasks. Each task can be rerun without rerunning the whole pipeline.

**Prerequisites:** Docker Desktop with ≥ 6 GB RAM allocated.

```bash
# 1. Start training stack
make up-train

# 2. Open Airflow UI: http://localhost:8080  (admin / admin)
#    DAGs → denttime_feature_engineering → ▶ Trigger DAG

# 3. After all 7 tasks turn green — version the outputs
make dvc-commit
git commit -m "feat: update features $(date +%Y-%m-%d)"

# 4. Stop
make down
```

### Task Graph

```mermaid
graph LR
    A[task_load_and_split] --> B[task_build_treatment_encoding]
    A --> C[task_build_doctor_profile]
    A --> D[task_build_clinic_profile]
    B --> E[task_transform_train]
    B --> F[task_transform_test]
    C --> E
    C --> F
    D --> E
    D --> F
    E --> G[task_compute_feature_stats]
    F --> G
```

All inter-task communication is via files on shared volumes — no Airflow XCom.

---

## Standalone Feature Engineering (no Docker)

```bash
pip install -r requirements-fe.txt
python feature_engineering.py --input "data/raw/data.csv" --output features/
```

---

## Model Retraining

The `denttime_retrain_dag` (5 tasks) runs inside the training stack and tracks experiments with MLflow.

```
features/features_{train,test}.parquet
  └─► load_features → train_model → rank_features → evaluate_model → export_artifacts
        └── writes models/model.joblib
```

MLflow UI: http://localhost:5008

For standalone training without Docker:

```bash
cd Trianing/
pip install -r requirements.txt
python train.py
```

---

## Frontend + Backend + Monitoring

```bash
make up-serve
```

| Service | URL |
|---|---|
| Backend API docs | http://localhost:8001/docs |
| Frontend | http://localhost:5173 |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 |

The `metrics_updater` service runs every 15 s, computing PSI, F1, and MAE from SQLite and writing `monitoring/state.json` for Prometheus.

---

## Tests

```bash
pip install -r requirements-fe.txt
pytest tests/ -v                                   # all tests
pytest tests/test_feature_transformer.py -v        # single file
pytest tests/dags/ -v                              # DAG structure tests (no Airflow needed)
```

---

## Data Versioning (DVC)

Outputs are tracked with DVC. To restore the last committed feature set:

```bash
dvc checkout
```

To version new outputs after a pipeline run:

```bash
make dvc-commit
git commit -m "feat: update features $(date +%Y-%m-%d)"
```

---

## Frontend Development

```bash
cd frontend/
npm run dev     # dev server :5173
npm run lint    # eslint
npm run build   # tsc + vite build
```

---

## Monitoring Demo Scripts

Three scripts simulate and reset monitoring degradation for classroom / grader demos. All require the serving stack (`make up-serve`, backend at `http://localhost:8001`).

| Script | What it does | Effect |
|---|---|---|
| `reset_alerts.sh` | Clears SQLite predictions, rewrites `monitoring/state.json` to baseline, waits for `metrics_updater` | All alerts resolve to green |
| `run_data_diff_demo.sh` | 80 parallel requests with unseen treatments + extreme feature values | `FeatureDriftHigh`, `MissingRateHigh`, `MacroF1Drop` |
| `run_critical_alert_demo.sh` | 170 parallel requests with wrong actual labels in two batches | `MacroF1Drop`, `UnderEstimationHigh`, `MissingRateHigh` |

**macOS / Linux** (requires `curl` and `python3`, both built-in on macOS):

```bash
# 0. Reset to baseline — run this before every demo
bash scripts/reset_alerts.sh

# 1a. Data drift demo — PSI > 0.25 on several features
bash scripts/run_data_diff_demo.sh

# 1b. Critical alert demo — Macro F1 drop + under-estimation
#     (reset first if switching between demos)
bash scripts/reset_alerts.sh
bash scripts/run_critical_alert_demo.sh
```

**Windows (PowerShell)**:

```powershell
# Data drift demo
scripts\run_data_diff_demo.bat

# Critical alert demo
scripts\run_critical_alert_demo.bat
```

After the script finishes, open:

| Page | URL |
|---|---|
| Grafana dashboard | http://localhost:3000/d/denttime-prometheus/denttime-monitoring-dashboard |
| Prometheus alerts | http://localhost:9090/alerts |
| Raw metrics | http://localhost:8001/metrics |

> **Note:** If Prometheus shows `Pending` instead of `Firing`, wait ~1 minute — alert rules use `for: 1m`.
