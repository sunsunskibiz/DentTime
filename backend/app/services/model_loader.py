from pathlib import Path
import joblib
import os

from src.features.feature_transformer import FEATURE_COLUMNS

MODEL_PATH = Path(os.getenv("MODEL_PATH", "/app/artifacts/model.joblib"))


def load_model():
    if not MODEL_PATH.exists():
        raise FileNotFoundError(f"Model not found at {MODEL_PATH}")

    loaded = joblib.load(MODEL_PATH)
    if isinstance(loaded, dict) and "model" in loaded:
        return loaded

    return {"model": loaded, "feature_cols": FEATURE_COLUMNS, "index_to_class": {}}
