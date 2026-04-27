from __future__ import annotations

import json
import math
import uuid
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from fastapi import APIRouter, Request

from app.db import get_conn
from app.schemas import APIRequest, PredictRequest


router = APIRouter(tags=["predict"])


def to_hour_bucket(dt: datetime) -> int:
    """Map appointment hour to the same bucket definition used by model training."""
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


def time_of_day_label(hour_bucket: int) -> str:
    """Human-readable label kept for backward-compatible monitoring logs."""
    if hour_bucket in (4, 8):
        return "morning"
    if hour_bucket == 12:
        return "afternoon"
    if hour_bucket == 16:
        return "evening"
    return "night"


def map_api_to_predict_request(data: APIRequest) -> PredictRequest:
    tooth_no = None if data.toothNumbers in (None, "", "none") else data.toothNumbers
    surfaces = None if data.surfaces in (None, "", "none") else data.surfaces

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


def _json_safe(value: Any) -> Any:
    """Convert numpy/pandas scalar values to JSON-safe Python values."""
    if value is None:
        return None

    if hasattr(value, "item"):
        try:
            value = value.item()
        except Exception:
            pass

    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None

    if pd.isna(value):
        return None

    return value


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def _decode_duration_minutes(predicted_index: int, index_to_class: dict) -> int:
    """Support both model outputs: encoded index 0..5 and direct duration classes."""
    if predicted_index in index_to_class:
        return int(index_to_class[predicted_index])
    if str(predicted_index) in index_to_class:
        return int(index_to_class[str(predicted_index)])
    return int(predicted_index)


def _log_prediction(
    *,
    request_id: str,
    timestamp: datetime,
    data: APIRequest,
    req: PredictRequest,
    features: dict[str, Any],
    duration_minutes: int,
    confidence_percent: float | None,
    model_version: str,
) -> None:
    """Persist one prediction row so Prometheus/Grafana can count it."""
    payload = data.model_dump()
    payload["timeOfDay"] = time_of_day_label(req.appt_hour_bucket)
    payload["isFirstCase"] = bool(req.is_first_case)
    payload["request_time"] = data.request_time or timestamp.isoformat()

    transformed_features = {key: _json_safe(value) for key, value in features.items()}

    conn = get_conn()
    try:
        conn.execute(
            """
            INSERT INTO predictions (
                request_id,
                request_ts,
                treatment_class,
                tooth_count,
                time_of_day,
                doctor_id,
                is_first_case,
                doctor_speed_ratio,
                notes,
                predicted_slot,
                actual_slot,
                input_payload_json,
                transformed_features_json,
                prediction_confidence,
                model_version
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                timestamp.isoformat(),
                data.treatmentSymptoms,
                int(transformed_features.get("tooth_count") or 0),
                payload["timeOfDay"],
                data.doctorId,
                int(req.is_first_case),
                transformed_features.get("doctor_pct_long"),
                data.notes or "",
                int(duration_minutes),
                None,
                _json_dumps(payload),
                _json_dumps(transformed_features),
                confidence_percent,
                model_version,
            ),
        )
        conn.commit()
    finally:
        conn.close()


@router.post("/predict")
def predict_api(data: APIRequest, request: Request):
    req = map_api_to_predict_request(data)
    transformer = request.app.state.transformer
    bundle = request.app.state.model

    model = bundle["model"]
    feature_cols = bundle["feature_cols"]
    index_to_class = bundle["index_to_class"]
    model_version = bundle.get("model_version", "DentTimeModel_v1")

    row = req.model_dump()
    df = pd.DataFrame([row])

    # The transformer expects the training target column to exist.
    # At inference time the true duration is unknown, so use a dummy value and
    # remove target/audit columns before passing features to the model.
    df["scheduled_duration_min"] = 30
    features_df = transformer.transform(df)

    # Keep the exact feature order the model was trained with.
    X = features_df[feature_cols].copy()

    predicted_index = int(model.predict(X)[0])
    duration_minutes = _decode_duration_minutes(predicted_index, index_to_class)

    proba_percent: list[float] = []
    confidence_percent: float | None = None
    if hasattr(model, "predict_proba"):
        proba_percent = (model.predict_proba(X)[0] * 100).tolist()
        confidence_percent = float(max(proba_percent)) if proba_percent else None

    request_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc)

    _log_prediction(
        request_id=request_id,
        timestamp=timestamp,
        data=data,
        req=req,
        features=X.iloc[0].to_dict(),
        duration_minutes=duration_minutes,
        confidence_percent=confidence_percent,
        model_version=model_version,
    )

    return {
        "predicted_duration_class": duration_minutes,
        "confidence": proba_percent,
        "unit": "minutes",
        "model_version": model_version,
        "timestamp": timestamp.isoformat(),
        "request_id": request_id,
        "status": "success",
    }
