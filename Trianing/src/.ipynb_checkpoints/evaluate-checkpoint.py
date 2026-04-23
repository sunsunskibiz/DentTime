import json
import numpy as np
import pandas as pd
from sklearn.metrics import f1_score, mean_absolute_error, classification_report

from preprocess import FEATURE_COLS


def evaluate_model(model, le, X_test, y_test):
    """คำนวณ metrics ทั้งหมดบน Test Set"""
    y_pred_enc = model.predict(X_test)
    y_pred     = le.inverse_transform(y_pred_enc)
    y_test_min = y_test.values

    macro_f1       = f1_score(y_test_min, y_pred, average='macro')
    mae            = mean_absolute_error(y_test_min, y_pred)
    under_est_rate = np.mean(y_pred < y_test_min)

    print("=== ผลการ Evaluate บน Test Set ===")
    print(f"Macro F1-score       : {macro_f1:.4f}")
    print(f"MAE (minutes)        : {mae:.2f}")
    print(f"Under-estimation Rate: {under_est_rate:.4f}")
    print("\n=== Classification Report ===")
    print(classification_report(y_test_min, y_pred))

    return y_pred, macro_f1, mae, under_est_rate


def save_baseline_metrics(macro_f1, mae, under_est_rate,
                          output_path='outputs/baseline_metrics.json'):
    """Export baseline metrics สำหรับ Phu ใช้เปรียบเทียบ drift"""
    baseline = {
        "model_version":   "denttime_xgb_v1.0_202604",
        "eval_period":     "April 16-30, 2025 (Test Set)",
        "macro_f1":        round(float(macro_f1), 4),
        "mae_minutes":     round(float(mae), 2),
        "under_estimation_rate": round(float(under_est_rate), 4),
        "class_labels":    [15, 30, 45, 60, 75, 90, 105],
        "degradation_thresholds": {
            "macro_f1_drop":      0.05,
            "under_est_rate_max": 0.55,
            "mae_increase_max":   10
        }
    }
    with open(output_path, 'w') as f:
        json.dump(baseline, f, indent=2)
    print(f"✅ Saved: {output_path}")


def save_feature_columns(output_path='outputs/feature_columns.json'):
    """Export รายชื่อ features ที่ใช้จริง"""
    with open(output_path, 'w') as f:
        json.dump({"feature_cols": FEATURE_COLS}, f, indent=2)
    print(f"✅ Saved: {output_path}")


def save_reference_features(train_df,
                            output_path='outputs/reference_features.parquet'):
    """Export Train set สำหรับใช้เป็น baseline drift reference"""
    train_df[FEATURE_COLS].to_parquet(output_path, index=False)
    print(f"✅ Saved: {output_path}")


def save_smoke_tests(test_df, output_path='outputs/smoke_test_inputs.json'):
    """Export ตัวอย่าง input 5 เคสสำหรับ smoke test"""
    import json
    sample = test_df.head(5)[FEATURE_COLS + ['duration_class']].copy()
    smoke_tests = []
    for i, row in sample.iterrows():
        smoke_tests.append({
            "case_id": f"smoke_00{len(smoke_tests)+1}",
            "input":   {col: float(row[col]) for col in FEATURE_COLS},
            "expected_duration_class": int(row['duration_class'])
        })
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(smoke_tests, f, indent=2, ensure_ascii=False)
    print(f"✅ Saved: {output_path}")