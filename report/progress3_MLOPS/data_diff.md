Based on the flowchart, here's a complete system design for data drift detection in DentTime, grounded in what's already defined in your pipeline.

---

## Requirements

**Functional**
- Detect when the statistical properties of incoming appointment data diverge from the training distribution
- Detect when prediction output distribution shifts across the 6 treatment-duration classes (15–105 min)
- Detect when model performance degrades on real outcomes (concept drift)
- Trigger M7 Retrain flag automatically when thresholds are breached

**Non-functional**
- Latency: drift scores computed within 1 hour of the rolling window closing (not real-time)
- No ground truth required for feature/output drift (ground truth only available in M_PERF after appointment completion)
- Stack constraint: Prometheus + Grafana already defined in the flowchart

---

## Four Drift Signal Types in DentTime

Your pipeline naturally produces four distinct drift signals, each from a different data source:

```
┌─────────────────────────────────────────────────────────────────────┐
│  SIGNAL 1: Data Quality Drift      ← D4 Great Expectations          │
│  SIGNAL 2: Feature Drift (PSI)     ← D7 Feature Store vs. live feed │
│  SIGNAL 3: Output Distribution     ← Prediction log (DEP4 → M_DRIFT)│
│  SIGNAL 4: Concept Drift (M_PERF)  ← JOIN actuals + predictions      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## High-Level Data Flow

```
CMS Database (D1)
       │
       ▼
Live Appointment Feed ──────────────────────────────────┐
       │                                                 │
       ▼                                                 ▼
D4: Great Expectations ──► SIGNAL 1              DEP4: FastAPI
  (schema, null rate,         Data Quality         logs prediction
   value range checks)        Drift Score          (appointment_id
                                                    + predicted_slot)
                                                         │
D7: Feature Store                                        ▼
  (Training Reference     SIGNAL 2          M_DRIFT: Distribution
   Distribution)  ──────► Feature Drift ──► Shift Detector
                           PSI per feature              │
                           MMD multivariate             ▼
                                              SIGNAL 3: Output Drift
                                              (class 15/30/45/60/
                                               75/90 min ratios)
                                                         │
M_PERF: JOIN actuals ──────────────────────► SIGNAL 4: Concept Drift
  (receipt_time -                             F1, MAE, Under-est Rate
   check_in_time)                                        │
                                                         ▼
                                              M7: Retrain Trigger
                                              (threshold logic)
                                                         │
                                                         ▼
                                              Prometheus Gauges
                                              Grafana Alerts
```

---

## Signal 1 — Data Quality Drift (D4, Great Expectations)

**What it checks:** Schema integrity, null rates, value distributions of raw CMS input before it enters the pipeline.

**How to detect:**
- Define expectations on the training baseline: e.g., `treatment_type` must have ≤ 2% null rate, `duration_minutes` must be in [5, 180]
- Run Great Expectations on every new batch arriving at D2
- Export the validation result JSON to a Prometheus gauge via a PythonOperator in Airflow

**Threshold:** Any expectation failure → flag immediately. Null rate increase > 5pp from baseline → soft warning.

**Why it matters for DentTime:** If the CMS schema changes (new treatment codes, renamed columns), this is the earliest possible catch — before bad data contaminates the feature store.

---

## Signal 2 — Feature Drift (PSI + MMD, M_DRIFT)

**What it checks:** Whether the statistical distribution of individual features and their joint distribution have shifted compared to the training set.

**Reference dataset:** The Feature Store snapshot (D7) from the most recent training run, stored as a DVC-versioned Parquet file. This becomes your "baseline."

**Per-feature univariate drift via PSI:**

PSI is already specified in your flowchart (threshold > 0.25). Here's the exact computation:

```
PSI = Σ (Actual% - Expected%) × ln(Actual% / Expected%)

