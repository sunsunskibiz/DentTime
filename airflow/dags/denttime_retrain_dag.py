"""
DentTime retrain DAG — P3 Model Training & Evaluation
พร้อม MLflow Tracking + Feature Ranking

Pipeline (5 tasks):
  load_features → train_model → rank_features → evaluate_model → export_artifacts
"""
import os
from pathlib import Path

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.utils.dates import days_ago

PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT", "/opt/airflow/project"))
FEATURES     = Path(os.getenv("FEATURES_DIR", str(PROJECT_ROOT / "features")))
MODEL_DIR    = Path(os.getenv("MODEL_DIR", str(PROJECT_ROOT / "models")))

MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "http://mlflow:5000")
MLFLOW_EXPERIMENT   = "DentTime_Duration_Prediction"

ALL_FEATURE_COLS = [
    'treatment_class', 'composite_treatment_flag', 'has_tooth_no',
    'tooth_count', 'is_area_treatment', 'surface_count', 'total_amount',
    'has_notes', 'appt_day_of_week', 'appt_hour_bucket', 'is_first_case',
    'has_dentist_id', 'appointment_rank_in_day', 'clinic_median_duration',
    'clinic_pct_long', 'doctor_median_duration', 'doctor_pct_long'
]
CLASS_LABELS = [15, 30, 45, 60, 90, 105]


def _train_xgb(X_train, y_enc, weights, params):
    from xgboost import XGBClassifier
    m = XGBClassifier(**params)
    m.fit(X_train, y_enc, sample_weight=weights, verbose=False)
    return m


def _score(model, le, X_test, y_test):
    import numpy as np
    from sklearn.metrics import f1_score, mean_absolute_error
    y_pred = le.inverse_transform(model.predict(X_test))
    return {
        "macro_f1":    float(f1_score(y_test, y_pred, average='macro')),
        "weighted_f1": float(f1_score(y_test, y_pred, average='weighted')),
        "mae":         float(mean_absolute_error(y_test, y_pred)),
        "accuracy":    float(np.mean(y_pred == y_test)),
    }


# ---------------------------------------------------------------------------
# Task helpers
# ---------------------------------------------------------------------------

def _assert_feature_files_exist():
    missing = []
    for file_name in ("features_train.parquet", "features_test.parquet"):
        path = FEATURES / file_name
        if not path.exists():
            missing.append(path)
    if missing:
        raise FileNotFoundError(
            "Missing feature files for retrain. Run the feature engineering DAG first: "
            "denttime_feature_engineering, or generate features with feature_engineering.py. "
            f"Missing: {', '.join(str(p) for p in missing)}"
        )


# ---------------------------------------------------------------------------
# Task 1: Load & validate features
# ---------------------------------------------------------------------------
def _task_load_features():
    import pandas as pd
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    _assert_feature_files_exist()
    train_df = pd.read_parquet(FEATURES / "features_train.parquet")
    test_df  = pd.read_parquet(FEATURES / "features_test.parquet")
    if len(train_df) == 0:
        raise ValueError("features_train.parquet ว่างเปล่า — รัน feature_engineering DAG ก่อน")
    if len(test_df) == 0:
        raise ValueError("features_test.parquet ว่างเปล่า — รัน feature_engineering DAG ก่อน")
    print(f"Train: {len(train_df):,} | Test: {len(test_df):,}")


# ---------------------------------------------------------------------------
# Task 2: Train full model
# ---------------------------------------------------------------------------
def _task_train_model(**context):
    import json, joblib, mlflow, mlflow.xgboost, pandas as pd
    from datetime import datetime
    from sklearn.preprocessing import LabelEncoder
    from sklearn.utils.class_weight import compute_sample_weight

    train_df = pd.read_parquet(FEATURES / "features_train.parquet")
    X_train  = train_df[ALL_FEATURE_COLS]
    y_train  = train_df['duration_class']
    le = LabelEncoder(); le.fit(CLASS_LABELS)
    y_enc   = le.transform(y_train)
    weights = compute_sample_weight('balanced', y=y_enc)

    params = dict(n_estimators=300, max_depth=6, learning_rate=0.05,
                  eval_metric="mlogloss", random_state=42, verbosity=0, n_jobs=-1)

    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    mlflow.set_experiment(MLFLOW_EXPERIMENT)

    with mlflow.start_run(run_name=f"XGB_full_{datetime.now().strftime('%Y%m%d_%H%M%S')}") as run:
        mlflow.log_params(params)
        mlflow.log_param("feature_set",  "full")
        mlflow.log_param("num_features", len(ALL_FEATURE_COLS))
        mlflow.log_param("train_rows",   len(train_df))
        model = _train_xgb(X_train, y_enc, weights, params)
        mlflow.xgboost.log_model(model, "xgb_model",
                                 registered_model_name="denttime_duration_classifier")
        run_id = run.info.run_id

    context['ti'].xcom_push(key='mlflow_run_id', value=run_id)
    context['ti'].xcom_push(key='xgb_params',    value=params)

    bundle = dict(model=model, label_encoder=le, feature_cols=ALL_FEATURE_COLS,
                  index_to_class={int(i): int(c) for i, c in enumerate(le.classes_)},
                  mlflow_run_id=run_id, feature_set="full")
    joblib.dump(bundle, MODEL_DIR / "model.joblib")
    with open(MODEL_DIR / "feature_columns.json", "w") as f:
        json.dump({"feature_cols": ALL_FEATURE_COLS}, f, indent=2)
    print(f"Full model trained | run_id: {run_id}")


