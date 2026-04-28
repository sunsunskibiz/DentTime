# ADR-002: Pipeline Scope for Prometheus-Triggered Retraining

**Status:** Proposed  
**Date:** 2026-04-28 (revised same day)  
**Deciders:** MLOps team (P3/P4/P5), reviewed against SE for ML course requirements

---

## Context

The monitoring system fires Prometheus alerts when the live model degrades. The retrain trigger service (to be implemented per the onboarding guide) must decide which Airflow DAG(s) to call.

DentTime has two sequential pipelines:

```
data/raw/data.csv
      │
      ▼
[Feature Engineering DAG]  — 7 tasks, ~1–2 min
  task_load_and_split
  task_build_treatment_encoding
  task_build_doctor_profile
  task_build_clinic_profile
  task_transform_train / task_transform_test
  task_compute_feature_stats
      │
      ▼ outputs: features_train.parquet, features_test.parquet, profile JSONs
      │
      ▼
[ML Retrain DAG]  — 5 tasks, ~5–15 min
  task_load_features
  task_train_model (XGBoost + MLflow)
  task_rank_features (permutation importance)
  task_evaluate_model (champion/challenger gate)
  task_export_artifacts
```

There are four alert types, each with a different root cause:

| Alert | Severity | Root cause |
|---|---|---|
| `DentTimeFeatureDriftHigh` | warning | Input data distribution has shifted away from training reference |
| `DentTimeMacroF1Drop` | critical | Model predictions no longer match real outcomes |
| `DentTimeUnderEstimationHigh` | critical | Model systematically under-predicts long procedures |
| `DentTimeMissingRateHigh` | warning | Upstream data quality problem (not a model issue) |

The question: when a critical alert fires, should the trigger call only the ML Retrain DAG, or should it first run the Feature Engineering DAG and then the ML Retrain DAG?

### Critical constraint discovered during review

The ML Retrain DAG trains exclusively on `features/features_train.parquet`, a static file built from `data/raw/data.csv` with a fixed time split (`appt_year_month <= "2025-02"`). **This file does not change unless the Feature Engineering DAG is re-run on new raw data.** The live predictions stored in SQLite — which are what degraded F1 and under-estimation rate are measured against — are never fed back into the training set automatically.

This means: **if `data/raw/data.csv` has not been updated with new appointment records, triggering either the ML-only or the full pipeline will produce essentially the same model as before.** Neither pipeline can recover performance from training data it has never seen.

This constraint rules out "ML-only as a fast recovery path" — retraining on the same parquet files does not address the root cause of any alert.

---

## Decision

**Always trigger the full pipeline (Feature Engineering → ML Retrain), but only when new raw data is confirmed available. Gate the trigger on a data freshness check before calling any DAG.**

- **Any critical alert + new data available → Full pipeline:** Feature Engineering rebuilds profiles and feature files from the latest raw data, then ML Retrain trains a new model on that fresh basis. This is the only path that meaningfully improves the model.

- **Any critical alert + no new raw data → Escalate to human, no automatic retrain.** An automatic retrain would reproduce the same model. The alert requires investigation: if the degradation is a transient outlier batch in live traffic, it may resolve on its own; if it reflects a systematic real-world change, the fix is to ingest new appointment data first.

- **`DentTimeMissingRateHigh` → No retrain regardless.** This is an upstream data quality problem. No pipeline will fix missing input fields.

---

## Options Considered

### Option A: Always trigger full pipeline (Feature Engineering → ML Retrain)

| Dimension | Assessment |
|---|---|
| Correctness | High — rebuilds all profiles and features from latest data |
| Recovery time | Slower — feature engineering adds ~2 min before training starts |
| Complexity | Low — single trigger path, no conditional logic |
| Reproducibility | Highest — each retrain is fully self-contained from raw data |
| Risk | Wasted compute if raw data hasn't changed; same model produced |

**Pros:**
- Simplest trigger logic.
- Rebuilds doctor/clinic/treatment profiles — captures new dentists, new treatment types.
- Satisfies reproducibility requirements.

**Cons:**
- If `data/raw/data.csv` has not been updated since the last run, feature engineering reruns produce identical outputs and the retrained model is identical to the current one — no recovery.
- Chaining two DAGs requires polling or a wrapper DAG.

---

### Option B: Always trigger ML-only pipeline (ML Retrain DAG only)

