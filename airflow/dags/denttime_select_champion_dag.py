"""
DentTime Champion Selection DAG
════════════════════════════════════════════════════════════════

Compare newly trained model (Staging) vs current champion (Production)
and promote to Production only if metrics are better.

Pipeline (3 tasks):
  compare_models → decide_promotion → promote_champion
"""
import os
import json
from pathlib import Path
from datetime import datetime

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.utils.dates import days_ago

PROJECT_ROOT = Path("/opt/airflow/project")
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
# Task 1: Compare Models
# ─────────────────────────────────────────────────────────────────────────
def _task_compare_models(**context):
    """
    Load metrics from newly trained model (Staging) and current champion (Production).
    Compare and decide if new model should be promoted.
    """
    import mlflow
    from mlflow.tracking import MlflowClient
    import pandas as pd

    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    client = MlflowClient()

    # --- Get latest Staging model (newly trained) ---
    try:
        staging_versions = client.get_latest_versions(MODEL_NAME, stages=["Staging"])
        if not staging_versions:
            raise ValueError(f"No model found in Staging for {MODEL_NAME}")
        staging_model = staging_versions[0]
        staging_version = staging_model.version
        staging_run_id = staging_model.run_id
        print(f"✓ Found Staging model: v{staging_version}")
    except Exception as e:
        raise ValueError(f"❌ Failed to get Staging model: {e}")

    # --- Get current Production model (champion) ---
    production_version = None
    production_run_id = None
    production_model = None
    try:
        prod_versions = client.get_latest_versions(MODEL_NAME, stages=["Production"])
        if prod_versions:
            production_model = prod_versions[0]
            production_version = production_model.version
            production_run_id = production_model.run_id
            print(f"✓ Found Production (Champion) model: v{production_version}")
        else:
            print("ℹ No current Production model → Staging will be promoted as first champion")
    except Exception:
        print("ℹ No Production model found")

    # --- Load metrics from MLflow runs ---
    staging_metrics = {}
    production_metrics = {}

    if staging_run_id:
        run = client.get_run(staging_run_id)
        staging_metrics = run.data.metrics
        print(f"\nStaging Model Metrics (v{staging_version}):")
        for k, v in sorted(staging_metrics.items()):
            if "eval_" in k:
                print(f"  {k}: {v:.4f}")

    if production_run_id:
        run = client.get_run(production_run_id)
        production_metrics = run.data.metrics
        print(f"\nProduction Model Metrics (v{production_version}):")
        for k, v in sorted(production_metrics.items()):
            if "eval_" in k:
                print(f"  {k}: {v:.4f}")

    # --- Comparison ---
    comparison = {
        "staging_version": staging_version,
        "staging_run_id": staging_run_id,
        "production_version": production_version,
        "production_run_id": production_run_id,
        "staging_metrics": staging_metrics,
        "production_metrics": production_metrics,
        "has_production": production_version is not None,
    }

    # Push to XCom
    context['ti'].xcom_push(key='comparison', value=comparison)
    print(f"\nComparison saved to XCom")
    return comparison


