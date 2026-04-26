"""
DentTime retrain DAG — P3 Model Training & Evaluation

4 tasks สำหรับ retrain XGBoost model อัตโนมัติ
trigger ได้จาก Phu (P5) เมื่อตรวจพบ model drift
"""
import sys
from pathlib import Path

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.utils.dates import days_ago



# ---------------------------------------------------------------------------
# Path constants — เดียวกับ Sun
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path("/opt/airflow/project")
FEATURES     = PROJECT_ROOT / "features"
MODEL_DIR    = PROJECT_ROOT / "models"


# ---------------------------------------------------------------------------
# Task functions
# ---------------------------------------------------------------------------

def _task_load_features():
    """โหลด features จาก P2 ที่ผ่าน pipeline ของ Sun แล้ว"""
    import pandas as pd

    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    train_df = pd.read_parquet(FEATURES / "features_train.parquet")
    test_df  = pd.read_parquet(FEATURES / "features_test.parquet")

    if len(train_df) == 0:
        raise ValueError("features_train.parquet ว่างเปล่า — รัน feature_engineering DAG ก่อน")
    if len(test_df) == 0:
        raise ValueError("features_test.parquet ว่างเปล่า — รัน feature_engineering DAG ก่อน")

    print(f"Train: {len(train_df):,} แถว")
    print(f"Test:  {len(test_df):,} แถว")
    print(f"Columns: {train_df.columns.tolist()}")


def _task_train_model():
    """Train XGBoost model ด้วย features จาก P2"""
    import json
    import pandas as pd
    from xgboost import XGBClassifier
    from sklearn.preprocessing import LabelEncoder
    from sklearn.utils.class_weight import compute_sample_weight

    # Feature columns (ตัด scheduled_duration_min เพราะ leakage)
    FEATURE_COLS = [
        'treatment_class', 'composite_treatment_flag', 'has_tooth_no',
        'tooth_count', 'is_area_treatment', 'surface_count', 'total_amount',
        'has_notes', 'appt_day_of_week', 'appt_hour_bucket', 'is_first_case',
        'has_dentist_id', 'appointment_rank_in_day', 'clinic_median_duration',
        'clinic_pct_long', 'doctor_median_duration', 'doctor_pct_long'
    ]

    train_df = pd.read_parquet(FEATURES / "features_train.parquet")

    X_train = train_df[FEATURE_COLS]
    y_train = train_df['duration_class']

    # Label Encoder (6 class ไม่มี 75)
    le = LabelEncoder()
    le.fit([15, 30, 45, 60, 90, 105])
    y_train_enc = le.transform(y_train)

    # Sample weights ช่วย minority class
    sample_weights = compute_sample_weight(
        class_weight='balanced',
        y=y_train_enc
    )

    # Train
    model = XGBClassifier(
        n_estimators=300,
        max_depth=6,
        learning_rate=0.05,
        eval_metric='mlogloss',
        random_state=42,
        verbosity=0,
        n_jobs=-1
    )
    model.fit(X_train, y_train_enc, sample_weight=sample_weights, verbose=50)

    # Save model bundle
    import joblib
    model_bundle = {
        "model":          model,
        "label_encoder":  le,
        "feature_cols":   FEATURE_COLS,
        "index_to_class": {int(i): int(c) for i, c in enumerate(le.classes_)}
    }
    joblib.dump(model_bundle, MODEL_DIR / "model.joblib")
    print("✅ Saved: model.joblib")

    # Save feature columns
    with open(MODEL_DIR / "feature_columns.json", "w") as f:
        json.dump({"feature_cols": FEATURE_COLS}, f, indent=2)
    print("✅ Saved: feature_columns.json")