| Dimension | Assessment |
|---|---|
| Correctness | Low — trains on the same static parquet files; produces nearly the same model |
| Recovery time | Faster — starts immediately |
| Complexity | Lowest — single DAG call |
| Reproducibility | Lower — stale feature profiles may persist |
| Risk | **High** — silent non-recovery: a new model is promoted but F1 does not improve |

**Pros:**
- Fastest path to *a* new model.

**Cons:**
- The ML Retrain DAG reads `features_train.parquet`, which is built from data up to "2025-02" and does not include any live prediction outcomes from SQLite. Retraining on this unchanged file produces essentially the same model.
- Does not address the root cause of any alert type. Class imbalance in live labeled data, outlier batches, and distribution shift are all properties of the live traffic — they do not appear in `features_train.parquet` and cannot be corrected by retraining on it.
- The champion/challenger gate (`macro_f1 drop ≤ 0.05`) will likely pass for the "new" model precisely because it is the same model — giving false confidence that recovery occurred.

**Verdict: ruled out.** This option creates the appearance of a retrain loop without the substance.

---

### Option C: Full pipeline gated on data freshness check (recommended)

| Dimension | Assessment |
|---|---|
| Correctness | Highest — only retrains when new data makes it meaningful |
| Recovery time | Slightly slower (freshness check is fast, ~ms) |
| Complexity | Medium — trigger service needs a pre-flight check before DAG calls |
| Reproducibility | High — full pipeline when triggered |
| Explainability | High — clear gate prevents meaningless retrains |

**Pros:**
- Prevents the silent non-recovery failure of Option B.
- Prevents wasted compute from Option A when data is stale.
- Forces the team to confront the real prerequisite — data ingestion — when no new data exists.
- Full pipeline ensures feature profiles reflect the latest clinic data every time a genuine retrain happens.

**Cons:**
- Requires a data freshness check in the trigger service (comparing `data/raw/data.csv` modification time against `features/features_train.parquet`).
- When no new data is available and an alert fires, the system escalates to human rather than self-healing. This is correct behaviour but requires an alerting/notification path to a human operator.

---

## Options Considered

### Option A: Always trigger full pipeline (Feature Engineering → ML Retrain)

| Dimension | Assessment |
|---|---|
| Correctness | High — ensures model always trains on freshly computed features |
| Recovery time | Slower — feature engineering adds ~2 min before training can start |
| Complexity | Low — single trigger path, no conditional logic |
| Reproducibility | Highest — each retrain is fully self-contained from raw data |
| Risk | Low risk of stale feature profiles causing silent errors |