Buckets: deciles from training distribution
Window:  rolling 7-day of live appointments (as specified)
```

Apply PSI to the structured features from D6:
- `treatment_class` (categorical — use frequency ratio instead of deciles)
- `tooth_count`
- `time_of_day` (morning / afternoon / evening bucket)
- `is_first_case`
- `doctor_profile_stats` (mean duration per doctor — watch for new doctors)

**Multivariate drift via MMD (Maximum Mean Discrepancy):**

Captures joint distribution shifts that per-feature PSI misses (e.g., a new pattern: long procedures now predominantly in morning slots).

```
MMD² = E[k(x,x')] - 2E[k(x,y)] + E[k(y,y')]
where k = RBF kernel, x = training sample, y = live window sample
```

Use a rolling 30-day window vs. training set. Flag if MMD² > calibrated threshold (set during initial deployment using permutation test).

**Implementation:** Run as a weekly Airflow DAG task (can extend M_PERF DAG). Load reference from DVC, load live window from prediction_log joined to CMS.

---

## Signal 3 — Output Distribution Drift (M_DRIFT)

**What it checks:** Whether the model's predicted class distribution has shifted, independent of ground truth.

**How to detect:** Maintain a rolling distribution of predicted slots over 7 days and compare to the training-set label distribution.

```
Class          Training%   Live 7-day%   Δ
─────────────────────────────────────────
15 min           18%          14%       -4pp
30 min           24%          20%       -4pp
45 min           21%          19%       -2pp
60 min           17%          22%       +5pp  ← watch this
75 min           12%          15%       +3pp
90 min            8%          10%       +2pp
```

If the distribution of longer classes (60–90 min) grows significantly, this directly signals **Under-estimation Risk** — the key business metric for DentTime.

**Threshold:** Chi-squared test on class frequency counts, p < 0.05 → alert. Or simpler: if any class shifts > 8pp from training baseline.

**Why this is valuable without ground truth:** You get a leading indicator of problems within days, whereas M_PERF (concept drift) requires waiting for appointment completion data which can lag by weeks.

---

## Signal 4 — Concept Drift (M_PERF, performance degradation)

**What it checks:** Whether the model's actual accuracy on completed appointments has degraded.

This is already partially defined in your flowchart. The JOIN logic:

```sql
SELECT
    p.appointment_id,
    p.predicted_slot,
    CASE
        WHEN actual_duration <= 15  THEN '15min'
        WHEN actual_duration <= 30  THEN '30min'
        WHEN actual_duration <= 45  THEN '45min'
        WHEN actual_duration <= 60  THEN '60min'
        WHEN actual_duration <= 75  THEN '75min'
        ELSE '90min'
    END AS actual_slot,
    actual_duration,
    p.predicted_duration_minutes
FROM prediction_log p
JOIN cms_appointments c
    ON p.appointment_id = c.appointment_id
WHERE c.receipt_time IS NOT NULL  -- completed appointments only
  AND p.created_at >= NOW() - INTERVAL '7 days'
```

**Metrics to push to Prometheus:**
- `denttime_macro_f1_gauge` — overall multi-class accuracy
- `denttime_under_estimation_rate_gauge` — predicted short, actually long (the key business risk)
- `denttime_mae_minutes_gauge` — mean absolute error in minutes

**Under-estimation Rate definition** (the one that matters most for clinic risk):

```
Under-estimation Rate =
  COUNT(predicted_slot < actual_slot AND actual_slot IN ['60min','75min','90min'])
  ÷ COUNT(actual_slot IN ['60min','75min','90min'])
```

---

## M7 — Retrain Trigger Logic

All four signals feed into M7. The trigger is a priority-ordered OR condition:

```
TRIGGER RETRAIN if ANY of:

  [CRITICAL]  Signal 1: D4 validation failure rate > 10% in a week
  [CRITICAL]  Signal 4: Under-estimation Rate for long procedures > 30%
  [HIGH]      Signal 4: Macro F1 drops > 5pp from champion baseline
  [HIGH]      Signal 3: Output class shift > 8pp for ≥ 2 classes
  [MEDIUM]    Signal 2: PSI > 0.25 on ≥ 3 features simultaneously
  [MEDIUM]    Signal 2: MMD² exceeds permutation-calibrated threshold
  [LOW]       Signal 6: User override frequency > 20% (M6, indirect)

  [SCHEDULED] Monthly retrain regardless of drift (calendar-based safety net)
```

The "monthly retrain regardless" is important — it ensures the model stays current even if drift signals are subtle but cumulative.

---

## Component Summary

| Component | Where in Flowchart | Tool | Cadence |
|---|---|---|---|
| Data quality check | D4 (Future → implement now) | Great Expectations | Per batch |
| Feature PSI | M_DRIFT | Evidently AI or custom Pandas | Rolling 7-day |
| MMD multivariate | M_DRIFT | scikit-learn / custom | Monthly |
| Output distribution | M_DRIFT | Pandas + Prometheus | Daily |
| Concept drift (F1 / MAE / Under-est) | M_PERF | Airflow DAG + Prometheus | Weekly |
| Alerting & dashboard | M7 → Grafana | Prometheus + Grafana Alertmanager | Real-time |

---

## Key Trade-off to Call Out in Your Presentation

**PSI vs. KS test vs. MMD:** PSI is interpretable and already chosen in your design. KS test is more statistically rigorous for continuous features but harder to explain to a clinic audience. MMD captures multivariate shifts but is a black box. The combination of PSI (per-feature, explainable) + MMD (joint, safety net) in your flowchart is the right call — it balances explainability with coverage.

**Ground-truth lag:** Signals 1–3 are available immediately; Signal 4 (concept drift) lags by however long it takes appointments to complete and receipts to be recorded in CMS. For a dental clinic, this is typically same-day, so weekly M_PERF runs are sufficient — you're not operating at millisecond latency.