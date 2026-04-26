# DentTime Monitoring Safe Merge Notes

This merge uses `DentTime-main (7).zip` as the source of truth for the team main branch and adds only the Monitoring & Alerting pipeline from `Dent-Time-main (1).zip`.

## What was preserved from team main

- `frontend/src/**` is preserved from the team main branch.
- Main frontend contract is preserved: `treatmentSymptoms`, `toothNumbers`, `surfaces`, `selectedDateTime`, `totalAmount`, `doctorId`, `clinicId`, `notes`.
- Feature engineering modules in `src/features/**` are preserved.
- Model artifact in `artifacts/model.joblib` is preserved.

## What was added for P5 Monitoring & Alerting

- Root `docker-compose.yml` for demo runtime: FastAPI, frontend, Prometheus, Grafana, metrics updater.
- `backend/app/db.py` for SQLite prediction logging.
- `backend/app/monitoring_metrics.py` exposing `/metrics` in Prometheus format.
- Prediction logging inside `backend/app/routers/predict.py`.
- Actual outcome update inside `backend/app/routers/actual.py`.
- `monitoring/update_metrics.py` for PSI, prediction distribution, MAE, Macro F1, under-estimation rate, and missing input rate.
- `prometheus/prometheus.yml` and `prometheus/alerts.yml`.
- Grafana provisioning and `grafana/dashboards/denttime-monitoring.json`.
- Runtime feature artifacts under `src/features/artifacts/*.json` so the demo can run without DVC remote access.
- `data/reference/reference_features.parquet` for drift baseline.
- `smoke_test_integration.py` for `predict -> actual -> metrics` verification.

## Important merge decision

The old Monitoring-Alerting frontend was not copied into this merge. This prevents overwriting the newer team frontend from `DentTime-main (7).zip`.

## Recommended verification commands

```powershell
docker compose down --remove-orphans
docker compose up --build -d
docker compose ps
docker compose exec api python smoke_test_integration.py
docker compose exec api python monitoring/update_metrics.py
```

Open:

- Frontend: http://localhost:5173
- FastAPI Swagger: http://localhost:8000/docs
- Prometheus: http://localhost:9090
- Grafana: http://localhost:3000

Grafana default login: `admin` / `admin`.
