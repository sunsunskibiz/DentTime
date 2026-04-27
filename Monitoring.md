# DentTime Monitoring

This README documents only the monitoring part of the DentTime project: Data Diff monitoring, Prometheus metrics, Prometheus alert rules, Grafana dashboard, and demo scripts. It intentionally does not describe unrelated frontend, training, or other team members' modules.

## 1. Scope

This monitoring module answers four operational questions:

- Is the live input data distribution different from the reference data?
- Is the current prediction quality worse than the offline baseline?
- Is the model under-estimating treatment duration more often than expected?
- Are incomplete inputs increasing and possibly degrading prediction quality?

The monitoring flow is:

```text
/predict request
    -> prediction is stored in SQLite
    -> /actual can attach the real duration label later
    -> metrics_updater computes monitoring/state.json
    -> /metrics exposes Prometheus metrics
    -> Prometheus evaluates alert rules
    -> Grafana visualizes metrics and alert evidence
```

## 2. Monitoring Files

| Path | Purpose |
|---|---|
| `backend/app/routers/metrics.py` | Exposes `/metrics` for Prometheus. It publishes PSI, class ratio, logged prediction count, Macro F1, MAE, under-estimation rate, baseline values, and input missing rate. |
| `backend/app/routers/actual.py` | Provides `/actual` so the real treatment duration can be logged after a prediction. This is required for Macro F1, MAE, and under-estimation monitoring. |
| `backend/app/db.py` | Creates and updates the SQLite `predictions` table used as persistent monitoring storage. |
| `backend/app/routers/predict.py` | Stores every prediction with raw input and transformed features so the monitoring job can compare live data with reference data. |
| `monitoring/update_metrics.py` | Computes Data Diff and quality metrics from SQLite and writes them to `monitoring/state.json`. |
| `run_metrics_loop.py` | Runs `monitoring/update_metrics.py` continuously every 15 seconds inside the `metrics_updater` container. |
| `prometheus/prometheus.yml` | Configures Prometheus to scrape `api:8000/metrics`. |
| `prometheus/alerts.yml` | Defines DentTime alert rules for drift, Macro F1 drop, under-estimation increase, and input missing rate. |
| `grafana/provisioning/datasources/prometheus.yml` | Automatically connects Grafana to Prometheus. |
| `grafana/provisioning/dashboards/dashboard.yml` | Automatically loads the DentTime Grafana dashboard. |
| `grafana/dashboards/denttime-monitoring.json` | Grafana dashboard JSON for the monitoring panels. |
| `scripts/run_data_diff_demo.ps1` | Sends intentionally shifted requests to create Data Diff and trigger monitoring evidence. |
| `scripts/run_data_diff_demo.bat` | Windows shortcut for running the Data Diff demo script. |
| `scripts/run_critical_alert_demo.ps1` | Sends controlled requests and actual labels to make critical model-quality alerts fire. |
| `scripts/run_critical_alert_demo.bat` | Windows shortcut for running the critical alert demo script. |

## 3. Run the Monitoring Stack

Run this command from the project root:

```bash
docker compose -f docker/compose/frontend-backend.yml up --build
```

This starts:

| Service | URL | Purpose |
|---|---|---|
| FastAPI backend | `http://localhost:8000` | Prediction API and monitoring metrics endpoint |
| API docs | `http://localhost:8000/docs` | Swagger UI for testing `/predict`, `/actual`, and `/metrics` |
| Prometheus | `http://localhost:9090` | Scrapes `/metrics` and evaluates alert rules |
| Prometheus alerts | `http://localhost:9090/alerts` | Shows active, pending, and inactive alerts |
| Grafana | `http://localhost:3000` | Visual dashboard for monitoring results |
| Frontend | `http://localhost:5173` | DentTime web UI |

Grafana login:

```text
Username: admin
Password: admin
```

Dashboard URL:

```text
http://localhost:3000/d/denttime-prometheus/denttime-monitoring-dashboard?orgId=1
```

Stop the stack:

```bash
docker compose -f docker/compose/frontend-backend.yml down
```

## 4. Prometheus Metrics

