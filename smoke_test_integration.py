from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE_URL = os.getenv("DENTTIME_API_URL", "http://127.0.0.1:8000").rstrip("/")
TIMEOUT_SECONDS = float(os.getenv("DENTTIME_SMOKE_TIMEOUT", "120"))


def _request(method: str, path: str, payload: dict[str, Any] | None = None) -> tuple[int, str]:
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = Request(f"{BASE_URL}{path}", data=body, headers=headers, method=method)
    try:
        with urlopen(req, timeout=20) as resp:
            return resp.status, resp.read().decode("utf-8")
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} failed with HTTP {exc.code}: {error_body}") from exc
    except URLError as exc:
        raise RuntimeError(f"{method} {path} failed: {exc.reason}") from exc


def _json_request(method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    _, text = _request(method, path, payload)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{method} {path} did not return valid JSON: {text[:500]}") from exc


def _wait_until_ready() -> None:
    deadline = time.time() + TIMEOUT_SECONDS
    last_error: Exception | None = None

    while time.time() < deadline:
        try:
            root = _json_request("GET", "/")
            print(f"api ready: {root}", flush=True)
            return
        except Exception as exc:  # API may still be starting.
            last_error = exc
            time.sleep(2)

    raise RuntimeError(f"API was not ready within {TIMEOUT_SECONDS:.0f}s. Last error: {last_error}")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> None:
    print(f"smoke test target: {BASE_URL}", flush=True)
    _wait_until_ready()

    options = _json_request("GET", "/options")
    treatments = options.get("treatments", [])
    clinics = options.get("clinics", [])
    doctors = options.get("doctors", [])

    _require(bool(treatments), "/options returned no treatments")
    _require(bool(clinics), "/options returned no clinics")

    treatment = treatments[0]["treatment"]
    clinic_id = clinics[0]["id"]
    doctor_id = doctors[0]["id"] if doctors else None

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

    pred_json = _json_request("POST", "/predict", predict_payload)
    print("predict:", pred_json, flush=True)

    _require("request_id" in pred_json, "/predict response has no request_id")
    _require("predicted_duration_class" in pred_json, "/predict response has no predicted_duration_class")

    actual_payload = {
        "request_id": pred_json["request_id"],
        "actual_duration": pred_json["predicted_duration_class"],
        "unit": "minutes",
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    actual_json = _json_request("POST", "/actual", actual_payload)
    print("actual:", actual_json, flush=True)

    _, metrics_text = _request("GET", "/metrics")
    required_metrics = [
        "denttime_prediction_requests_total",
        "denttime_logged_predictions_total",
        "denttime_macro_f1",
        "denttime_feature_psi",
    ]
    missing = [name for name in required_metrics if name not in metrics_text]
    _require(not missing, f"/metrics is missing expected metrics: {missing}")

    print("metrics: ok", flush=True)
    print("smoke test: PASS", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"smoke test: FAIL - {exc}", file=sys.stderr, flush=True)
        sys.exit(1)
