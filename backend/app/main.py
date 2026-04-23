from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from app.routers.predict import router as predict_router
from app.routers.actual import router as actual_router
from app.routers.options import router as options_router

from src.features.feature_transformer import FeatureTransformer
import json


ARTIFACTS = 'src/features/artifacts'


@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup logic
    app.state.transformer = FeatureTransformer(
        doctor_profile_path=f"{ARTIFACTS}/doctor_profile.json",
        clinic_profile_path=f"{ARTIFACTS}/clinic_profile.json",
        treatment_dict_path=f"{ARTIFACTS}/treatment_dict.json",
        treatment_encoding_path=f"{ARTIFACTS}/treatment_encoding.json",
    )

    with open(f"{ARTIFACTS}/doctor_profile.json", "r", encoding="utf-8") as f:
        app.state.doctor_profile = json.load(f)

    with open(f"{ARTIFACTS}/clinic_profile.json", "r", encoding="utf-8") as f:
        app.state.clinic_profile = json.load(f)

    with open(f"{ARTIFACTS}/treatment_encoding.json", "r", encoding="utf-8") as f:
        app.state.treatment_encoding = json.load(f)

    yield  # app starts running here


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



@app.get("/")
def root():
    return {"message": "DentTime backend is running"}