def _task_evaluate_model():
    """Evaluate โมเดลใหม่ เปรียบเทียบกับ baseline"""
    import json
    import numpy as np
    import pandas as pd
    import joblib
    from sklearn.metrics import f1_score, mean_absolute_error

    # โหลด model ใหม่
    bundle = joblib.load(MODEL_DIR / "model.joblib")
    model  = bundle["model"]
    le     = bundle["label_encoder"]
    feats  = bundle["feature_cols"]

    test_df    = pd.read_parquet(FEATURES / "features_test.parquet")
    X_test     = test_df[feats]
    y_test_min = test_df['duration_class'].values

    y_pred_enc = model.predict(X_test)
    y_pred     = le.inverse_transform(y_pred_enc)

    macro_f1       = f1_score(y_test_min, y_pred, average='macro')
    mae            = mean_absolute_error(y_test_min, y_pred)
    under_est_rate = float(np.mean(y_pred < y_test_min))

    print(f"Macro F1-score       : {macro_f1:.4f}")
    print(f"MAE (minutes)        : {mae:.2f}")
    print(f"Under-estimation Rate: {under_est_rate:.4f}")

    # โหลด baseline metrics เพื่อเปรียบเทียบ
    baseline_path = MODEL_DIR / "baseline_metrics.json"
    if baseline_path.exists():
        with open(baseline_path) as f:
            baseline = json.load(f)

        baseline_f1 = baseline.get("macro_f1", 0)
        f1_drop     = baseline_f1 - macro_f1

        print(f"\nBaseline F1  : {baseline_f1:.4f}")
        print(f"New F1       : {macro_f1:.4f}")
        print(f"F1 Drop      : {f1_drop:.4f}")

        if f1_drop > 0.05:
            raise ValueError(f"โมเดลใหม่แย่กว่า baseline เกิน 0.05 (drop={f1_drop:.4f}) — ไม่ promote")

    # Save metrics ใหม่
    new_metrics = {
        "model_version":         "denttime_xgb_retrain",
        "macro_f1":              round(float(macro_f1), 4),
        "mae_minutes":           round(float(mae), 2),
        "under_estimation_rate": round(float(under_est_rate), 4),
        "class_labels":          [15, 30, 45, 60, 90, 105],
        "degradation_thresholds": {
            "macro_f1_drop":      0.05,
            "under_est_rate_max": 0.20,
            "mae_increase_max":   5
        }
    }
    with open(MODEL_DIR / "baseline_metrics.json", "w") as f:
        json.dump(new_metrics, f, indent=2)
    print("✅ Saved: baseline_metrics.json")


def _task_export_artifacts():
    """Export ไฟล์ทั้งหมดให้ Phu (P5) ใช้ต่อ"""
    import pandas as pd
    import json
    import joblib

    bundle = joblib.load(MODEL_DIR / "model.joblib")
    feats  = bundle["feature_cols"]

    # reference_features.parquet สำหรับ drift monitoring
    train_df = pd.read_parquet(FEATURES / "features_train.parquet")
    train_df[feats].to_parquet(MODEL_DIR / "reference_features.parquet", index=False)
    print("✅ Saved: reference_features.parquet")

    # smoke_test_inputs.json
    test_df = pd.read_parquet(FEATURES / "features_test.parquet")
    sample  = test_df.head(5)[feats + ['duration_class']].copy()
    smoke_tests = []
    for i, row in sample.iterrows():
        smoke_tests.append({
            "case_id": f"smoke_00{len(smoke_tests)+1}",
            "input":   {col: float(row[col]) for col in feats},
            "expected_duration_class": int(row['duration_class'])
        })
    with open(MODEL_DIR / "smoke_test_inputs.json", "w", encoding="utf-8") as f:
        json.dump(smoke_tests, f, indent=2, ensure_ascii=False)
    print("✅ Saved: smoke_test_inputs.json")

    print("\n=== Export เสร็จแล้ว ===")
    for p in MODEL_DIR.iterdir():
        print(f"  {p.name:40s} {p.stat().st_size:>10,} bytes")


# ---------------------------------------------------------------------------
# DAG definition
# ---------------------------------------------------------------------------
with DAG(
    dag_id="denttime_retrain",
    schedule=None,       # trigger manually หรือจาก Phu
    start_date=days_ago(1),
    catchup=False,
    tags=["model-training", "retrain"],
) as dag:

    load_features = PythonOperator(
        task_id="task_load_features",
        python_callable=_task_load_features,
    )
    train_model = PythonOperator(
        task_id="task_train_model",
        python_callable=_task_train_model,
    )
    evaluate_model = PythonOperator(
        task_id="task_evaluate_model",
        python_callable=_task_evaluate_model,
    )
    export_artifacts = PythonOperator(
        task_id="task_export_artifacts",
        python_callable=_task_export_artifacts,
    )

    # Dependency wiring
    load_features >> train_model >> evaluate_model >> export_artifacts