from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers.predict import router as predict_router
from app.routers.actual import router as actual_router
from app.routers.options import router as options_router

app = FastAPI(title="DentTime Backend")

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