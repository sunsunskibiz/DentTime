from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import get_conn
from app.main import app


def prediction_count() -> int:
    conn = get_conn()
    try:
        return int(conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0])
    finally:
        conn.close()


def main() -> None:
    with TestClient(app) as client:
        options = client.get("/options")
        options.raise_for_status()
        data = options.json()

        first_treatment = data["treatments"][0]["treatment"]
        first_doctor = data["doctors"][0]["id"] if data["doctors"] else None
        first_clinic = data["clinics"][0]["id"]

        before = prediction_count()

        predict_payload = {
            "treatmentSymptoms": first_treatment,
            "toothNumbers": "16",
            "surfaces": "none",
            "selectedDateTime": "2026-04-27T09:30",
            "totalAmount": 1200,
            "doctorId": first_doctor,
            "clinicId": first_clinic,
            "notes": "smoke test",
            "request_time": "2026-04-27T02:30:00Z",
        }

        pred = client.post("/predict", json=predict_payload)
        pred.raise_for_status()
        pred_json = pred.json()
        print("predict:", pred_json)

        after = prediction_count()
        assert after == before + 1, f"prediction log count did not increase: before={before}, after={after}"

        actual_payload = {
            "request_id": pred_json["request_id"],
            "actual_duration": pred_json["predicted_duration_class"],
            "unit": "minutes",
            "completed_at": "2026-04-27T03:00:00Z",
        }
        actual = client.post("/actual", json=actual_payload)
        actual.raise_for_status()
        print("actual:", actual.json())

        metrics = client.get("/metrics")
        metrics.raise_for_status()
        body = metrics.text
        required = [
            "denttime_logged_predictions_total",
            "denttime_macro_f1",
            "denttime_feature_psi",
        ]
        for token in required:
            assert token in body, f"missing metric: {token}"
        print("metrics: ok")
        print(f"logged_predictions_total increased from {before} to {after}")


if __name__ == "__main__":
    main()
