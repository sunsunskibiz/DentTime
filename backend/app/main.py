from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db import init_db
from app.routers.actual import router as actual_router
from app.routers.options import router as options_router
from app.routers.predict import router as predict_router
from app.monitoring_metrics import router as monitoring_router
from app.services.model_loader import load_model
from src.features.feature_transformer import FeatureTransformer

ARTIFACTS_DIR = Path("src/features/artifacts")


def _load_json(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Required artifact not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()

    app.state.model = load_model()
    print("Model loaded")

    app.state.transformer = FeatureTransformer(
        doctor_profile_path=str(ARTIFACTS_DIR / "doctor_profile.json"),
        clinic_profile_path=str(ARTIFACTS_DIR / "clinic_profile.json"),
        treatment_dict_path=str(ARTIFACTS_DIR / "treatment_dict.json"),
        treatment_encoding_path=str(ARTIFACTS_DIR / "treatment_encoding.json"),
    )

    app.state.doctor_profile = _load_json(ARTIFACTS_DIR / "doctor_profile.json")
    app.state.clinic_profile = _load_json(ARTIFACTS_DIR / "clinic_profile.json")
    app.state.treatment_encoding = _load_json(ARTIFACTS_DIR / "treatment_encoding.json")

    yield


app = FastAPI(title="DentTime Backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(predict_router)
app.include_router(actual_router)
app.include_router(options_router)
app.include_router(monitoring_router)


@app.get("/")
def root():
    return {"message": "DentTime backend is running"}
