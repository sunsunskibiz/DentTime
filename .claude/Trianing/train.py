import pandas as pd
import os
import numpy as np
import mlflow
import mlflow.sklearn
import mlflow.xgboost
from xgboost import XGBClassifier
from sklearn.metrics import f1_score, mean_absolute_error, classification_report
from sklearn.preprocessing import LabelEncoder
from sklearn.utils.class_weight import compute_sample_weight
from datetime import datetime

# --- 1. SET FEATURES ---
FEATURE_COLS_V2 = [
    'treatment_class', 'composite_treatment_flag', 'has_tooth_no',
    'tooth_count', 'is_area_treatment', 'surface_count', 'total_amount',
    'has_notes', 'appt_day_of_week', 'appt_hour_bucket', 'is_first_case',
    'has_dentist_id', 'appointment_rank_in_day', 'clinic_median_duration',
    'clinic_pct_long', 'doctor_median_duration', 'doctor_pct_long'
]

# --- 2. LOAD DATA ---
current_dir = os.path.dirname(os.path.abspath(__file__))
train_path = os.path.abspath(os.path.join(current_dir, 'data', 'features_train.parquet'))
test_path = os.path.abspath(os.path.join(current_dir, 'data', 'features_test.parquet'))

print(f"Searching for data at: {train_path}")
train_df = pd.read_parquet(train_path)
test_df = pd.read_parquet(test_path)

X_train = train_df[FEATURE_COLS_V2]
y_train = train_df['duration_class']
X_test  = test_df[FEATURE_COLS_V2]
y_test  = test_df['duration_class']

# --- 3. PREPARE LABELS & WEIGHTS ---
le = LabelEncoder()
le.fit([15, 30, 45, 60, 90, 105])
y_train_enc = le.transform(y_train)
y_test_enc  = le.transform(y_test)

sample_weights = compute_sample_weight(class_weight='balanced', y=y_train_enc)

# --- 4. MLFLOW SETUP ---
mlflow.set_experiment("DentTime_Duration_Prediction")
run_name = f"XGB_v2.0_Balanced_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

with mlflow.start_run(run_name=run_name):
    # Log Params
    params = {
        "n_estimators": 300,
        "max_depth": 6,
        "learning_rate": 0.05,
        "eval_metric": "mlogloss",
        "random_state": 42
    }
    mlflow.log_params(params)
    mlflow.log_param("num_features", len(FEATURE_COLS_V2))

    # --- 5. TRAINING ---
    print(f"Start Training: {run_name}")
    model = XGBClassifier(**params)
    model.fit(
        X_train, y_train_enc,
        sample_weight=sample_weights,
        eval_set=[(X_test, y_test_enc)],
        verbose=False
    )

    # --- 6. EVALUATION ---
    y_pred_enc = model.predict(X_test)
    y_pred = le.inverse_transform(y_pred_enc)
    y_actual = y_test.values
    
    # คำนวณ Metrics ต่างๆ
    accuracy = np.mean(y_pred == y_actual)
    macro_f1 = f1_score(y_actual, y_pred, average='macro')
    weighted_f1 = f1_score(y_actual, y_pred, average='weighted') # เพิ่ม Weighted F1
    mae = mean_absolute_error(y_actual, y_pred)
    under_est_rate = np.mean(y_pred < y_actual)
    
    # Log Metrics ทั้งหมดลง MLflow
    mlflow.log_metric("accuracy", accuracy)
    mlflow.log_metric("macro_f1", macro_f1)
    mlflow.log_metric("weighted_f1", weighted_f1)
    mlflow.log_metric("mae_minutes", mae)
    mlflow.log_metric("under_estimation_rate", under_est_rate)
    
    # Log Classification Report เป็น Artifact (Text file) ไว้ดูรายละเอียดราย Class
    report = classification_report(y_actual, y_pred)
    with open("outputs/classification_report.txt", "w") as f:
        f.write(report)
    mlflow.log_artifact("outputs/classification_report.txt")
    
    # Log Model
    mlflow.sklearn.log_model(model, "model")

    print("Training + MLflow Success")
    print(f"Metrics สรุป:")
    print(f"  - Accuracy: {accuracy:.4f}")
    print(f"  - Macro F1: {macro_f1:.4f}")
    print(f"  - Weighted F1: {weighted_f1:.4f}")
    print(f"  - MAE: {mae:.2f} นาที")
    print(f"  - Under-estimation Rate: {under_est_rate:.4f}")
