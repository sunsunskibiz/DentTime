from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.main import app
from monitoring.update_metrics import main as update_metrics


def main() -> None:
    with TestClient(app) as client:
        options = client.get("/options")
        options.raise_for_status()
        data = options.json()

        treatment = data["treatments"][0]["treatment"]
        clinic_id = data["clinics"][0]["id"]
        doctor_id = data["doctors"][0]["id"] if data["doctors"] else None

        predict_payload = {
            "treatmentSymptoms": treatment,
            "toothNumbers": "16",
            "surfaces": "none",
            "selectedDateTime": "2026-04-21T09:00:00",
            "totalAmount": 990.0,
            "doctorId": doctor_id,
            "clinicId": clinic_id,
            "notes": "smoke test",
            "request_time": datetime.now(timezone.utc).isoformat(),
        }
        pred = client.post("/predict", json=predict_payload)
        pred.raise_for_status()
        pred_json = pred.json()
        print("predict:", pred_json)

        actual_payload = {
            "request_id": pred_json["request_id"],
            "actual_duration": pred_json["predicted_duration_class"],
            "unit": "minutes",
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        actual = client.post("/actual", json=actual_payload)
        actual.raise_for_status()
        print("actual:", actual.json())

        update_metrics()
        metrics = client.get("/metrics")
        metrics.raise_for_status()
        body = metrics.text
        required = [
            "denttime_prediction_requests_total",
            "denttime_logged_predictions_total",
            "denttime_macro_f1",
            "denttime_feature_psi",
        ]
        for token in required:
            assert token in body, f"missing metric: {token}"
        print("metrics: ok")


if __name__ == "__main__":
    main()
