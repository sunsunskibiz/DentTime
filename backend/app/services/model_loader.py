from pathlib import Path
import subprocess
import joblib
import os

from src.features.feature_transformer import FEATURE_COLUMNS

# /app/artifacts/model.joblib
MODEL_PATH = Path(os.getenv("MODEL_PATH", "/app/artifacts/model.joblib"))
FALLBACK_PATH = Path("/app/models/model.joblib")

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
        result = subprocess.run(
            ["dvc", "pull", "-r", "localremote", "artifacts/model.joblib"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
        )

        print("STDOUT:\n", result.stdout)
        print("STDERR:\n", result.stderr)

        result.check_returncode()

        print("DVC pull completed: artifacts/model.joblib")

    except subprocess.CalledProcessError as e:
        print("❌ DVC pull failed")

        # อันนี้คือของจริง
        print("Return code:", e.returncode)
        print("STDOUT:\n", e.stdout)
        print("STDERR:\n", e.stderr)

        print("Using local file instead.")

    except FileNotFoundError:
        print("DVC is not installed/available. Using local file instead.")


def load_model():
    if USE_MLFLOW_MODEL:
        import mlflow
        mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
        model = mlflow.pyfunc.load_model(MLFLOW_MODEL_URI)

        return {
            "model": model,
            "feature_cols": FEATURE_COLUMNS,
            "index_to_class": _default_index_to_class(),
            "model_version": MLFLOW_MODEL_URI,
        }

    # try DVC first
    _try_dvc_pull()

    if MODEL_PATH.exists():
        print("✅ Using DVC model")
        loaded = joblib.load(MODEL_PATH)

    if FALLBACK_PATH.exists():
        print("⚠️ Using fallback model")
        loaded = joblib.load(FALLBACK_PATH)

    else:
        raise FileNotFoundError(
            "No model found. Neither DVC model nor fallback model exists."
        )

    if isinstance(loaded, dict) and "model" in loaded:
        return loaded

    return {
        "model": loaded,
        "feature_cols": FEATURE_COLUMNS,
        "index_to_class": _default_index_to_class(),
        "model_version": "dvc" if MODEL_PATH.exists() else "fallback",
    }
