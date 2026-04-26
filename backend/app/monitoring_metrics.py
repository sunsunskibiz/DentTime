from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

from app.db import get_conn, init_db

router = APIRouter(tags=["monitoring"])

BASELINE_PATH = Path("artifacts/baseline_metrics.json")
STATE_PATH = Path("monitoring/state.json")

REQUEST_COUNT = Counter("denttime_prediction_requests_total", "Total prediction requests")
REQUEST_LATENCY = Histogram("denttime_prediction_latency_seconds", "Prediction latency in seconds")
FEATURE_PSI = Gauge("denttime_feature_psi", "PSI by monitored feature", ["feature"])
OUTPUT_RATIO = Gauge("denttime_prediction_class_ratio", "Prediction ratio per class", ["slot_minutes"])
LOGGED_PREDICTIONS_TOTAL = Gauge("denttime_logged_predictions_total", "Total prediction records stored in SQLite")
LABELED_PREDICTIONS_TOTAL = Gauge("denttime_labeled_predictions_total", "Total predictions that already have actual outcomes")
MACRO_F1 = Gauge("denttime_macro_f1", "Current rolling macro F1")
MAE_MIN = Gauge("denttime_mae_minutes", "Current rolling MAE in minutes")
UNDER_RATE = Gauge("denttime_underestimation_rate", "Current under-estimation rate")
BASELINE_F1 = Gauge("denttime_macro_f1_baseline", "Offline baseline macro F1")
BASELINE_UNDER = Gauge("denttime_underestimation_rate_baseline", "Offline baseline under-estimation rate")
MISSING_RATE = Gauge("denttime_input_missing_rate", "Recent input missing rate")


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    return json.loads(text) if text else {}


@router.get("/metrics")
def metrics() -> Response:
    """Expose model/API monitoring signals in Prometheus text format."""
    init_db()

    baseline = load_json(BASELINE_PATH)
    state = load_json(STATE_PATH)

    if "macro_f1" in baseline:
        BASELINE_F1.set(float(baseline["macro_f1"]))
    under_baseline = baseline.get("under_estimation_rate", baseline.get("underestimation_rate"))
    if under_baseline is not None:
        BASELINE_UNDER.set(float(under_baseline))

    for feature, value in state.get("feature_psi", {}).items():
        FEATURE_PSI.labels(feature=str(feature)).set(float(value))
    for slot, value in state.get("prediction_ratio", {}).items():
        OUTPUT_RATIO.labels(slot_minutes=str(slot)).set(float(value))

    if "macro_f1" in state:
        MACRO_F1.set(float(state["macro_f1"]))
    if "mae_minutes" in state:
        MAE_MIN.set(float(state["mae_minutes"]))
    under_rate = state.get("under_estimation_rate", state.get("underestimation_rate"))
    if under_rate is not None:
        UNDER_RATE.set(float(under_rate))
    if "input_missing_rate" in state:
        MISSING_RATE.set(float(state["input_missing_rate"]))

    conn = get_conn()
    total_predictions = conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
    total_labeled = conn.execute("SELECT COUNT(*) FROM predictions WHERE actual_slot IS NOT NULL").fetchone()[0]
    conn.close()
    LOGGED_PREDICTIONS_TOTAL.set(float(total_predictions))
    LABELED_PREDICTIONS_TOTAL.set(float(total_labeled))

    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
