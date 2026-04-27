import sys
from pathlib import Path

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.utils.dates import days_ago
from airflow.utils.trigger_rule import TriggerRule

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

def _task_pull_raw_data():
    sys.path.insert(0, str(PROJECT_ROOT))
    from airflow.exceptions import AirflowSkipException
    from src.features.dvc_utils import pull_raw_data

    status = pull_raw_data(
        dvc_file="data/published.dvc",
        local_csv=str(PROJECT_ROOT / "data" / "raw" / "data.csv"),
        remote="dagshub-raw",
        project_root=str(PROJECT_ROOT),
    )
    if status == "skipped":
        raise AirflowSkipException("DVC pull failed; using existing local data/raw/data.csv")


def _task_load_and_split():
    sys.path.insert(0, str(PROJECT_ROOT))
    import pandas as pd
    from src.features.feature_transformer import LEAKAGE_COLUMNS

    INTERIM.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(RAW_CSV)
    train_df = df[df["appt_year_month"] <= "2025-02"].copy()
    test_df  = df[df["appt_year_month"] == "2025-04"].copy()

    if len(train_df) == 0:
        raise ValueError("Train split is empty — check appt_year_month column")
    if len(test_df) == 0:
        raise ValueError("Test split is empty — check appt_year_month column")

    leakage_present = [c for c in LEAKAGE_COLUMNS if c in train_df.columns]
    if leakage_present:
        train_df = train_df.drop(columns=leakage_present)
        test_df  = test_df.drop(columns=leakage_present)

    train_df.to_parquet(INTERIM / "train_split.parquet", index=False)
    test_df.to_parquet(INTERIM  / "test_split.parquet",  index=False)


def _task_build_doctor_profile():
    sys.path.insert(0, str(PROJECT_ROOT))
    import json
    import pandas as pd
    from src.features.build_profiles import build_doctor_profile

    train_df = pd.read_parquet(INTERIM / "train_split.parquet")
    profile  = build_doctor_profile(train_df)

    with open(ARTIFACTS / "doctor_profile.json", "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2, ensure_ascii=False)


def _task_build_clinic_profile():
    sys.path.insert(0, str(PROJECT_ROOT))
    import json
    import pandas as pd
    from src.features.build_profiles import build_clinic_profile

    train_df = pd.read_parquet(INTERIM / "train_split.parquet")
    profile  = build_clinic_profile(train_df)

    with open(ARTIFACTS / "clinic_profile.json", "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2, ensure_ascii=False)


def _task_build_treatment_encoding():
    sys.path.insert(0, str(PROJECT_ROOT))
    import json
    from src.features.feature_transformer import build_treatment_encoding, load_treatment_dict

    treatment_dict = load_treatment_dict(str(ARTIFACTS / "treatment_dict.json"))
    encoding       = build_treatment_encoding(treatment_dict)

    with open(ARTIFACTS / "treatment_encoding.json", "w", encoding="utf-8") as f:
        json.dump(encoding, f, indent=2, sort_keys=True)


def _task_transform_train():
    sys.path.insert(0, str(PROJECT_ROOT))
    import pandas as pd
    from src.features.feature_transformer import FeatureTransformer

    FEATURES.mkdir(parents=True, exist_ok=True)

    transformer = FeatureTransformer(
        doctor_profile_path=str(ARTIFACTS / "doctor_profile.json"),
        clinic_profile_path=str(ARTIFACTS / "clinic_profile.json"),
        treatment_dict_path=str(ARTIFACTS / "treatment_dict.json"),
        treatment_encoding_path=str(ARTIFACTS / "treatment_encoding.json"),
    )
    train_df = pd.read_parquet(INTERIM / "train_split.parquet")
    transformer.transform(train_df).to_parquet(
        FEATURES / "features_train.parquet", index=False
    )


def _task_transform_test():
    sys.path.insert(0, str(PROJECT_ROOT))
    import pandas as pd
    from src.features.feature_transformer import FeatureTransformer

    transformer = FeatureTransformer(
        doctor_profile_path=str(ARTIFACTS / "doctor_profile.json"),
        clinic_profile_path=str(ARTIFACTS / "clinic_profile.json"),
        treatment_dict_path=str(ARTIFACTS / "treatment_dict.json"),
        treatment_encoding_path=str(ARTIFACTS / "treatment_encoding.json"),
    )
    test_df = pd.read_parquet(INTERIM / "test_split.parquet")
    transformer.transform(test_df).to_parquet(
        FEATURES / "features_test.parquet", index=False
    )


def _task_compute_feature_stats():
    sys.path.insert(0, str(PROJECT_ROOT))
    import json
    import pandas as pd

    train_features = pd.read_parquet(FEATURES / "features_train.parquet")

    with open(ARTIFACTS / "treatment_encoding.json", encoding="utf-8") as f:
        encoding = json.load(f)
    unknown_int = encoding["UNKNOWN"]

    stats = {}
    for col in train_features.columns:
        col_stats: dict = {"null_rate": float(train_features[col].isna().mean())}
        if col == "treatment_class":
            col_stats["unknown_rate"] = float(
                (train_features[col] == unknown_int).sum() / len(train_features)
            )
            col_stats["mean"] = float(train_features[col].mean())
        elif train_features[col].dtype == object:
            top5 = train_features[col].value_counts().head(5).to_dict()
            col_stats["top5"] = {str(k): int(v) for k, v in top5.items()}
        else:
            col_stats["mean"] = float(train_features[col].mean())
            if col == "appt_hour_bucket":
                col_stats["pct_sentinel"] = float(
                    (train_features[col] == -1).sum() / len(train_features)
                )
        stats[col] = col_stats

    with open(FEATURES / "feature_stats.json", "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2)


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

    pull_raw_data = PythonOperator(
        task_id="task_pull_raw_data",
        python_callable=_task_pull_raw_data,
    )
    load_and_split = PythonOperator(
        task_id="task_load_and_split",
        python_callable=_task_load_and_split,
        trigger_rule=TriggerRule.NONE_FAILED,
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
    pull_raw_data >> load_and_split
    load_and_split >> [build_doctor_profile, build_clinic_profile, build_treatment_encoding]
    [build_doctor_profile, build_clinic_profile, build_treatment_encoding] >> transform_train
    [build_doctor_profile, build_clinic_profile, build_treatment_encoding] >> transform_test
    [transform_train, transform_test] >> compute_feature_stats
