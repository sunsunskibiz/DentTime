"""
DentTime Get Model DAG — Fetch Latest Model from MLflow & Push with DVC
══════════════════════════════════════════════════════════════════════════

Fetch the latest model version from MLflow (Production stage) and push to DVC storage.

Pipeline (2 tasks):
  get_latest_model → dvc_push_model
"""
import os
import json
import joblib
from pathlib import Path
from datetime import datetime

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.utils.dates import days_ago

PROJECT_ROOT = Path("/opt/airflow/project")
MODEL_DIR = PROJECT_ROOT / "models"
ARTIFACTS_DIR = PROJECT_ROOT / "artifacts"

MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "http://mlflow:5000")
MODEL_NAME = "denttime_duration_classifier"

def run_cmd(cmd, cwd):
    import subprocess

    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )

    print("CMD:", " ".join(cmd))
    print("STDOUT:", result.stdout)
    print("STDERR:", result.stderr)

    result.check_returncode()
    return result
# ─────────────────────────────────────────────────────────────────────────
# Task 1: Get Latest Model from MLflow
# ─────────────────────────────────────────────────────────────────────────
def _task_get_latest_model(**context):
    """
    Fetch the latest model version from MLflow (Production stage).
    Download the model to local artifacts directory.
    """
    import mlflow
    from mlflow.tracking import MlflowClient
    import joblib

    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    client = MlflowClient()

    # Ensure directories exist
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

    # --- Get latest Production model ---
    try:
        production_versions = client.get_latest_versions(MODEL_NAME, stages=["Production"])
        if not production_versions:
            print(f"⚠ No Production model found, trying Staging...")
            production_versions = client.get_latest_versions(MODEL_NAME, stages=["Staging"])
        
        if not production_versions:
            raise ValueError(f"No model found in Production or Staging for {MODEL_NAME}")
        
        model_version = production_versions[0]
        version_num = model_version.version
        run_id = model_version.run_id
        stage = model_version.current_stage
        
        print(f"✓ Found {stage} model: v{version_num} (run_id: {run_id})")
    except Exception as e:
        raise ValueError(f"❌ Failed to get latest model: {e}")

    # --- Load and dump model with joblib ---
    try:
        import joblib
        
        # Load model from MLflow using run_id
        model_uri = f"runs:/{run_id}/xgb_model"
        model = mlflow.xgboost.load_model(model_uri)
        print(f"  Loaded model from MLflow: {model_uri}")
        
        # Save as joblib to artifacts directory
        local_model_path = ARTIFACTS_DIR / "model.joblib"
        ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        joblib.dump(model, local_model_path)
        print(f"✓ Model saved to {local_model_path}")
        
        # Also try to get feature_columns metadata from run
        try:
            run = mlflow.get_run(run_id)
            feature_cols_data = run.data.params
            if feature_cols_data:
                with open(ARTIFACTS_DIR / "feature_columns.json", "w") as f:
                    json.dump(feature_cols_data, f, indent=2)
                print(f"✓ Feature columns metadata saved")
        except:
            print("  ⚠ Could not retrieve feature columns metadata")
    
    except Exception as e:
        print(f"❌ Error loading/dumping model: {e}")
        raise

    # Push to context
    context['ti'].xcom_push(key='model_version', value=str(version_num))
    context['ti'].xcom_push(key='model_stage', value=stage)
    context['ti'].xcom_push(key='run_id', value=run_id)
    
    print(f"✓ Ready to push model v{version_num} to DVC")


# ─────────────────────────────────────────────────────────────────────────
# Task 2: Push Model to DVC Storage
# ─────────────────────────────────────────────────────────────────────────
def _task_dvc_push_model(**context):
    """
    Push model artifacts to DVC remote storage.
    Tracks model version information in metadata.
    """
    import subprocess
    import joblib
    
    model_version = context['ti'].xcom_pull(key='model_version', task_ids='task_get_latest_model')
    model_stage = context['ti'].xcom_pull(key='model_stage', task_ids='task_get_latest_model')
    run_id = context['ti'].xcom_pull(key='run_id', task_ids='task_get_latest_model')

    print(f"📦 Pushing model v{model_version} ({model_stage}) to DVC...")

    # --- DVC push (using existing config) ---
    os.chdir(PROJECT_ROOT)

    # Check model exists
    model_path = PROJECT_ROOT / "artifacts/model.joblib"
    if not model_path.exists():
        raise ValueError("Model file not found before DVC add")

    # Add + push
    run_cmd(
        ["dvc", "add", "--force", "artifacts/model.joblib"],
        cwd=PROJECT_ROOT,
    )

    run_cmd(
        ["dvc", "push", "-r", "localremote"],
        cwd=PROJECT_ROOT,
    )


# ─────────────────────────────────────────────────────────────────────────
# DAG Definition
# ─────────────────────────────────────────────────────────────────────────
default_args = {
    'owner': 'denttime-ml',
    'start_date': days_ago(1),
    'retries': 1,
}

dag = DAG(
    'get_model_dag',
    default_args=default_args,
    description='Fetch latest model from MLflow and push to DVC',
    schedule_interval='@daily',
    catchup=False,
    tags=['denttime', 'ml-ops', 'model-management'],
)

# Define tasks
task_get_latest_model = PythonOperator(
    task_id='task_get_latest_model',
    python_callable=_task_get_latest_model,
    dag=dag,
)

task_dvc_push_model = PythonOperator(
    task_id='task_dvc_push_model',
    python_callable=_task_dvc_push_model,
    dag=dag,
)

# Task dependencies
task_get_latest_model >> task_dvc_push_model
