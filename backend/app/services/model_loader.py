from pathlib import Path
import subprocess
import joblib
import os

from src.features.feature_transformer import FEATURE_COLUMNS

# /app/artifacts/model.joblib
MODEL_PATH = Path(os.getenv("MODEL_PATH", "/app/artifacts/model.joblib"))

MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "http://mlflow:5000")
MLFLOW_MODEL_URI = os.getenv(
    "MLFLOW_MODEL_URI",
    "models:/denttime_duration_classifier/Production"
)

USE_MLFLOW_MODEL = os.getenv("USE_MLFLOW_MODEL", "false").lower() in {
    "1", "true", "yes", "on"
}

CLASS_LABELS = [15, 30, 45, 60, 75, 90, 105]


def _default_index_to_class():
    return {idx: int(label) for idx, label in enumerate(CLASS_LABELS)}


def _try_dvc_pull():
    project_root = MODEL_PATH.parent.parent

    try:
        subprocess.run(
            ["dvc", "pull", "artifacts/model.joblib"],
            check=True,
            cwd=str(project_root),
        )
        print("DVC pull completed: artifacts/model.joblib")

    except subprocess.CalledProcessError as e:
        print(f"DVC pull failed: {e}. Using local file instead.")

    except FileNotFoundError:
        print("DVC is not installed/available. Using local file instead.")


def load_model():
    if USE_MLFLOW_MODEL:
        try:
            import mlflow
        except ImportError as exc:
            raise ImportError(
                "mlflow is required to load the Production model from MLflow. "
                "Install mlflow or set USE_MLFLOW_MODEL=false to load a local model file."
            ) from exc

        mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
        model = mlflow.pyfunc.load_model(MLFLOW_MODEL_URI)

        return {
            "model": model,
            "feature_cols": FEATURE_COLUMNS,
            "index_to_class": _default_index_to_class(),
            "model_version": MLFLOW_MODEL_URI,
        }

    _try_dvc_pull()

    if not MODEL_PATH.exists():
        raise FileNotFoundError(
            f"Model not found at {MODEL_PATH}. "
            "Make sure Airflow pushed artifacts/model.joblib with DVC, "
            "or mount the artifacts folder into the backend container."
        )

    loaded = joblib.load(MODEL_PATH)

    if isinstance(loaded, dict) and "model" in loaded:
        return loaded

    return {
        "model": loaded,
        "feature_cols": FEATURE_COLUMNS,
        "index_to_class": _default_index_to_class(),
        "model_version": str(MODEL_PATH),
    }