# ─────────────────────────────────────────────────────────────────────────
# Task 2: Decide Promotion
# ─────────────────────────────────────────────────────────────────────────
def _task_decide_promotion(**context):
    """
    Decide if the Staging model should be promoted to Production.
    
    Rules:
    - If no Production model exists → promote (first time)
    - If Production exists → promote only if Staging is better
      * Primary: higher eval_macro_f1 (or equal + lower eval_mae)
      * Threshold: at least 0.01 improvement OR equal with lower mae
    """
    comparison = context['ti'].xcom_pull(key='comparison', task_ids='task_compare_models')

    staging_version = comparison["staging_version"]
    production_version = comparison["production_version"]
    staging_metrics = comparison["staging_metrics"]
    production_metrics = comparison["production_metrics"]
    has_production = comparison["has_production"]

    # Primary metric: macro_f1 (higher is better)
    # Secondary metric: mae (lower is better)
    staging_f1 = staging_metrics.get("eval_macro_f1", 0)
    staging_mae = staging_metrics.get("eval_mae", float('inf'))

    decision = {
        "should_promote": False,
        "reason": "",
        "staging_version": staging_version,
        "production_version": production_version,
    }

    if not has_production:
        decision["should_promote"] = True
        decision["reason"] = (
            f"✓ First champion: v{staging_version} promoted to Production "
            f"(macro_f1={staging_f1:.4f})"
        )
    else:
        production_f1 = production_metrics.get("eval_macro_f1", 0)
        production_mae = production_metrics.get("eval_mae", float('inf'))

        f1_improvement = staging_f1 - production_f1
        mae_improvement = production_mae - staging_mae  # positive = better (lower mae)

        print(f"\n{'='*60}")
        print(f"CHAMPION SELECTION DECISION")
        print(f"{'='*60}")
        print(f"Current Champion (v{production_version}):")
        print(f"  macro_f1: {production_f1:.4f}")
        print(f"  mae:      {production_mae:.2f}")
        print(f"\nNew Challenger (v{staging_version}):")
        print(f"  macro_f1: {staging_f1:.4f}")
        print(f"  mae:      {staging_mae:.2f}")
        print(f"\nImprovement:")
        print(f"  Δ macro_f1: {f1_improvement:+.4f}")
        print(f"  Δ mae:      {mae_improvement:+.2f}")
        print(f"{'='*60}")

        # Decision threshold: +0.01 F1 improvement OR equal F1 + lower MAE
        if f1_improvement >= 0.01:
            decision["should_promote"] = True
            decision["reason"] = (
                f"✓ Promotion: v{staging_version} better macro_f1 "
                f"({production_f1:.4f} → {staging_f1:.4f}, Δ={f1_improvement:+.4f})"
            )
        elif f1_improvement >= -0.001 and mae_improvement > 0.1:  # ~equal F1, better MAE
            decision["should_promote"] = True
            decision["reason"] = (
                f"✓ Promotion: v{staging_version} equal F1 but better MAE "
                f"({production_mae:.2f} → {staging_mae:.2f}, Δ={mae_improvement:+.2f})"
            )
        else:
            decision["should_promote"] = False
            decision["reason"] = (
                f"✗ Keep champion: v{production_version} still better "
                f"(F1 diff={f1_improvement:+.4f}, MAE diff={mae_improvement:+.2f})"
            )

    print(f"\n{decision['reason']}")
    context['ti'].xcom_push(key='decision', value=decision)
    return decision


# ─────────────────────────────────────────────────────────────────────────
# Task 3: Execute Promotion
# ─────────────────────────────────────────────────────────────────────────
def _task_promote_champion(**context):
    """
    Execute the promotion decision:
    - If should_promote=True: promote Staging→Production, archive old Production
    - If should_promote=False: archive Staging, keep current Production
    """
    import mlflow
    from mlflow.tracking import MlflowClient

    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    client = MlflowClient()

    decision = context['ti'].xcom_pull(key='decision', task_ids='task_decide_promotion')
    comparison = context['ti'].xcom_pull(key='comparison', task_ids='task_compare_models')

    staging_version = decision["staging_version"]
    production_version = decision["production_version"]
    should_promote = decision["should_promote"]

    if should_promote:
        # Archive current Production (if exists)
        if production_version:
            try:
                client.transition_model_version_stage(
                    name=MODEL_NAME,
                    version=production_version,
                    stage="Archived"
                )
                print(f"✓ Archived old champion: v{production_version} → Archived")
            except Exception as e:
                print(f"⚠ Failed to archive v{production_version}: {e}")

        # Promote Staging to Production
        try:
            client.transition_model_version_stage(
                name=MODEL_NAME,
                version=staging_version,
                stage="Production"
            )
            print(f"✓ New champion promoted: v{staging_version} → Production")

            # Log event
            new_prod = client.get_model_version(MODEL_NAME, staging_version)
            print(f"\n🏆 NEW CHAMPION DEPLOYED")
            print(f"   Model: {MODEL_NAME}")
            print(f"   Version: {staging_version}")
            print(f"   Timestamp: {datetime.now().isoformat()}")

        except Exception as e:
            raise ValueError(f"❌ Failed to promote v{staging_version}: {e}")

    else:
        # Archive Staging (reject)
        try:
            client.transition_model_version_stage(
                name=MODEL_NAME,
                version=staging_version,
                stage="Archived"
            )
            print(f"✓ Rejected challenger archived: v{staging_version} → Archived")
            print(f"\n⚠ CHAMPION UNCHANGED")
            print(f"   Current champion: v{production_version} remains in Production")
            print(f"   Timestamp: {datetime.now().isoformat()}")

        except Exception as e:
            print(f"⚠ Failed to archive rejected model v{staging_version}: {e}")

    context['ti'].xcom_push(key='final_decision', value=decision)
    return decision


