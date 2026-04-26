import uuid
from datetime import datetime, timezone
from xml.parsers.expat import model

import pandas as pd
from fastapi import APIRouter, Request
from app.schemas import PredictRequest, PredictResponse, APIRequest
# from app.services.predict import predict
# from app.services.mlflow_loader import get_model_info


router = APIRouter(tags=["predict"])



def to_hour_bucket(dt: datetime) -> int:
    hour = dt.hour

    if 4 <= hour < 8:
        return 4
    elif 8 <= hour < 12:
        return 8
    elif 12 <= hour < 16:
        return 12
    elif 16 <= hour < 20:
        return 16
    else:
        return 20


def map_api_to_predict_request(data: APIRequest) -> PredictRequest:
  
    if data.toothNumbers == 'none':
        tooth_no = None
    else:
        tooth_no = data.toothNumbers

    if data.surfaces == 'none':
        surfaces = None
    else:
        surfaces = data.surfaces

    dt = datetime.fromisoformat(data.selectedDateTime)

    return PredictRequest(
        clinic_pseudo_id=data.clinicId,
        dentist_pseudo_id=data.doctorId,
        has_dentist_id=1 if data.doctorId else 0,
        treatment=data.treatmentSymptoms,
        tooth_no=tooth_no,
        surfaces=surfaces,
        total_amount=data.totalAmount,
        has_notes=1 if data.notes and data.notes.strip() else 0,
        appt_day_of_week=dt.weekday(),
        appt_hour_bucket=to_hour_bucket(dt),
        is_first_case=0,
        appointment_rank_in_day=None,
    )

# mock predict API for testing frontend integration
# @router.post("/predict")
# def predict_api(data: APIRequest, request: Request):
#     print("Received data for prediction:", data)
#     req = map_api_to_predict_request(data)
#     print("Mapped to PredictRequest:", req)

#     transformer = request.app.state.transformer

#     # 1. Convert to DataFrame (single row)
#     row = req.model_dump()
#     df = pd.DataFrame([row])

#     # 2. Feature engineering — adds all 17 numeric features
#     #    NOTE: transform() expects 'scheduled_duration_min' for target binning.
#     #    At inference time we do NOT have it, so we must handle this.
#     #    Pass a dummy value (e.g. 30) — it will be binned into duration_class
#     #    but we drop duration_class before predicting (see step 3).
#     df['scheduled_duration_min'] = 30  # dummy — dropped before model input
#     features_df = transformer.transform(df)

#     # 3. Drop audit/target columns — model does not see these
#     X = features_df.drop(columns=['scheduled_duration_min', 'duration_class'])

#     print(X.is_area_treatment)
#     request_id = str(uuid.uuid4())
#     return {
#         "predicted_duration_class": 45,
#         "unit": "minutes",
#         "model_version": "DentTimeModel_v3",
#         "timestamp": datetime.now(timezone.utc).isoformat(),
#         "request_id": request_id,
#         "status": "success",
#     }

@router.post("/predict")
def predict_api(data: APIRequest, request: Request):
    req = map_api_to_predict_request(data)
    transformer = request.app.state.transformer
    bundle = request.app.state.model
  # loaded separately at startup

    model = bundle["model"]
    feature_cols = bundle["feature_cols"]
    index_to_class = bundle["index_to_class"]
    # 1. Convert to DataFrame (single row)
    row = req.model_dump()
    df = pd.DataFrame([row])

    # 2. Feature engineering — adds all 17 numeric features
    #    NOTE: transform() expects 'scheduled_duration_min' for target binning.
    #    At inference time we do NOT have it, so we must handle this.
    #    Pass a dummy value (e.g. 30) — it will be binned into duration_class
    #    but we drop duration_class before predicting (see step 3).
    df['scheduled_duration_min'] = 30  # dummy — dropped before model input
    features_df = transformer.transform(df)

    # 3. Drop audit/target columns — model does not see these
    X = features_df.drop(columns=['scheduled_duration_min', 'duration_class'])

    # 4. Predict — model returns integer class index (0–5)
    predicted_index = int(model.predict(X)[0])

    # 5. Decode: map model output to duration minutes
    #    Model was trained on duration_class ∈ {15,30,45,60,90,105}
    #    If model outputs index (0–5), map back with label encoder.
    #    If model was trained directly on the class values, use as-is.
    duration_minutes = index_to_class[predicted_index]
    # # If your label encoder maps {15→0, 30→1, 45→2, 60→3, 90→4, 105→5}:
    # DECODE = {0: 15, 1: 30, 2: 45, 3: 60, 4: 90, 5: 105}
    # duration_minutes = DECODE[model.predict(X)[0]]
    # # If trained directly on {15, 30, 45, 60, 90, 105} as labels:
    # duration_minutes = int(model.predict(X)[0])  

    proba = model.predict_proba(X)[0]
    proba_percent = (proba * 100).tolist()
    return {
        'predicted_duration_class': duration_minutes,
        'confidence': proba_percent,
        "model_version": "DentTimeModel_v1",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": str(uuid.uuid4()),
        "status": "success"
    }


# @router.get("/model-info")
# def get_model_info_api():
#     return get_model_info()