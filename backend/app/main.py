from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, List

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db import init_db
from app.routers.actual import router as actual_router
from app.routers.metrics import router as metrics_router
from app.routers.options import router as options_router
from app.routers.predict import router as predict_router
from app.services.model_loader import load_model
from src.features.feature_transformer import FEATURE_COLUMNS, FeatureTransformer

import os
ARTIFACTS_DIR = Path(os.getenv("ARTIFACTS_DIR", "/app/artifacts"))
RUNTIME_ARTIFACTS_DIR = Path(os.getenv("RUNTIME_ARTIFACTS_DIR", "/app/src/features/artifacts"))


class ModelBundle(dict):
    @property
    def model(self):
        return self["model"]

    @property
    def label_encoder(self):
        return self.get("label_encoder")

    @property
    def feature_cols(self) -> List[str]:
        return self.get("feature_cols", FEATURE_COLUMNS)

    @property
    def index_to_class(self) -> Dict[int, int]:
        raw = self.get("index_to_class", {})
        return {int(k): int(v) for k, v in raw.items()}

    @property
    def model_version(self) -> str:
        return self.get("model_version", "denttime_model_unknown")


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    return json.loads(text)


def _candidate_artifact_paths(filename: str) -> list[Path]:
    return [
        RUNTIME_ARTIFACTS_DIR / filename,
        ARTIFACTS_DIR / filename,
    ]


def _resolve_artifact_path(filename: str) -> Path:
    for candidate in _candidate_artifact_paths(filename):
        if candidate.exists():
            return candidate
    raise RuntimeError(f"required artifact not found: {filename}")


def load_transformer() -> FeatureTransformer:
    return FeatureTransformer(
        doctor_profile_path=str(_resolve_artifact_path("doctor_profile.json")),
        clinic_profile_path=str(_resolve_artifact_path("clinic_profile.json")),
        treatment_dict_path=str(_resolve_artifact_path("treatment_dict.json")),
        treatment_encoding_path=str(_resolve_artifact_path("treatment_encoding.json")),
    )


def load_model_bundle() -> ModelBundle:
    loaded = load_model()
    if isinstance(loaded, dict) and "model" in loaded:
        bundle = ModelBundle(loaded)
    else:
        bundle = ModelBundle({"model": loaded})
    if not bundle.get("feature_cols"):
        bundle["feature_cols"] = FEATURE_COLUMNS
    return bundle


def _load_runtime_artifacts() -> None:
    init_db()
    app.state.transformer = load_transformer()
    app.state.model = load_model_bundle()
    app.state.model_bundle = app.state.model

    with open(_resolve_artifact_path("doctor_profile.json"), encoding="utf-8") as f:
        app.state.doctor_profile = json.load(f)
    with open(_resolve_artifact_path("clinic_profile.json"), encoding="utf-8") as f:
        app.state.clinic_profile = json.load(f)
    with open(_resolve_artifact_path("treatment_encoding.json"), encoding="utf-8") as f:
        app.state.treatment_encoding = json.load(f)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _load_runtime_artifacts()
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
app.include_router(metrics_router)
app.include_router(options_router)


@app.get("/")
def root():
    return {"message": "DentTime backend is running"}
