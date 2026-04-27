import pytest
from subprocess import CalledProcessError
from unittest.mock import patch, call

from src.features.dvc_utils import pull_raw_data

ARGS = {
    "dvc_file": "data/published.dvc",
    "local_csv": "/opt/airflow/data/raw/data.csv",
    "remote": "dagshub-raw",
    "project_root": "/opt/airflow/project",
}

PUBLISHED_CSV = "/opt/airflow/project/data/published/2026-04-01/data.csv"


@patch("src.features.dvc_utils.shutil.copy")
@patch("src.features.dvc_utils.glob.glob", return_value=[PUBLISHED_CSV])
@patch("src.features.dvc_utils.subprocess.run")
@patch.dict("os.environ", {"DAGSHUB_USER": "", "DAGSHUB_TOKEN": ""})
def test_pull_success(mock_run, mock_glob, mock_copy):
    result = pull_raw_data(**ARGS)
    assert result == "pulled"


@patch("src.features.dvc_utils.shutil.copy")
@patch("src.features.dvc_utils.glob.glob", return_value=[PUBLISHED_CSV])
@patch("src.features.dvc_utils.subprocess.run")
@patch.dict("os.environ", {"DAGSHUB_USER": "", "DAGSHUB_TOKEN": ""})
def test_pull_success_csv_copied(mock_run, mock_glob, mock_copy):
    pull_raw_data(**ARGS)
    mock_copy.assert_called_once_with(PUBLISHED_CSV, "/opt/airflow/data/raw/data.csv")


@patch("src.features.dvc_utils.os.path.exists", return_value=True)
@patch("src.features.dvc_utils.subprocess.run", side_effect=CalledProcessError(1, "dvc"))
@patch.dict("os.environ", {"DAGSHUB_USER": "", "DAGSHUB_TOKEN": ""})
def test_pull_fails_local_exists(mock_run, mock_exists):
    result = pull_raw_data(**ARGS)
    assert result == "skipped"


@patch("src.features.dvc_utils.os.path.exists", return_value=False)
@patch("src.features.dvc_utils.subprocess.run", side_effect=CalledProcessError(1, "dvc"))
@patch.dict("os.environ", {"DAGSHUB_USER": "", "DAGSHUB_TOKEN": ""})
def test_pull_fails_no_local(mock_run, mock_exists):
    with pytest.raises(RuntimeError, match="Cannot proceed"):
        pull_raw_data(**ARGS)


@patch("src.features.dvc_utils.glob.glob", return_value=[])
@patch("src.features.dvc_utils.subprocess.run")
@patch.dict("os.environ", {"DAGSHUB_USER": "", "DAGSHUB_TOKEN": ""})
def test_pull_success_no_csv_in_published(mock_run, mock_glob):
    with pytest.raises(RuntimeError, match="no .csv found"):
        pull_raw_data(**ARGS)