| Metric | Meaning |
|---|---|
| `denttime_logged_predictions_total` | Number of prediction rows stored in SQLite. |
| `denttime_labeled_predictions_total` | Number of predictions that already have actual duration labels. |
| `denttime_feature_psi{feature="..."}` | Population Stability Index for each monitored feature. High PSI means live data distribution differs from the reference data. |
| `denttime_prediction_class_ratio{slot_minutes="..."}` | Ratio of predictions per duration class. This helps detect output distribution shift. |
| `denttime_macro_f1` | Current rolling Macro F1 computed from predictions that already have actual labels. |
| `denttime_macro_f1_baseline` | Offline baseline Macro F1 from `artifacts/baseline_metrics.json`. |
| `denttime_mae_minutes` | Current rolling MAE in minutes. |
| `denttime_underestimation_rate` | Ratio of labeled predictions where predicted duration is shorter than actual duration. |
| `denttime_underestimation_rate_baseline` | Offline baseline under-estimation rate from `artifacts/baseline_metrics.json`. |
| `denttime_input_missing_rate` | Recent ratio of missing important input fields. |

## 5. Alert Rules

The alert rules are defined in `prometheus/alerts.yml`.

| Alert | Rule | Severity | Meaning |
|---|---|---|---|
| `DentTimeFeatureDriftHigh` | `denttime_feature_psi > 0.25` for 1 minute | `warning` | At least one monitored feature has strong Data Diff from the reference distribution. |
| `DentTimeMacroF1Drop` | `denttime_macro_f1 < (denttime_macro_f1_baseline - 0.05)` for 1 minute | `critical` | Current Macro F1 is more than 0.05 below the offline baseline. This indicates model-quality degradation. |
| `DentTimeUnderEstimationHigh` | `denttime_underestimation_rate > (denttime_underestimation_rate_baseline + 0.05)` for 1 minute | `critical` | The model is under-estimating treatment duration more often than expected. This is operationally risky because appointments may be scheduled too short. |
| `DentTimeMissingRateHigh` | `denttime_input_missing_rate > 0.10` for 1 minute | `warning` | Missing input fields are increasing and may reduce prediction reliability. |

## 6. Data Diff and Retraining Decision

This monitoring module does not retrain the model automatically. It provides evidence for deciding whether retraining is needed.

Retraining should be considered when one or more of the following are true:

1. Many features have `denttime_feature_psi > 0.25`.
2. `DentTimeMacroF1Drop` is firing.
3. `DentTimeUnderEstimationHigh` is firing.
4. Data Diff remains high after checking that the input data is valid.
5. Missing input rate is high enough to affect prediction reliability.

Recommended interpretation:

| Situation | Decision |
|---|---|
| Only `DentTimeFeatureDriftHigh` is firing | Investigate Data Diff first. Check whether live inputs are valid or whether the user population has changed. |
| `DentTimeFeatureDriftHigh` + `DentTimeMacroF1Drop` are firing | Strong evidence that Data Diff is hurting model quality. Retraining is recommended. |
| `DentTimeUnderEstimationHigh` is firing | Critical operational risk. Review the model and consider retraining or recalibration. |
| `DentTimeMissingRateHigh` is firing | Improve data collection or validation before retraining, because missing data may be the root cause. |

## 7. Grafana Dashboard Panels

The Grafana dashboard contains these monitoring panels:

| Panel | Query | Purpose |
|---|---|---|
| `MAE (minutes)` | `denttime_mae_minutes` | Shows current prediction error in minutes. |
| `Input Missing Rate` | `denttime_input_missing_rate` | Shows whether important inputs are frequently missing. |
| `Logged Predictions (Persisted)` | `denttime_logged_predictions_total` | Confirms that predictions are being persisted in SQLite. |
| `Feature Drift (PSI)` | `denttime_feature_psi` | Shows Data Diff for each monitored feature. |
| `Prediction Class Ratio` | `denttime_prediction_class_ratio` | Shows distribution of predicted duration classes. |
| `Macro F1 vs Baseline` | `denttime_macro_f1`, `denttime_macro_f1_baseline` | Compares current model quality against baseline quality. |
| `Under-estimation Rate vs Baseline` | `denttime_underestimation_rate`, `denttime_underestimation_rate_baseline` | Shows whether the model is predicting treatment duration too short too often. |

## 8. Run the Data Diff Demo

Run this command from the project root on Windows:

```bat
scripts\run_data_diff_demo.bat
```

