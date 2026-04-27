# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

DentTime predicts dental appointment duration (classification into {15, 30, 45, 60, 90, 105} minutes) using an XGBoost model trained on 17 engineered features. The system is split across four independent Docker stacks that must be started separately.

---

## Commands

### Feature Engineering Pipeline (Airflow + MLflow)
```bash
# Start stack (Airflow :8080, MLflow :5000, Postgres)
cd docker/ && docker compose up --build -d

# Trigger DAG in UI: http://localhost:8080 (admin/admin)
# After all 7 tasks complete, version the outputs:
make dvc-commit
git commit -m "feat: update features $(date +%Y-%m-%d)"

# Stop
cd docker/ && docker compose down
```

### Standalone Feature Engineering (no Docker)
```bash
pip install -r requirements-fe.txt
python feature_engineering.py --input "data/raw/data.csv" --output features/
```

### Frontend + Backend (Inference)
```bash
docker compose -f docker/compose/frontend-backend.yml up --build
# Backend: http://localhost:8000/docs  Frontend: http://localhost:5173
```

### Monitoring Stack (Prometheus + Grafana)
```bash
cd Monitoring-Alerting/ && docker compose up --build -d
# API :8000 | Prometheus :9090 | Grafana :3000 (admin/admin) | Frontend :5173
```

### Standalone Training
```bash
cd Trianing/
pip install -r requirements.txt
python train.py   # expects data/ symlinked or copied from features/
```

### Tests
```bash
pip install -r requirements-fe.txt
pytest tests/ -v                                   # all tests
pytest tests/test_feature_transformer.py -v        # single file
pytest tests/dags/ -v                              # DAG structure tests (no Airflow needed)
```

### Frontend
```bash
cd frontend/
npm run dev     # dev server :5173
npm run lint    # eslint
npm run build   # tsc + vite build
```

---

## Architecture

### Four Independent Stacks

| Stack | Compose file | Purpose |
|---|---|---|
| Airflow + MLflow | `docker/docker-compose.yml` | Feature engineering + model retraining |
| Frontend + Backend | `docker/compose/frontend-backend.yml` | Inference UI + API |
| Monitoring | `Monitoring-Alerting/docker-compose.yml` | Live monitoring with Prometheus/Grafana |
| Training | `Trianing/train.py` (standalone) | Ad-hoc model training with MLflow |

The stacks are not connected at runtime. Artifacts flow through the filesystem: feature pipeline outputs land in `features/` and `src/features/artifacts/`; the retrain DAG writes to `models/`; the inference backend loads from `artifacts/model.joblib`.

### ML Pipeline (Feature Engineering → Model)

```
data/raw/data.csv
  └─► feature_engineering_dag (7 Airflow tasks, all file-based — no XCom)
        ├── task_load_and_split          → data/interim/train_split.parquet, test_split.parquet
        ├── task_build_treatment_encoding→ src/features/artifacts/treatment_encoding.json
        ├── task_build_doctor_profile   → src/features/artifacts/doctor_profile.json
        ├── task_build_clinic_profile   → src/features/artifacts/clinic_profile.json
        ├── task_transform_train        → features/features_train.parquet
        ├── task_transform_test         → features/features_test.parquet
        └── task_compute_feature_stats  → features/feature_stats.json

features/features_{train,test}.parquet
  └─► denttime_retrain_dag (5 Airflow tasks, with MLflow tracking)
        load_features → train_model → rank_features → evaluate_model → export_artifacts
        └── writes models/model.joblib (dict bundle — see below)
```

Data split is time-based: `appt_year_month <= "2025-02"` → train, `"2025-04"` → test.

**LEAKAGE_COLUMNS** `{"checkin_delay_min", "tx_record_offset_min", "receipt_offset_min"}` are dropped at the start of every pipeline run.

### Model Bundle Format

`artifacts/model.joblib` and `models/model.joblib` are joblib-serialized dicts:
```python
{
    "model": XGBClassifier,
    "feature_cols": [...],           # 17 feature names
    "index_to_class": {0:15, 1:30, 2:45, 3:60, 4:90, 5:105},
    "label_encoder": LabelEncoder,
    "model_version": "denttime_model_...",
}
```
The inference backend (`backend/app/routers/predict.py`) uses `index_to_class` to decode model output. A dummy `scheduled_duration_min=30` is injected at inference time (not a real input — it's dropped before prediction).

### Two Separate FastAPI Apps

**`backend/app/`** — inference-only:
- Loads model + FeatureTransformer at startup via `lifespan`
- `POST /predict` → runs `FeatureTransformer.transform()` → XGBoost → returns duration class
- `POST /actual` → mock in-memory logging (no persistence)
- `GET /options` → returns available doctors, clinics, treatments from profile JSONs

**`Monitoring-Alerting/app/`** — monitoring API:
- Real SQLite persistence (`data/denttime.db`)
- Same `/predict` and `/actual` endpoints but writes to SQLite
- `GET /metrics` → Prometheus exposition format (reads from `monitoring/state.json`)
- `monitoring/update_metrics.py` runs every 15 s via a sidecar container; computes PSI, F1, MAE from SQLite and writes `state.json`

### Monitoring Alert Rules (`Monitoring-Alerting/prometheus/alerts.yml`)

| Alert | Condition | Severity |
|---|---|---|
| FeatureDriftHigh | any feature PSI > 0.25 | warning |
| MacroF1Drop | macro_f1 < baseline − 0.05 | critical |
| UnderEstimationHigh | under_rate > baseline + 0.05 | critical |
| MissingRateHigh | input missing rate > 0.10 | warning |

The retrain trigger (P5 work) connects these Prometheus alerts → webhook → Airflow REST API → `denttime_retrain` DAG.

### Frontend (`frontend/`)

React 19 + Vite + TypeScript, styled with Chakra UI v3 + Tailwind CSS v4. Pages: `landing`, `login`, `predict`, `about`, `how-it-works`. The `predict` page calls `POST /predict` and displays the predicted duration class with confidence breakdown.

### Feature Engineering Modules (`src/features/`)

- `feature_transformer.py` — `FeatureTransformer` class; `FEATURE_COLUMNS` (17 names) and `LEAKAGE_COLUMNS` are defined here and imported by DAGs and the backend
- `build_profiles.py` — builds doctor/clinic median duration and pct_long profiles from training data
- `treatment_mapper.py` + `tooth_parser.py` — parse raw treatment strings and tooth notation

### DVC-Tracked Artifacts

`make dvc-commit` stages `.dvc` pointer files for:
- `features/features_train.parquet`, `features/features_test.parquet`, `features/feature_stats.json`
- `src/features/artifacts/doctor_profile.json`, `clinic_profile.json`, `treatment_encoding.json`

`artifacts/model.joblib` is NOT currently DVC-tracked — it is committed directly or loaded from `models/` after a retrain run.

### Duplicate Retrain DAGs

`airflow/dags/` contains two retrain DAGs:
- `denttime_retrain_dag.py` — **current version** (P3), 5 tasks, MLflow tracking, feature ranking
- `retrain_dag.py` — older version, 4 tasks, no MLflow — kept for reference

Use `denttime_retrain_dag` when triggering retrains.
