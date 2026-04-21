"""
DentTime feature engineering DAG.

7 tasks that wrap feature_engineering.py as independently-rerunnable steps.
Outputs are written to the project root bind mount so version-control tracking
on the host works without any file copying.
"""
import sys
from pathlib import Path

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.utils.dates import days_ago

# ---------------------------------------------------------------------------
# Path constants — defined at module level, referenced by all task functions
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path("/opt/airflow/project")
ARTIFACTS    = PROJECT_ROOT / "src/features/artifacts"
FEATURES     = PROJECT_ROOT / "features"
INTERIM      = Path("/opt/airflow/data/interim")
RAW_CSV      = Path("/opt/airflow/data/raw/data.csv")


# ---------------------------------------------------------------------------
# Task functions
# ---------------------------------------------------------------------------

def _task_load_and_split():
    sys.path.insert(0, str(PROJECT_ROOT))


def _task_build_doctor_profile():
    sys.path.insert(0, str(PROJECT_ROOT))


def _task_build_clinic_profile():
    sys.path.insert(0, str(PROJECT_ROOT))


def _task_build_treatment_encoding():
    sys.path.insert(0, str(PROJECT_ROOT))


def _task_transform_train():
    sys.path.insert(0, str(PROJECT_ROOT))


def _task_transform_test():
    sys.path.insert(0, str(PROJECT_ROOT))


def _task_compute_feature_stats():
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# DAG definition
# ---------------------------------------------------------------------------
with DAG(
    dag_id="denttime_feature_engineering",
    schedule=None,
    start_date=days_ago(1),
    catchup=False,
    tags=["feature-engineering"],
) as dag:

    load_and_split = PythonOperator(
        task_id="task_load_and_split",
        python_callable=_task_load_and_split,
    )
    build_doctor_profile = PythonOperator(
        task_id="task_build_doctor_profile",
        python_callable=_task_build_doctor_profile,
    )
    build_clinic_profile = PythonOperator(
        task_id="task_build_clinic_profile",
        python_callable=_task_build_clinic_profile,
    )
    build_treatment_encoding = PythonOperator(
        task_id="task_build_treatment_encoding",
        python_callable=_task_build_treatment_encoding,
    )
    transform_train = PythonOperator(
        task_id="task_transform_train",
        python_callable=_task_transform_train,
    )
    transform_test = PythonOperator(
        task_id="task_transform_test",
        python_callable=_task_transform_test,
    )
    compute_feature_stats = PythonOperator(
        task_id="task_compute_feature_stats",
        python_callable=_task_compute_feature_stats,
    )

    # Dependency wiring
    load_and_split >> [build_doctor_profile, build_clinic_profile, build_treatment_encoding]
    [build_doctor_profile, build_clinic_profile, build_treatment_encoding] >> transform_train
    [build_doctor_profile, build_clinic_profile, build_treatment_encoding] >> transform_test
    [transform_train, transform_test] >> compute_feature_stats