# ---------------------------------------------------------------------------
# Task 3: Feature Ranking + Pruning
# ---------------------------------------------------------------------------
def _task_rank_features(**context):
    import json, joblib, numpy as np, pandas as pd, mlflow
    from sklearn.inspection import permutation_importance
    from sklearn.utils.class_weight import compute_sample_weight

    run_id     = context['ti'].xcom_pull(key='mlflow_run_id', task_ids='task_train_model')
    xgb_params = context['ti'].xcom_pull(key='xgb_params',   task_ids='task_train_model')

    bundle   = joblib.load(MODEL_DIR / "model.joblib")
    model, le, feats = bundle["model"], bundle["label_encoder"], bundle["feature_cols"]

    train_df = pd.read_parquet(FEATURES / "features_train.parquet")
    test_df  = pd.read_parquet(FEATURES / "features_test.parquet")
    X_train  = train_df[feats];  y_train = train_df['duration_class']
    X_test   = test_df[feats];   y_test  = test_df['duration_class'].values
    y_enc    = le.transform(y_train)

    # --- A) XGBoost built-in importance ---
    def _norm(d, keys):
        v = np.array([d.get(k, 0) for k in keys], dtype=float)
        s = v.sum(); return v / s if s > 0 else v

    gain   = _norm(model.get_booster().get_score(importance_type='gain'),   feats)
    weight = _norm(model.get_booster().get_score(importance_type='weight'), feats)
    cover  = _norm(model.get_booster().get_score(importance_type='cover'),  feats)
    composite = (gain + weight + cover) / 3

    # --- B) Permutation Importance ---
    print("Computing Permutation Importance ...")
    y_test_enc = le.transform(pd.Series(y_test).clip(CLASS_LABELS[0], CLASS_LABELS[-1]))
    perm = permutation_importance(model, X_test, y_test_enc,
                                  n_repeats=10, random_state=42, n_jobs=-1, scoring="accuracy")

    # --- รวม และ normalize ---
    p_min, p_max = perm.importances_mean.min(), perm.importances_mean.max()
    perm_norm = (perm.importances_mean - p_min) / (p_max - p_min + 1e-9)

    # final_score = 60% XGB + 40% Perm
    final_score = 0.6 * composite + 0.4 * perm_norm

    rank_df = pd.DataFrame({
        "feature":     feats,
        "gain":        gain,
        "weight":      weight,
        "cover":       cover,
        "xgb_comp":    composite,
        "perm_mean":   perm.importances_mean,
        "perm_std":    perm.importances_std,
        "final_score": final_score,
    }).sort_values("final_score", ascending=False).reset_index(drop=True)
    rank_df["rank"] = rank_df.index + 1

    print("\n=== Feature Ranking ===")
    print(rank_df[["rank","feature","xgb_comp","perm_mean","final_score"]].to_string(index=False))

    # --- threshold: mean - 0.5*std ---
    mu, sigma = rank_df["final_score"].mean(), rank_df["final_score"].std()
    threshold = max(mu - 0.5 * sigma, 0.01)
    kept    = rank_df[rank_df["final_score"] >= threshold]["feature"].tolist()
    dropped = rank_df[rank_df["final_score"] <  threshold]["feature"].tolist()
    print(f"\nThreshold={threshold:.4f} | Kept={len(kept)} | Dropped={len(dropped)}: {dropped}")

    # --- Retrain pruned ---
    w_pruned     = compute_sample_weight('balanced', y=y_enc)
    model_pruned = _train_xgb(X_train[kept], y_enc, w_pruned, xgb_params)

    full_sc   = _score(model,        le, X_test[feats], y_test)
    pruned_sc = _score(model_pruned, le, X_test[kept],  y_test)
    print(f"\nFull   macro_f1={full_sc['macro_f1']:.4f} mae={full_sc['mae']:.2f}")
    print(f"Pruned macro_f1={pruned_sc['macro_f1']:.4f} mae={pruned_sc['mae']:.2f}")

    use_pruned  = pruned_sc["macro_f1"] >= full_sc["macro_f1"] - 0.01
    final_model = model_pruned if use_pruned else model
    final_feats = kept         if use_pruned else feats
    final_set   = "pruned"     if use_pruned else "full"

    # --- Log to MLflow ---
    rank_path = MODEL_DIR / "feature_ranking.csv"
    rank_df.to_csv(rank_path, index=False)

    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    with mlflow.start_run(run_id=run_id):
        for _, row in rank_df.iterrows():
            fn = row["feature"]
            mlflow.log_metric(f"fi_gain_{fn}",  float(row["gain"]))
            mlflow.log_metric(f"fi_perm_{fn}",  float(row["perm_mean"]))
            mlflow.log_metric(f"fi_score_{fn}", float(row["final_score"]))
        mlflow.log_metric("fi_threshold",        threshold)
        mlflow.log_metric("features_kept",        len(kept))
        mlflow.log_metric("features_dropped",     len(dropped))
        mlflow.log_metric("full_macro_f1",        full_sc["macro_f1"])
        mlflow.log_metric("pruned_macro_f1",      pruned_sc["macro_f1"])
        mlflow.log_param("features_dropped",      str(dropped))
        mlflow.log_param("use_pruned_model",      str(use_pruned))
        mlflow.log_artifact(str(rank_path),       artifact_path="feature_ranking")
        mlflow.set_tag("feature_ranking_done",    "true")

    # --- Save bundle ---
    joblib.dump(dict(model=final_model, label_encoder=le, feature_cols=final_feats,
                     index_to_class={int(i): int(c) for i, c in enumerate(le.classes_)},
                     mlflow_run_id=run_id, feature_set=final_set, dropped_features=dropped),
                MODEL_DIR / "model.joblib")
    with open(MODEL_DIR / "feature_columns.json", "w") as f:
        json.dump({"feature_cols": final_feats, "dropped": dropped}, f, indent=2)

    context['ti'].xcom_push(key='final_feature_set', value=final_set)
    context['ti'].xcom_push(key='kept_features',     value=final_feats)
    context['ti'].xcom_push(key='dropped_features',  value=dropped)
    print(f"\nFeature ranking done → [{final_set}] model ({len(final_feats)} features)")


