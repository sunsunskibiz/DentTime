from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from fastapi import APIRouter, Request

from app.db import get_conn, init_db
from app.monitoring_metrics import REQUEST_COUNT, REQUEST_LATENCY
from app.schemas import APIRequest, PredictRequest

router = APIRouter(tags=["predict"])
MODEL_VERSION = "DentTimeModel_v1"


def to_hour_bucket(dt: datetime) -> int:
    hour = dt.hour
    if 4 <= hour < 8:
        return 4
    if 8 <= hour < 12:
        return 8
    if 12 <= hour < 16:
        return 12
    if 16 <= hour < 20:
        return 16
    return 20


def _parse_frontend_datetime(value: str) -> datetime:
    # Browser datetime-local inputs usually send "YYYY-MM-DDTHH:MM".
    # Accept ISO strings with a trailing Z as well for API clients.
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def map_api_to_predict_request(data: APIRequest) -> PredictRequest:
    tooth_no = None if data.toothNumbers in (None, "", "none") else data.toothNumbers
    surfaces = None if data.surfaces in (None, "", "none") else data.surfaces
    dt = _parse_frontend_datetime(data.selectedDateTime)

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


def _decode_duration(predicted_index: int, index_to_class: dict[Any, Any]) -> int:
    # joblib can deserialize JSON-like dict keys either as int or str depending on
    # how the artifact was produced; support both forms safely.
    if predicted_index in index_to_class:
        return int(index_to_class[predicted_index])
    str_key = str(predicted_index)
    if str_key in index_to_class:
        return int(index_to_class[str_key])
    return int(predicted_index)


def _safe_json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def _log_prediction(
    *,
    request_id: str,
    data: APIRequest,
    req: PredictRequest,
    feature_row: dict[str, Any],
    predicted_slot: int,
    confidence: float,
) -> None:
    init_db()
    conn = get_conn()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO predictions (
                request_id,
                request_ts,
                treatment_class,
                tooth_count,
                time_of_day,
                doctor_id,
                clinic_id,
                is_first_case,
                doctor_speed_ratio,
                notes,
                predicted_slot,
                actual_slot,
                input_payload_json,
                transformed_features_json,
                prediction_confidence,
                model_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)
            """,
            (
                request_id,
                datetime.now(timezone.utc).isoformat(),
                str(feature_row.get("treatment_class")),
                int(feature_row.get("tooth_count", 0)),
                str(req.appt_hour_bucket),
                req.dentist_pseudo_id,
                req.clinic_pseudo_id,
                int(req.is_first_case),
                float(feature_row.get("doctor_pct_long", 0.0)),
                data.notes,
                int(predicted_slot),
                _safe_json_dumps(data.model_dump()),
                _safe_json_dumps(feature_row),
                float(confidence),
                MODEL_VERSION,
            ),
        )
        conn.commit()
    finally:
        conn.close()


@router.post("/predict")
def predict_api(data: APIRequest, request: Request):
    started = time.perf_counter()
    request_id = str(uuid.uuid4())

    req = map_api_to_predict_request(data)
    transformer = request.app.state.transformer
    bundle = request.app.state.model

    model = bundle["model"]
    feature_cols = bundle["feature_cols"]
    index_to_class = bundle["index_to_class"]

    row = req.model_dump()
    df = pd.DataFrame([row])

    # The transformer requires scheduled_duration_min only to compute/drop the
    # training target fields. At inference we use a dummy value and never pass it
    # to the model.
    df["scheduled_duration_min"] = 30
    features_df = transformer.transform(df)
    X = features_df[feature_cols]

    predicted_index = int(model.predict(X)[0])
    duration_minutes = _decode_duration(predicted_index, index_to_class)

    proba = model.predict_proba(X)[0]
    proba_percent = (proba * 100).tolist()
    confidence = float(max(proba)) if len(proba) else 0.0

    feature_row = X.iloc[0].to_dict()
    _log_prediction(
        request_id=request_id,
        data=data,
        req=req,
        feature_row=feature_row,
        predicted_slot=duration_minutes,
        confidence=confidence,
    )

    latency = time.perf_counter() - started
    REQUEST_COUNT.inc()
    REQUEST_LATENCY.observe(latency)

    return {
        "predicted_duration_class": duration_minutes,
        "confidence": proba_percent,
        "unit": "minutes",
        "model_version": MODEL_VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": request_id,
        "status": "success",
        "processing_time_ms": round(latency * 1000, 2),
    }
