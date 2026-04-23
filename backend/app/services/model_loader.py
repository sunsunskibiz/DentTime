from pathlib import Path
import joblib

BASE_DIR = Path(__file__).resolve().parent.parent.parent  # /app
MODEL_PATH = BASE_DIR / "artifacts" / "model.joblib"


def load_model():
    print("Loading model from:", MODEL_PATH)

    if not MODEL_PATH.exists():
        raise FileNotFoundError(f"Model not found at {MODEL_PATH}")

    model = joblib.load(MODEL_PATH)
    return model