# ---------------------------------------------------------------------------
# Task 4: Evaluate
# ---------------------------------------------------------------------------
def _task_evaluate_model(**context):
    import json, numpy as np, pandas as pd, joblib, mlflow
    from sklearn.metrics import f1_score, mean_absolute_error, classification_report

    run_id     = context['ti'].xcom_pull(key='mlflow_run_id',     task_ids='task_train_model')
    final_set  = context['ti'].xcom_pull(key='final_feature_set', task_ids='task_rank_features')
    dropped    = context['ti'].xcom_pull(key='dropped_features',  task_ids='task_rank_features')

    bundle = joblib.load(MODEL_DIR / "model.joblib")
    model, le, feats = bundle["model"], bundle["label_encoder"], bundle["feature_cols"]

    test_df = pd.read_parquet(FEATURES / "features_test.parquet")
    X_test  = test_df[feats]; y_test = test_df['duration_class'].values

    y_pred = le.inverse_transform(model.predict(X_test))

    macro_f1    = float(f1_score(y_test, y_pred, average='macro'))
    weighted_f1 = float(f1_score(y_test, y_pred, average='weighted'))
    mae         = float(mean_absolute_error(y_test, y_pred))
    accuracy    = float(np.mean(y_pred == y_test))
    under_rate  = float(np.mean(y_pred < y_test))

    per_class = {}
    for cls in CLASS_LABELS:
        mask = y_test == cls
        if mask.sum() > 0:
            per_class[f"acc_class_{cls}"] = float(np.mean(y_pred[mask] == cls))

    print(f"Model set: {final_set} ({len(feats)} features) | Dropped: {dropped}")
    print(f"Macro F1={macro_f1:.4f} | MAE={mae:.2f} | Acc={accuracy:.4f}")

    f1_drop = 0.0
    baseline_path = MODEL_DIR / "baseline_metrics.json"
    if baseline_path.exists():
        with open(baseline_path) as f:
            baseline = json.load(f)
        f1_drop = baseline.get("macro_f1", 0) - macro_f1
        if f1_drop > 0.05:
            raise ValueError(f"โมเดลแย่กว่า baseline เกิน 0.05 (drop={f1_drop:.4f})")

    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    with mlflow.start_run(run_id=run_id):
        mlflow.log_metrics({"eval_macro_f1": macro_f1, "eval_weighted_f1": weighted_f1,
                            "eval_mae": mae, "eval_accuracy": accuracy,
                            "eval_under_rate": under_rate, "eval_f1_drop": f1_drop,
                            **{f"eval_{k}": v for k, v in per_class.items()}})
        report_txt  = classification_report(y_test, y_pred)
        report_path = MODEL_DIR / "classification_report.txt"
        with open(report_path, "w") as f:
            f.write(f"Feature set: {final_set} ({len(feats)} features)\nDropped: {dropped}\n\n")
            f.write(report_txt)
        mlflow.log_artifact(str(report_path), artifact_path="evaluation")
        mlflow.set_tag("promoted", "true" if f1_drop <= 0.05 else "false")

    with open(MODEL_DIR / "baseline_metrics.json", "w") as f:
        json.dump({"macro_f1": round(macro_f1,4), "weighted_f1": round(weighted_f1,4),
                   "mae": round(mae,2), "feature_set": final_set,
                   "features_used": feats, "features_dropped": dropped,
                   "mlflow_run_id": run_id}, f, indent=2)
    print("Evaluation done")


