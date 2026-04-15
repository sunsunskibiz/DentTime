import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter

from app.schemas import PredictRequest, PredictResponse
# from app.services.predict import predict
# from app.services.mlflow_loader import get_model_info

router = APIRouter(tags=["predict"])

# mock predict API for testing frontend integration
@router.post("/predict", response_model=PredictResponse)
def predict_api(data: PredictRequest):
    print("Received data for prediction:", data)
    request_id = str(uuid.uuid4())
    start_time = data.request_time
    #mock processing time
    time.sleep(5)
    end_time = datetime.now(timezone.utc)
    processing_time_ms = (end_time - start_time).total_seconds() * 1000
    return {
        "predicted_duration_class": 45,
        "unit": "minutes",
        "model_version": "DentTimeModel_v3",
        "timestamp": end_time.isoformat(),
        "request_id": request_id,
        "status": "success",
        "processing_time_ms": processing_time_ms
    }

# @router.post("/predict")
# def predict_api(data: PredictRequest):
#     pred, prob = predict(data.dict())

#     return {
#         "predicted_duration_class": pred,
#         "unit": "minutes",
#         "confidence": prob,
#         "status": "success"
#     }

# @router.get("/model-info")
# def get_model_info_api():
#     return get_model_info()