# ─────────────────────────────────────────────────────────────────────────
# Task 4: Deploy Champion Model
# ─────────────────────────────────────────────────────────────────────────
def _task_deploy_champion(**context):
    """
    Deploy the new champion model to backend location and version with DVC.
    - Download model from MLflow Production
    - Extract XGBoost model and save as joblib
    - DVC add and push
    """
    import mlflow
    from mlflow.tracking import MlflowClient
    import joblib
    import os, subprocess
    from pathlib import Path
    
    decision = context['ti'].xcom_pull(key='decision', task_ids='task_decide_promotion')
    should_promote = decision["should_promote"]
    
    if not should_promote:
        print("No promotion, skipping deployment")
        return
    
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    client = MlflowClient()
    
    # Get Production model
    prod_versions = client.get_latest_versions(MODEL_NAME, stages=["Production"])
    if not prod_versions:
        raise ValueError("No Production model found")
    
    prod_model = prod_versions[0]
    run_id = prod_model.run_id
    
    # Download model
    model_uri = f"runs:/{run_id}/xgb_model"
    model = mlflow.xgboost.load_model(model_uri)
    
    # Save as joblib
    model_path = ARTIFACTS_DIR / "model.joblib"
    model_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, model_path)
    
    print(f"Model saved to {model_path}")
    
    rel_model_path = str(model_path.relative_to(PROJECT_ROOT))

    run_cmd(
        ["dvc", "add", "--force", rel_model_path],
        cwd=PROJECT_ROOT,
    )

    run_cmd(
        ["dvc", "push", "-r", "localremote"],
        cwd=PROJECT_ROOT,
    )
    
    print("Champion model deployed and versioned with DVC")
    
    context['ti'].xcom_push(key='deployed', value=True)
    return True


# ─────────────────────────────────────────────────────────────────────────
# DAG Definition
# ─────────────────────────────────────────────────────────────────────────
with DAG(
    dag_id="dentime_select_champion",
    schedule=None,
    start_date=days_ago(1),
    catchup=False,
    tags=["model-selection", "champion", "mlflow", "promotion"],
    doc_md="""
## DentTime Champion Selection DAG

Compares the newly trained model (Staging) against the current champion (Production)
and promotes to Production only if it's better.

### Pipeline
```
compare_models → decide_promotion → promote_champion → deploy_champion
```

### Metrics
- **Primary**: macro_f1 (higher is better)  
- **Secondary**: mae (lower is better)

### Promotion Rules
1. **First time**: If no Production model exists → Staging becomes champion
2. **Subsequent**: Promote Staging only if:
   - macro_f1 improvement ≥ +0.01, OR
   - macro_f1 roughly equal AND mae improvement > 0.1

### Outcomes
- ✓ New champion promoted → v(N) to Production, archive v(N-1)
- ✗ Champion unchanged → Archive rejected v(N), keep v(N-1) in Production
    """,
) as dag:

    t1 = PythonOperator(
        task_id="task_compare_models",
        python_callable=_task_compare_models,
        provide_context=True,
    )

    t2 = PythonOperator(
        task_id="task_decide_promotion",
        python_callable=_task_decide_promotion,
        provide_context=True,
    )

    t3 = PythonOperator(
        task_id="task_promote_champion",
        python_callable=_task_promote_champion,
        provide_context=True,
    )

    t4 = PythonOperator(
        task_id="task_deploy_champion",
        python_callable=_task_deploy_champion,
        provide_context=True,
    )

    t1 >> t2 >> t3 >> t4