# ---------------------------------------------------------------------------
# Task 5: Export artifacts
# ---------------------------------------------------------------------------
def _task_export_artifacts(**context):
    import json, mlflow, pandas as pd, joblib
    from mlflow.tracking import MlflowClient

    run_id    = context['ti'].xcom_pull(key='mlflow_run_id',     task_ids='task_train_model')
    final_set = context['ti'].xcom_pull(key='final_feature_set', task_ids='task_rank_features')

    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    client = MlflowClient()
    bundle = joblib.load(MODEL_DIR / "model.joblib")
    feats  = bundle["feature_cols"]

    train_df = pd.read_parquet(FEATURES / "features_train.parquet")
    ref_path = MODEL_DIR / "reference_features.parquet"
    train_df[feats].to_parquet(ref_path, index=False)

    test_df = pd.read_parquet(FEATURES / "features_test.parquet")
    smoke   = []
    for _, row in test_df.head(5).iterrows():
        smoke.append({"case_id": f"smoke_{len(smoke)+1:03d}",
                      "input": {c: float(row[c]) for c in feats},
                      "expected": int(row['duration_class'])})
    smoke_path = MODEL_DIR / "smoke_test_inputs.json"
    with open(smoke_path, "w") as f:
        json.dump(smoke, f, indent=2)

    with mlflow.start_run(run_id=run_id):
        for p in [ref_path, smoke_path,
                  MODEL_DIR/"baseline_metrics.json",
                  MODEL_DIR/"feature_ranking.csv",
                  MODEL_DIR/"feature_columns.json"]:
            mlflow.log_artifact(str(p), artifact_path="artifacts")
        mlflow.set_tag("final_model_set", final_set)

    try:
        versions = client.get_latest_versions("denttime_duration_classifier", stages=["None"])
        if versions:
            v = versions[-1].version
            client.transition_model_version_stage(
                name="denttime_duration_classifier", version=v,
                stage="Staging", archive_existing_versions=False)
            print(f"Model v{v} [{final_set}] → Staging")
    except Exception as e:
        print(f"Registry skip: {e}")

    print("Export done")


# ---------------------------------------------------------------------------
# DAG
# ---------------------------------------------------------------------------
with DAG(
    dag_id="denttime_retrain",
    schedule=None,
    start_date=days_ago(1),
    catchup=False,
    tags=["model-training", "retrain", "mlflow", "feature-ranking"],
    doc_md="""
## DentTime Retrain DAG

```
load_features → train_model → rank_features → evaluate_model → export_artifacts
```

### Feature Ranking (Task 3)
- XGBoost Importance: gain / weight / cover
- Permutation Importance: 10 repeats บน test set
- Final Score: 60% XGB + 40% Permutation
- ตัด feature ที่ score < (mean − 0.5·std)
- Retrain pruned model → เลือก full หรือ pruned (threshold: −0.01 F1)
    """,
) as dag:

    t1 = PythonOperator(task_id="task_load_features",  python_callable=_task_load_features)
    t2 = PythonOperator(task_id="task_train_model",    python_callable=_task_train_model,    provide_context=True)
    t3 = PythonOperator(task_id="task_rank_features",  python_callable=_task_rank_features,  provide_context=True)
    t4 = PythonOperator(task_id="task_evaluate_model", python_callable=_task_evaluate_model, provide_context=True)
    t5 = PythonOperator(task_id="task_export_artifacts",python_callable=_task_export_artifacts,provide_context=True)

    t1 >> t2 >> t3 >> t4 >> t5