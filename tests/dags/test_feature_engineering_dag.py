import ast
from pathlib import Path

DAG_PATH = Path("airflow/dags/feature_engineering_dag.py")

EXPECTED_TASKS = [
    "task_pull_raw_data",
    "task_load_and_split",
    "task_build_doctor_profile",
    "task_build_clinic_profile",
    "task_build_treatment_encoding",
    "task_transform_train",
    "task_transform_test",
    "task_compute_feature_stats",
]

EXPECTED_CONSTANTS = ["PROJECT_ROOT", "ARTIFACTS", "FEATURES", "INTERIM", "RAW_CSV"]


def test_dag_file_exists():
    assert DAG_PATH.exists(), f"DAG file not found at {DAG_PATH}"


def test_dag_file_valid_syntax():
    source = DAG_PATH.read_text()
    ast.parse(source)


def test_uses_schedule_not_schedule_interval():
    source = DAG_PATH.read_text()
    assert "schedule_interval" not in source, \
        "schedule_interval is deprecated in Airflow 2.4+, use schedule="
    assert "schedule=None" in source


def test_has_all_eight_tasks():
    source = DAG_PATH.read_text()
    for task_id in EXPECTED_TASKS:
        assert task_id in source, f"Missing task_id: {task_id}"


def test_no_xcom_usage():
    source = DAG_PATH.read_text()
    assert "xcom_push" not in source, "XCom not allowed — use file-based communication"
    assert "xcom_pull" not in source, "XCom not allowed — use file-based communication"


def test_pull_task_calls_dvc_utils():
    source = DAG_PATH.read_text()
    assert "subprocess" not in source, \
        "No subprocess calls in DAG — delegate to src.features.dvc_utils"
    assert "dvc_utils" in source, \
        "_task_pull_raw_data must import from src.features.dvc_utils"


def test_path_constants_at_module_level():
    source = DAG_PATH.read_text()
    for const in EXPECTED_CONSTANTS:
        assert const in source, f"Missing path constant: {const}"


def test_sys_path_insert_in_each_task():
    source = DAG_PATH.read_text()
    tree = ast.parse(source)
    task_func_names = [f"_task_{t.replace('task_', '')}" for t in EXPECTED_TASKS]
    funcs_with_insert = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("_task_"):
            func_src = ast.unparse(node)
            if "sys.path.insert" in func_src:
                funcs_with_insert.add(node.name)
    for fn in task_func_names:
        assert fn in funcs_with_insert, \
            f"{fn} is missing sys.path.insert(0, str(PROJECT_ROOT))"


def test_pull_task_is_first():
    source = DAG_PATH.read_text()
    assert "pull_raw_data >> load_and_split" in source, \
        "task_pull_raw_data must be wired before task_load_and_split"


def test_load_and_split_trigger_rule():
    source = DAG_PATH.read_text()
    assert "NONE_FAILED_MIN_ONE_SUCCESS" in source, \
        "task_load_and_split must use TriggerRule.NONE_FAILED_MIN_ONE_SUCCESS"


def test_dependency_wiring():
    source = DAG_PATH.read_text()
    assert "load_and_split >> [build_doctor_profile" in source or \
           "load_and_split >> [build_clinic_profile" in source, \
           "load_and_split must fan out to profile/encoding tasks"
    assert "[transform_train, transform_test] >> compute_feature_stats" in source, \
           "Both transforms must complete before feature stats"