Or run the PowerShell script directly:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_data_diff_demo.ps1 -Total 80
```

What this script does:

- Sends intentionally shifted prediction requests to `/predict`.
- Uses unseen treatment names.
- Uses very large `totalAmount` values.
- Uses many tooth numbers and surfaces.
- Omits optional fields such as `doctorId` and `notes`.
- Optionally sends `/actual` labels so quality metrics can be computed.

Expected monitoring effect:

- `denttime_logged_predictions_total` increases.
- `denttime_feature_psi` increases for several features.
- `DentTimeFeatureDriftHigh` should become `FIRING` after Prometheus evaluates the rule for 1 minute.
- `denttime_input_missing_rate` may increase and can trigger `DentTimeMissingRateHigh`.

## 9. Run the Critical Alert Demo

Run this command from the project root on Windows:

```bat
scripts\run_critical_alert_demo.bat
```

Or run the PowerShell script directly:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_critical_alert_demo.ps1 -F1CriticalTotal 130 -UnderEstCriticalTotal 40
```

What this script does:

- Sends a batch designed to reduce rolling Macro F1.
- Sends another batch designed to increase the under-estimation rate.
- Logs actual duration labels through `/actual`.
- Waits for `metrics_updater` and Prometheus scraping.
- Prints expected alert states based on the Prometheus rules.

Expected monitoring effect:

- `DentTimeMacroF1Drop` should become `FIRING` when current Macro F1 is below `baseline - 0.05` for 1 minute.
- `DentTimeUnderEstimationHigh` should become `FIRING` when current under-estimation rate is above `baseline + 0.05` for 1 minute.
- If Prometheus shows `Pending`, wait at least 1 minute because the alert rules use `for: 1m`.

## 10. Manual Verification Commands

Check backend health:

```powershell
Invoke-RestMethod http://localhost:8000/
```

Check exported metrics:

```powershell
Invoke-WebRequest http://localhost:8000/metrics -UseBasicParsing
```

Search for key metrics:

```powershell
(Invoke-WebRequest http://localhost:8000/metrics -UseBasicParsing).Content | Select-String "denttime_macro_f1|denttime_underestimation_rate|denttime_feature_psi|denttime_input_missing_rate"
```

Check Prometheus alerts in browser:

```text
http://localhost:9090/alerts
```

Check Grafana dashboard in browser:

```text
http://localhost:3000/d/denttime-prometheus/denttime-monitoring-dashboard?orgId=1
```

Check container logs:

```bash
docker compose -f docker/compose/frontend-backend.yml logs -f api
docker compose -f docker/compose/frontend-backend.yml logs -f metrics_updater
docker compose -f docker/compose/frontend-backend.yml logs -f prometheus
docker compose -f docker/compose/frontend-backend.yml logs -f grafana
```

## 11. Troubleshooting

| Problem | Likely Cause | Fix |
|---|---|---|
| Grafana dashboard is empty | Prometheus has not scraped metrics yet. | Wait 30-60 seconds and refresh the dashboard. |
| Alerts show `Pending` instead of `FIRING` | Alert rule has `for: 1m`. | Wait at least 1 minute. |
| `denttime_macro_f1` does not appear | There are no predictions with actual labels yet. | Send `/actual` labels or run the critical alert demo script. |
| `denttime_feature_psi` does not appear | No live prediction features have been logged yet or `metrics_updater` has not refreshed. | Send prediction requests and wait for `metrics_updater`. |
| `metrics_updater` fails | SQLite, artifact, or reference file path may be missing. | Check mounted paths in `docker/compose/frontend-backend.yml` and logs from the `metrics_updater` container. |
| Prometheus cannot scrape the API | The `api` service is not running or not reachable from Prometheus. | Check `docker compose ps` and confirm `api:8000` is used inside `prometheus/prometheus.yml`. |

## 12. Notes for Presentation

This monitoring work demonstrates the Monitoring and Alerting requirement because it includes:

1. Persistent prediction logging in SQLite.
2. Data Diff calculation using PSI.
3. Model-quality monitoring using Macro F1 and MAE.
4. Operational-risk monitoring using under-estimation rate.
5. Data-quality monitoring using input missing rate.
6. Prometheus metric exposure through `/metrics`.
7. Prometheus alert rules with warning and critical severity.
8. Grafana dashboard visualization.
9. Demo scripts that can intentionally create drift and critical alert conditions.

The important point is that the system does not only show a dashboard. It connects live predictions, actual labels, Data Diff computation, Prometheus metrics, alert rules, and Grafana visualization into one monitoring pipeline.