**Pros:**
- Simplest trigger logic: one DAG sequence always.
- Rebuilds doctor/clinic/treatment profiles from the latest data — if a new dentist joined or new treatments were added, the model benefits immediately.
- Satisfies reproducibility requirements (the instructor's feedback on reproducible datasets applies here).
- If raw data has been updated since last feature run (new appointments added to `data/raw/data.csv`), the model trains on more data automatically.

**Cons:**
- Feature engineering adds time even when the root cause is purely a model issue.
- If `data/raw/data.csv` has not been updated, feature engineering reruns produce identical outputs — wasted compute.
- Chaining two DAGs requires either a sensor DAG or a wrapper DAG, adding orchestration complexity.

---

### Option B: Always trigger ML-only pipeline (ML Retrain DAG only)

| Dimension | Assessment |
|---|---|
| Correctness | Moderate — assumes features are always current |
| Recovery time | Faster — retraining starts immediately |
| Complexity | Lowest — direct single DAG call |
| Reproducibility | Lower — feature profiles may be stale |
| Risk | Silent failure if doctor/clinic profiles no longer reflect reality |

**Pros:**
- Fastest path to a new model.
- The ML Retrain DAG already validates that the new model beats the baseline before promoting it, so a bad retrain is caught.
- Features rarely change unless raw data changes — if no new appointments have arrived, running feature engineering adds nothing.

**Cons:**
- If the alert was caused by distribution shift in the underlying clinic data (new doctor patterns, new treatment types), retraining on stale feature profiles will produce a model that is still misaligned.
- Under-estimation alerts specifically indicate the model is wrong about long procedures — this is often tied to changes in doctor or clinic profiles, which feature engineering fixes.
- Violates the spirit of the pipeline design: the ML DAG reads `features_train.parquet`, but that file was built on data up to "2025-02". If new data is available, it stays unused.

---

### Option C: Conditional trigger based on alert type (recommended)

| Dimension | Assessment |
|---|---|
| Correctness | Highest — matches root cause to pipeline scope |
| Recovery time | Fast for performance alerts, slightly slower for drift alerts |
| Complexity | Medium — trigger service needs alert-type routing logic |
| Reproducibility | High — full pipeline when data may have changed |
| Explainability | High — clear reasoning for why each path was chosen |

**Pros:**
- Each alert type is matched to the correct recovery action.
- Avoids unnecessary feature engineering runs when features are still valid.
- Avoids the silent risk of training on stale profiles when data has drifted.

**Cons:**
- Trigger service needs a routing table (but this is a small addition to the webhook receiver already planned).
- Chaining Feature Engineering → ML Retrain still needs an Airflow sensor or sequential triggering.

---

## Trade-off Analysis

The central insight is that **the ML Retrain DAG has no path to improvement without updated training data.** It reads static parquet files; the live predictions in SQLite that are causing the alert are invisible to it. This means the choice is not "full pipeline vs. ML-only" but "full pipeline with new data vs. no meaningful retrain at all."

The actual trade-off is therefore between **automated recovery when data is fresh** vs. **human escalation when it isn't.**

For the dental clinic context, chronic under-estimation is the highest-priority business risk — scheduling too short cascades into overrun appointments for the rest of the day. But automatically triggering a retrain that produces the same model provides false assurance while wasting compute and Airflow executor capacity. Escalating to human when no new data exists is the safer and more honest behaviour: it forces the team to ask "what changed in the clinic?" before retraining.

When new data is available, the full pipeline is the only meaningful option. Feature Engineering must run first because it is the only step that ingests new appointment records into the training set and rebuilds the doctor/clinic/treatment profiles that capture real-world behavioural patterns.

---

## Consequences

**What becomes easier:**
- Each triggered retrain is guaranteed to incorporate new data — the champion/challenger gate in `task_evaluate_model` now has a real chance of passing because the model genuinely trained on something new.
- Feature profiles stay in sync with real clinic behaviour (new doctors, new treatments) every time a retrain runs.
- The audit trail is honest: "retrain skipped — no new data" is a meaningful log entry, whereas "retrain completed — same model promoted" is a silent failure.

**What becomes harder:**
- The trigger service must perform a data freshness pre-flight check before calling any DAG.
- When no new data exists and an alert fires, a human must be notified. This requires an alerting path (Alertmanager email/Line receiver, or at minimum a prominent log entry that monitoring can surface).
- Chaining Feature Engineering → ML Retrain requires polling or a `TriggerDagRunOperator` wrapper DAG.

**What we'll need to revisit:**
- The debounce interval (currently 4 hours) is less important once the freshness gate exists — a retrain only triggers when data actually changed, so repeated spurious triggers are naturally suppressed.
- When new appointment data is ingested into `data/raw/data.csv` automatically (not yet implemented), the freshness check becomes the single gate that enables the full retrain loop without any other changes to this design.

---

## Implementation in the Retrain Trigger

Update `Monitoring-Alerting/retrain_trigger/main.py` to replace the old routing table with a data freshness gate:

```python
import os
from pathlib import Path

RAW_DATA_PATH      = Path(os.getenv("RAW_DATA_PATH",      "/opt/airflow/project/data/raw/data.csv"))
FEATURES_PATH      = Path(os.getenv("FEATURES_PATH",      "/opt/airflow/project/features/features_train.parquet"))

# Alerts that should never trigger a retrain (data quality issue, not model issue)
SKIP_ALERTS = {"DentTimeMissingRateHigh"}


def check_new_data_available() -> bool:
    """Return True only if raw data has been updated after the last feature run."""
    if not RAW_DATA_PATH.exists():
        return False  # no raw data at all — cannot retrain
    if not FEATURES_PATH.exists():
        return True   # features never built — always trigger
    return RAW_DATA_PATH.stat().st_mtime > FEATURES_PATH.stat().st_mtime


@app.post("/alert")
async def receive_alert(request: Request):
    global _last_trigger_ts
    payload = await request.json()
    alerts = payload.get("alerts", [])

    retrain_worthy = [
        a for a in alerts
        if a.get("status") == "firing"
        and a.get("labels", {}).get("alertname") not in SKIP_ALERTS
    ]

    if not retrain_worthy:
        return {"status": "skipped", "reason": "no_retrain_worthy_alerts"}

    # Gate: only proceed if new data exists
    if not check_new_data_available():
        alert_names = [a["labels"]["alertname"] for a in retrain_worthy]
        log.warning(
            "Alert(s) %s fired but raw data has not changed since last feature run. "
            "Retrain skipped — human review required.",
            alert_names,
        )
        # TODO: send notification to human operator here
        return {"status": "skipped", "reason": "no_new_data", "alerts": alert_names}

    # Debounce
    now = time.time()
    if now - _last_trigger_ts < MIN_RETRAIN_INTERVAL_S:
        remaining_min = int((MIN_RETRAIN_INTERVAL_S - (now - _last_trigger_ts)) / 60)
        return {"status": "debounced", "retry_in_minutes": remaining_min}

    alert_names = [a["labels"]["alertname"] for a in retrain_worthy]
    return await trigger_full_pipeline(alert_names, now)


async def trigger_full_pipeline(alert_names: list, triggered_at: float) -> dict:
    global _last_trigger_ts
    conf = {
        "triggered_by": "prometheus_alert",
        "alert_names": alert_names,
        "triggered_at": datetime.now(timezone.utc).isoformat(),
    }

    # Step 1: trigger feature engineering
    resp1 = requests.post(
        f"{AIRFLOW_URL}/api/v1/dags/denttime_feature_engineering/dagRuns",
        json={"conf": conf},
        auth=(AIRFLOW_USER, AIRFLOW_PASS), timeout=10,
    )
    resp1.raise_for_status()
    fe_run_id = resp1.json()["dag_run_id"]
    log.info("Feature engineering DAG triggered: %s", fe_run_id)

    # Step 2: poll until feature engineering completes (max 10 min)
    for _ in range(60):
        time.sleep(10)
        state = requests.get(
            f"{AIRFLOW_URL}/api/v1/dags/denttime_feature_engineering/dagRuns/{fe_run_id}",
            auth=(AIRFLOW_USER, AIRFLOW_PASS), timeout=10,
        ).json().get("state")
        if state == "success":
            break
        if state == "failed":
            log.error("Feature engineering DAG failed — aborting retrain")
            return {"status": "error", "reason": "feature_engineering_failed", "fe_run_id": fe_run_id}

    # Step 3: trigger ML retrain
    resp2 = requests.post(
        f"{AIRFLOW_URL}/api/v1/dags/denttime_retrain/dagRuns",
        json={"conf": {**conf, "fe_run_id": fe_run_id}},
        auth=(AIRFLOW_USER, AIRFLOW_PASS), timeout=10,
    )
    resp2.raise_for_status()
    dag_run_id = resp2.json().get("dag_run_id")
    _last_trigger_ts = triggered_at
    log.info("ML retrain DAG triggered: %s", dag_run_id)
    return {"status": "triggered", "dag_run_id": dag_run_id, "fe_run_id": fe_run_id, "alerts": alert_names}
```

> **Note on file paths:** The trigger service container needs read access to the same filesystem paths as the Airflow container so that `RAW_DATA_PATH` and `FEATURES_PATH` resolve correctly. In the Docker Compose setup, mount the project root as a read-only volume in the `retrain_trigger` service: `- ../:/project:ro` and set `RAW_DATA_PATH=/project/data/raw/data.csv`, `FEATURES_PATH=/project/features/features_train.parquet`.

---

## Action Items

1. [ ] Replace the routing-table implementation in `retrain_trigger/main.py` with the data freshness gate above.
2. [ ] Mount the project root as a read-only volume in the `retrain_trigger` Docker service and set `RAW_DATA_PATH` / `FEATURES_PATH` env vars.
3. [ ] Add a human notification path (Alertmanager email/Line receiver, or a dedicated log alert) for the `no_new_data` skip case.
4. [ ] Test the gate: verify that triggering `/alert` with no new raw data returns `{"status": "skipped", "reason": "no_new_data"}`.
5. [ ] Test the full pipeline path: update `data/raw/data.csv` timestamp (`touch data/raw/data.csv`), fire a test alert, confirm Feature Engineering then ML Retrain both complete in Airflow.
6. [ ] Remove the now-incorrect `ML_ONLY_ALERTS` routing from any documentation or slide that references it.
