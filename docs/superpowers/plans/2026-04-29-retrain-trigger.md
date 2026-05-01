# Retrain Trigger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire Prometheus alerts → Alertmanager → FastAPI webhook → Airflow pipeline, with human-in-the-loop escalation when new data is unavailable.

**Architecture:** A new `retrain_trigger` FastAPI service receives Alertmanager webhooks, gates on data freshness, and either emails the engineer (WAITING) or fires the Feature Engineering → Retrain DAG chain. Airflow Dataset events keep the DAGs loosely coupled. All DAG-to-DAG sequencing uses Airflow's native Dataset scheduling.

**Tech Stack:** Python 3.11 · FastAPI · httpx (async HTTP) · aiosmtplib (async SMTP) · pytest-asyncio · Apache Airflow 2.4+ Datasets API · Alertmanager v0.27 · Docker Compose

---

## File Map

| File | Status | Responsibility |
|---|---|---|
| `retrain_trigger/requirements.txt` | CREATE | Pinned deps for FastAPI service container |
| `retrain_trigger/Dockerfile` | CREATE | python:3.11-slim image, port 5001 |
| `retrain_trigger/main.py` | CREATE | FastAPI app: /health, /alert endpoint with full state machine |
| `alertmanager/alertmanager.yml` | CREATE | Route all 4 DentTime alerts explicitly |
| `airflow/dags/datasets.py` | CREATE | Shared `RAW_DATA` and `FEATURES_TRAIN` Dataset constants |
| `airflow/dags/denttime_await_data_dag.py` | CREATE | FileSensor DAG: wait 24 h for data/raw/data.csv, then chain to FE |
| `airflow/dags/feature_engineering_dag.py` | MODIFY | Add `schedule=[RAW_DATA]`, `max_active_runs=1`, `outlets=[FEATURES_TRAIN]` |
| `airflow/dags/denttime_retrain_dag.py` | MODIFY | Add `schedule=[FEATURES_TRAIN]`, success/failure email callbacks |
| `prometheus/prometheus.yml` | MODIFY | Add `evaluation_interval: 15s` + alerting block |
| `docker-compose.yml` | MODIFY | Add `alertmanager` + `retrain_trigger` services to serving profile |
| `.env` | MODIFY | Append SMTP credentials (already gitignored) |
| `requirements-fe.txt` | MODIFY | Add pytest-asyncio, httpx for test runner |
| `tests/test_retrain_trigger.py` | CREATE | Unit tests for FastAPI service (no Docker) |
| `tests/dags/test_denttime_await_data_dag.py` | CREATE | AST-based DAG structure tests |
| `tests/dags/test_modified_dags.py` | CREATE | AST-based tests for Dataset wiring on modified DAGs |
| `tests/integration/__init__.py` | CREATE | Empty package marker |
| `tests/integration/test_retrain_trigger_integration.py` | CREATE | Live-stack integration tests (`pytest.mark.integration`) |

---

## Task 1: Scaffold `retrain_trigger/` service

**Files:**
- Create: `retrain_trigger/requirements.txt`
- Create: `retrain_trigger/Dockerfile`
- Create: `retrain_trigger/main.py` (skeleton — imports + /health only)
- Modify: `requirements-fe.txt` (add test deps)

- [ ] **Step 1.1: Create `retrain_trigger/requirements.txt`**

```
fastapi==0.111.1
uvicorn[standard]==0.30.1
httpx==0.27.0
aiosmtplib==3.0.1
```

- [ ] **Step 1.2: Create `retrain_trigger/Dockerfile`**

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

EXPOSE 5001

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5001"]
```

- [ ] **Step 1.3: Create skeleton `retrain_trigger/main.py`**

```python
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from email.message import EmailMessage

import aiosmtplib
import httpx
from fastapi import FastAPI, Request

app = FastAPI(title="DentTime Retrain Trigger")
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

AIRFLOW_URL            = os.getenv("AIRFLOW_URL",            "http://host.docker.internal:8080")
AIRFLOW_USER           = os.getenv("AIRFLOW_USER",           "admin")
AIRFLOW_PASS           = os.getenv("AIRFLOW_PASS",           "admin")
MIN_RETRAIN_INTERVAL_S = int(os.getenv("MIN_RETRAIN_INTERVAL_S", "14400"))
RAW_DATA_PATH          = os.getenv("RAW_DATA_PATH",          "/opt/airflow/project/data/raw/data.csv")
FEATURES_PATH          = os.getenv("FEATURES_PATH",          "/opt/airflow/project/features/features_train.parquet")
SMTP_HOST              = os.getenv("SMTP_HOST",              "smtp.gmail.com")
SMTP_PORT              = int(os.getenv("SMTP_PORT",          "587"))
SMTP_USER              = os.getenv("SMTP_USER",              "")
SMTP_PASS              = os.getenv("SMTP_PASS",              "")
ENGINEER_EMAIL         = os.getenv("ENGINEER_EMAIL",         "")
STATE_JSON_PATH        = os.getenv("STATE_JSON_PATH",        "/opt/airflow/project/monitoring/state.json")

SKIP_ALERTS: set[str] = {"DentTimeMissingRateHigh"}

_last_trigger_ts: float = 0.0
_debounce_lock: asyncio.Lock = asyncio.Lock()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/alert")
async def receive_alert(request: Request):
    return {"status": "not_implemented"}
```

- [ ] **Step 1.4: Add test deps to `requirements-fe.txt`**

Append these two lines to the existing file:
```
pytest-asyncio>=0.23
httpx>=0.27.0
```

- [ ] **Step 1.5: Verify the skeleton imports cleanly**

```bash
cd retrain_trigger && pip install -r requirements.txt && python -c "from main import app; print('OK')" && cd ..
```

Expected: `OK`

- [ ] **Step 1.6: Commit scaffold**

```bash
git add retrain_trigger/ requirements-fe.txt
git commit -m "feat: scaffold retrain_trigger service (skeleton main.py)"
```

---

## Task 2: TDD `check_new_data_available()`

**Files:**
- Create: `tests/test_retrain_trigger.py`
- Modify: `retrain_trigger/main.py`

- [ ] **Step 2.1: Create `tests/test_retrain_trigger.py` with failing tests**

```python
import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

# Set env vars BEFORE importing main (module reads them at import time)
os.environ.update({
    "RAW_DATA_PATH":          "/tmp/denttime_test_raw.csv",
    "FEATURES_PATH":          "/tmp/denttime_test_features.parquet",
    "STATE_JSON_PATH":        "/tmp/denttime_test_state.json",
    "SMTP_HOST":              "smtp.example.com",
    "SMTP_PORT":              "587",
    "SMTP_USER":              "test@example.com",
    "SMTP_PASS":              "testpass",
    "ENGINEER_EMAIL":         "engineer@example.com",
    "AIRFLOW_URL":            "http://localhost:8080",
    "MIN_RETRAIN_INTERVAL_S": "14400",
})

sys.path.insert(0, str(Path(__file__).parent.parent / "retrain_trigger"))

import main as m
from main import InfrastructureError, app, check_new_data_available


# ── fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_module_state():
    """Reset debounce state and recreate lock for each test."""
    m._last_trigger_ts = 0.0
    m._debounce_lock = asyncio.Lock()
    yield
    m._last_trigger_ts = 0.0


@pytest.fixture
def data_paths(tmp_path):
    """Patch module-level path constants to use tmp_path files."""
    raw = tmp_path / "raw.csv"
    feat = tmp_path / "features.parquet"
    orig_raw, orig_feat = m.RAW_DATA_PATH, m.FEATURES_PATH
    m.RAW_DATA_PATH = str(raw)
    m.FEATURES_PATH = str(feat)
    yield raw, feat
    m.RAW_DATA_PATH = orig_raw
    m.FEATURES_PATH = orig_feat


# ── check_new_data_available ──────────────────────────────────────────────

def test_check_raises_infrastructure_error_when_raw_missing(data_paths):
    raw, _ = data_paths
    # raw does NOT exist
    with pytest.raises(InfrastructureError):
        check_new_data_available()


def test_check_returns_true_when_features_missing(data_paths):
    raw, feat = data_paths
    raw.touch()
    # feat does NOT exist → first time build → treat as new data
    assert check_new_data_available() is True


def test_check_returns_true_when_raw_newer(data_paths):
    import time
    raw, feat = data_paths
    feat.touch()
    time.sleep(0.01)
    raw.touch()
    assert check_new_data_available() is True


def test_check_returns_false_when_raw_not_newer(data_paths):
    import time
    raw, feat = data_paths
    raw.touch()
    time.sleep(0.01)
    feat.touch()
    assert check_new_data_available() is False
```

- [ ] **Step 2.2: Run tests — expect all 4 to fail with `NameError`**

```bash
pytest tests/test_retrain_trigger.py::test_check_raises_infrastructure_error_when_raw_missing \
       tests/test_retrain_trigger.py::test_check_returns_true_when_features_missing \
       tests/test_retrain_trigger.py::test_check_returns_true_when_raw_newer \
       tests/test_retrain_trigger.py::test_check_returns_false_when_raw_not_newer -v
```

Expected: 4 FAILED (`ImportError: cannot import name 'InfrastructureError'` or `check_new_data_available`)

- [ ] **Step 2.3: Add `InfrastructureError` and `check_new_data_available()` to `main.py`**

Add after the constants block (after `SKIP_ALERTS`):

```python
class InfrastructureError(Exception):
    """Raised when RAW_DATA_PATH does not exist — volume mount problem, not a data gap."""
    pass


def check_new_data_available() -> bool:
    """
    Returns True  — raw data is newer than features (or features never built).
    Returns False — raw is not newer than features (data gap).
    Raises InfrastructureError — RAW_DATA_PATH missing entirely (bad mount).
    """
    if not os.path.exists(RAW_DATA_PATH):
        raise InfrastructureError("raw_data_missing")
    if not os.path.exists(FEATURES_PATH):
        return True
    return os.path.getmtime(RAW_DATA_PATH) > os.path.getmtime(FEATURES_PATH)
```

- [ ] **Step 2.4: Run tests — expect all 4 to pass**

```bash
pytest tests/test_retrain_trigger.py -k "test_check" -v
```

Expected: 4 PASSED

- [ ] **Step 2.5: Commit**

```bash
git add retrain_trigger/main.py tests/test_retrain_trigger.py
git commit -m "feat: add check_new_data_available() with InfrastructureError"
```

---

## Task 3: TDD `send_email()` and `_read_metrics()`

**Files:**
- Modify: `tests/test_retrain_trigger.py`
- Modify: `retrain_trigger/main.py`

- [ ] **Step 3.1: Add failing tests to `tests/test_retrain_trigger.py`**

Append after the existing check tests:

```python
# ── send_email ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_email_calls_aiosmtplib(monkeypatch):
    sent = {}

    async def fake_send(msg, **kwargs):
        sent["subject"] = msg["Subject"]
        sent["to"] = msg["To"]

    monkeypatch.setattr("aiosmtplib.send", fake_send)
    from main import send_email
    await send_email("Test Subject", "Test body")
    assert sent["subject"] == "Test Subject"
    assert sent["to"] == "engineer@example.com"


# ── _read_metrics ─────────────────────────────────────────────────────────

def test_read_metrics_returns_dict_when_file_present(tmp_path):
    state = tmp_path / "state.json"
    state.write_text('{"macro_f1": 0.75, "under_estimation_rate": 0.1}')
    m.STATE_JSON_PATH = str(state)
    from main import _read_metrics
    result = _read_metrics()
    assert result["macro_f1"] == 0.75


def test_read_metrics_returns_empty_dict_when_missing(tmp_path):
    m.STATE_JSON_PATH = str(tmp_path / "nonexistent.json")
    from main import _read_metrics
    assert _read_metrics() == {}


def test_read_metrics_returns_empty_dict_when_corrupt(tmp_path):
    bad = tmp_path / "state.json"
    bad.write_text("not json {{")
    m.STATE_JSON_PATH = str(bad)
    from main import _read_metrics
    assert _read_metrics() == {}
```

- [ ] **Step 3.2: Run — expect failures**

```bash
pytest tests/test_retrain_trigger.py -k "email or metrics" -v
```

Expected: 4 FAILED (`ImportError`)

- [ ] **Step 3.3: Add helpers to `main.py`**

Add after `check_new_data_available()`:

```python
def _read_metrics() -> dict:
    try:
        with open(STATE_JSON_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


async def send_email(subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = SMTP_USER
    msg["To"] = ENGINEER_EMAIL
    msg["Subject"] = subject
    msg.set_content(body)
    await aiosmtplib.send(
        msg,
        hostname=SMTP_HOST,
        port=SMTP_PORT,
        username=SMTP_USER,
        password=SMTP_PASS,
        start_tls=True,
    )
```

- [ ] **Step 3.4: Run — expect all to pass**

```bash
pytest tests/test_retrain_trigger.py -k "email or metrics" -v
```

Expected: 4 PASSED

- [ ] **Step 3.5: Commit**

```bash
git add retrain_trigger/main.py tests/test_retrain_trigger.py
git commit -m "feat: add send_email() and _read_metrics() helpers"
```

---

## Task 4: TDD `/alert` — steps [A] and [B] (filter + data gate)

**Files:**
- Modify: `tests/test_retrain_trigger.py`
- Modify: `retrain_trigger/main.py`

Shared payload constants — add near the top of the test file (after the imports):

```python
MACRO_F1_PAYLOAD = {
    "alerts": [{"status": "firing", "labels": {"alertname": "DentTimeMacroF1Drop", "severity": "critical"}}]
}
MISSING_RATE_PAYLOAD = {
    "alerts": [{"status": "firing", "labels": {"alertname": "DentTimeMissingRateHigh", "severity": "warning"}}]
}
RESOLVED_PAYLOAD = {
    "alerts": [{"status": "resolved", "labels": {"alertname": "DentTimeMacroF1Drop", "severity": "critical"}}]
}
```

- [ ] **Step 4.1: Add failing tests for steps [A] and [B]**

Append to `tests/test_retrain_trigger.py`:

```python
# ── /alert step [A]: filter ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_alert_skipped_all_in_skip_alerts():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/alert", json=MISSING_RATE_PAYLOAD)
    assert r.status_code == 200
    assert r.json() == {"status": "skipped", "reason": "no_retrain_worthy_alerts"}


@pytest.mark.asyncio
async def test_alert_skipped_when_all_resolved():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/alert", json=RESOLVED_PAYLOAD)
    assert r.status_code == 200
    assert r.json()["status"] == "skipped"


# ── /alert step [B]: data gate ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_alert_skipped_raw_data_missing(data_paths):
    raw, _ = data_paths
    # raw does NOT exist
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/alert", json=MACRO_F1_PAYLOAD)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "skipped"
    assert body["reason"] == "raw_data_missing"


@pytest.mark.asyncio
async def test_alert_waiting_when_no_new_data(data_paths):
    import time
    raw, feat = data_paths
    raw.touch()
    time.sleep(0.01)
    feat.touch()   # feat is newer → no new data

    with patch("main.send_email", new_callable=AsyncMock) as mock_email, \
         patch("main._trigger_dag", new_callable=AsyncMock) as mock_trigger:
        mock_trigger.return_value = {"dag_run_id": "await-run-123"}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/alert", json=MACRO_F1_PAYLOAD)

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "waiting"
    assert body["reason"] == "no_new_data"
    assert "DentTimeMacroF1Drop" in body["alerts"]
    mock_email.assert_called_once()
    mock_trigger.assert_called_once_with("denttime_await_data", conf={"alerts": ["DentTimeMacroF1Drop"]})


@pytest.mark.asyncio
async def test_alert_waiting_email_contains_alert_name(data_paths):
    import time
    raw, feat = data_paths
    raw.touch()
    time.sleep(0.01)
    feat.touch()

    captured = {}

    async def capture_email(subject, body):
        captured["subject"] = subject
        captured["body"] = body

    with patch("main.send_email", side_effect=capture_email), \
         patch("main._trigger_dag", new_callable=AsyncMock) as mock_trigger:
        mock_trigger.return_value = {"dag_run_id": "x"}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/alert", json=MACRO_F1_PAYLOAD)

    assert "DentTimeMacroF1Drop" in captured["body"]
    assert "[DentTime]" in captured["subject"]


@pytest.mark.asyncio
async def test_alert_waiting_email_sent_even_when_state_json_missing(data_paths, tmp_path):
    import time
    raw, feat = data_paths
    raw.touch()
    time.sleep(0.01)
    feat.touch()

    m.STATE_JSON_PATH = str(tmp_path / "nonexistent.json")
    captured = {}

    async def capture_email(subject, body):
        captured["body"] = body

    with patch("main.send_email", side_effect=capture_email), \
         patch("main._trigger_dag", new_callable=AsyncMock) as mock_trigger:
        mock_trigger.return_value = {"dag_run_id": "x"}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/alert", json=MACRO_F1_PAYLOAD)

    assert "metrics unavailable" in captured["body"]
```

- [ ] **Step 4.2: Run — expect failures**

```bash
pytest tests/test_retrain_trigger.py -k "alert_skipped or alert_waiting" -v
```

Expected: all FAILED (endpoint returns `not_implemented`)

- [ ] **Step 4.3: Implement steps [A] and [B] in `receive_alert()`**

Replace the `receive_alert` stub in `main.py`:

```python
@app.post("/alert")
async def receive_alert(request: Request):
    global _last_trigger_ts
    payload = await request.json()

    # [A] Filter SKIP_ALERTS — only process retrain-worthy firing alerts
    firing_alerts = [
        a["labels"]["alertname"]
        for a in payload.get("alerts", [])
        if a.get("status") == "firing" and a["labels"].get("alertname") not in SKIP_ALERTS
    ]
    if not firing_alerts:
        return {"status": "skipped", "reason": "no_retrain_worthy_alerts"}

    # [B] Data freshness gate
    try:
        has_new_data = check_new_data_available()
    except InfrastructureError:
        log.error("RAW_DATA_PATH %s missing — check volume mount", RAW_DATA_PATH)
        return {"status": "skipped", "reason": "raw_data_missing"}

    if not has_new_data:
        metrics = _read_metrics()
        if metrics:
            metrics_str = (
                f"macro_f1={metrics.get('macro_f1', 'N/A')}, "
                f"under_estimation_rate={metrics.get('under_estimation_rate', 'N/A')}"
            )
        else:
            metrics_str = "metrics unavailable"
        body = (
            f"Alerts firing: {', '.join(firing_alerts)}\n"
            f"Current metrics: {metrics_str}\n\n"
            "Action required: run the data collection pipeline to update data/raw/data.csv.\n"
            "The denttime_await_data DAG will trigger Feature Engineering automatically once\n"
            "the file is updated.\n\n"
            f"Airflow UI: http://localhost:8080/dags/denttime_await_data\n"
            "Grafana: http://localhost:3000"
        )
        try:
            await send_email(
                subject="[DentTime] Model alert — data collection needed before retrain",
                body=body,
            )
        except Exception as exc:
            log.error("Failed to send WAITING email: %s", exc)
        try:
            await _trigger_dag("denttime_await_data", conf={"alerts": firing_alerts})
        except Exception as exc:
            log.error("Failed to trigger denttime_await_data: %s", exc)
        return {"status": "waiting", "reason": "no_new_data", "alerts": firing_alerts}

    # [C]–[F] implemented in Task 5
    return {"status": "not_implemented"}
```

- [ ] **Step 4.4: Run — expect all to pass**

```bash
pytest tests/test_retrain_trigger.py -k "alert_skipped or alert_waiting" -v
```

Expected: all PASSED

- [ ] **Step 4.5: Commit**

```bash
git add retrain_trigger/main.py tests/test_retrain_trigger.py
git commit -m "feat: implement /alert steps A+B (filter + data gate)"
```

---

## Task 5: TDD `/alert` — steps [C]–[F] (debounce + trigger + polling)

**Files:**
- Modify: `tests/test_retrain_trigger.py`
- Modify: `retrain_trigger/main.py`

- [ ] **Step 5.1: Add failing tests**

Append to `tests/test_retrain_trigger.py`:

```python
# ── /alert step [C]: debounce ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_alert_debounced_within_interval(data_paths):
    import time
    raw, feat = data_paths
    raw.touch()
    time.sleep(0.01)
    feat.touch()
    # Simulate a retrain triggered 10 minutes ago
    m._last_trigger_ts = time.time() - 600
    m.MIN_RETRAIN_INTERVAL_S = 14400

    # Make raw newer than feat for this test
    time.sleep(0.01)
    raw.touch()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/alert", json=MACRO_F1_PAYLOAD)
    assert r.json()["status"] == "debounced"
    assert "retry_in_minutes" in r.json()


@pytest.mark.asyncio
async def test_alert_concurrent_requests_one_triggered_one_debounced(data_paths):
    import time
    raw, feat = data_paths
    raw.touch()
    time.sleep(0.01)
    feat.touch()
    time.sleep(0.01)
    raw.touch()   # raw newest

    with patch("main._trigger_dag", new_callable=AsyncMock) as mock_trigger, \
         patch("main._get_dag_run_state", new_callable=AsyncMock) as mock_state, \
         patch("main.send_email", new_callable=AsyncMock), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        mock_trigger.return_value = {"dag_run_id": "run-abc"}
        mock_state.return_value = {"state": "success"}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r1, r2 = await asyncio.gather(
                client.post("/alert", json=MACRO_F1_PAYLOAD),
                client.post("/alert", json=MACRO_F1_PAYLOAD),
            )

    statuses = {r1.json()["status"], r2.json()["status"]}
    assert "debounced" in statuses
    assert len(statuses) == 2   # one triggered/error, one debounced


# ── /alert steps [D]–[F]: trigger + polling ───────────────────────────────

@pytest.mark.asyncio
async def test_alert_triggered_when_new_data_available(data_paths):
    import time
    raw, feat = data_paths
    raw.touch()
    time.sleep(0.01)
    feat.touch()
    time.sleep(0.01)
    raw.touch()

    with patch("main._trigger_dag", new_callable=AsyncMock) as mock_trigger, \
         patch("main._get_dag_run_state", new_callable=AsyncMock) as mock_state, \
         patch("asyncio.sleep", new_callable=AsyncMock):
        mock_trigger.side_effect = [
            {"dag_run_id": "fe-run-111"},     # first call: trigger FE
            {"dag_run_id": "retrain-run-222"}, # second call: trigger retrain
        ]
        mock_state.return_value = {"state": "success"}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/alert", json=MACRO_F1_PAYLOAD)

    body = r.json()
    assert body["status"] == "triggered"
    assert body["dag_run_id"] == "retrain-run-222"
    assert body["fe_run_id"] == "fe-run-111"
    assert "DentTimeMacroF1Drop" in body["alerts"]


@pytest.mark.asyncio
async def test_alert_error_airflow_unreachable(data_paths):
    import time
    raw, feat = data_paths
    raw.touch(); time.sleep(0.01); feat.touch(); time.sleep(0.01); raw.touch()

    with patch("main._trigger_dag", side_effect=httpx.ConnectError("refused")):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/alert", json=MACRO_F1_PAYLOAD)

    assert r.json()["status"] == "error"
    assert r.json()["reason"] == "airflow_unreachable"


@pytest.mark.asyncio
async def test_alert_error_airflow_bad_response(data_paths):
    import time
    raw, feat = data_paths
    raw.touch(); time.sleep(0.01); feat.touch(); time.sleep(0.01); raw.touch()

    with patch("main._trigger_dag", new_callable=AsyncMock) as mock_trigger:
        mock_trigger.return_value = {"detail": "DAG not found"}   # no dag_run_id
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/alert", json=MACRO_F1_PAYLOAD)

    assert r.json()["status"] == "error"
    assert r.json()["reason"] == "airflow_bad_response"


@pytest.mark.asyncio
async def test_alert_error_feature_engineering_failed(data_paths):
    import time
    raw, feat = data_paths
    raw.touch(); time.sleep(0.01); feat.touch(); time.sleep(0.01); raw.touch()

    with patch("main._trigger_dag", new_callable=AsyncMock) as mock_trigger, \
         patch("main._get_dag_run_state", new_callable=AsyncMock) as mock_state, \
         patch("main.send_email", new_callable=AsyncMock), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        mock_trigger.return_value = {"dag_run_id": "fe-run-fail"}
        mock_state.return_value = {"state": "failed"}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/alert", json=MACRO_F1_PAYLOAD)

    assert r.json() == {"status": "error", "reason": "feature_engineering_failed"}


@pytest.mark.asyncio
async def test_alert_error_feature_engineering_timeout(data_paths):
    import time
    raw, feat = data_paths
    raw.touch(); time.sleep(0.01); feat.touch(); time.sleep(0.01); raw.touch()

    with patch("main._trigger_dag", new_callable=AsyncMock) as mock_trigger, \
         patch("main._get_dag_run_state", new_callable=AsyncMock) as mock_state, \
         patch("main.send_email", new_callable=AsyncMock), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        mock_trigger.return_value = {"dag_run_id": "fe-run-timeout"}
        mock_state.return_value = {"state": "running"}   # never succeeds
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/alert", json=MACRO_F1_PAYLOAD)

    assert r.json() == {"status": "error", "reason": "feature_engineering_timeout"}
```

- [ ] **Step 5.2: Run — expect failures**

```bash
pytest tests/test_retrain_trigger.py -k "debounced or triggered or airflow" -v
```

Expected: all FAILED (endpoint still returns `not_implemented`)

- [ ] **Step 5.3: Add HTTP helpers and complete `receive_alert()` in `main.py`**

Add the two helper functions before `receive_alert()`:

```python
async def _trigger_dag(dag_id: str, conf: dict) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{AIRFLOW_URL}/api/v1/dags/{dag_id}/dagRuns",
            json={"conf": conf},
            auth=(AIRFLOW_USER, AIRFLOW_PASS),
            timeout=10,
        )
        r.raise_for_status()
    return r.json()


async def _get_dag_run_state(dag_id: str, run_id: str) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{AIRFLOW_URL}/api/v1/dags/{dag_id}/dagRuns/{run_id}",
            auth=(AIRFLOW_USER, AIRFLOW_PASS),
            timeout=10,
        )
        r.raise_for_status()
    return r.json()


async def _send_pipeline_error_email(dag_id: str, run_id: str) -> None:
    body = (
        f"DAG {dag_id} failed.\n"
        f"Run ID: {run_id}\n"
        f"Airflow UI: http://localhost:8080/dags/{dag_id}/grid?dag_run_id={run_id}\n\n"
        "Action required: check Airflow task logs and investigate the failure."
    )
    try:
        await send_email(
            subject="[DentTime] Retrain pipeline failed — manual review needed",
            body=body,
        )
    except Exception as exc:
        log.error("Failed to send pipeline error email: %s", exc)
```

Now replace the `return {"status": "not_implemented"}` at the end of `receive_alert()` with steps [C]–[F]:

```python
    # [C] Debounce — atomic check-and-stamp, lock released before any I/O
    async with _debounce_lock:
        now = time.time()
        if now - _last_trigger_ts < MIN_RETRAIN_INTERVAL_S:
            retry_min = int((MIN_RETRAIN_INTERVAL_S - (now - _last_trigger_ts)) / 60)
            return {"status": "debounced", "retry_in_minutes": retry_min}
        _last_trigger_ts = time.time()
    # Lock released here — polling runs outside the lock

    # [D] Trigger Feature Engineering DAG
    try:
        fe_result = await _trigger_dag("denttime_feature_engineering", conf={})
    except (httpx.HTTPError, httpx.ConnectError) as exc:
        return {"status": "error", "reason": "airflow_unreachable", "detail": str(exc)}
    if "dag_run_id" not in fe_result:
        return {"status": "error", "reason": "airflow_bad_response", "detail": fe_result.get("detail")}
    fe_run_id = fe_result["dag_run_id"]

    # [E] Poll Feature Engineering (max 10 min)
    for _ in range(60):
        await asyncio.sleep(10)
        try:
            state = (await _get_dag_run_state("denttime_feature_engineering", fe_run_id)).get("state")
        except Exception as exc:
            log.warning("Poll error (will retry): %s", exc)
            continue
        if state == "success":
            break
        if state == "failed":
            await _send_pipeline_error_email("denttime_feature_engineering", fe_run_id)
            return {"status": "error", "reason": "feature_engineering_failed"}
    else:
        await _send_pipeline_error_email("denttime_feature_engineering", fe_run_id)
        return {"status": "error", "reason": "feature_engineering_timeout"}

    # [F] Trigger ML Retrain DAG
    try:
        retrain_result = await _trigger_dag("denttime_retrain", conf={})
    except (httpx.HTTPError, httpx.ConnectError) as exc:
        return {"status": "error", "reason": "retrain_trigger_failed", "detail": str(exc)}
    if "dag_run_id" not in retrain_result:
        return {"status": "error", "reason": "airflow_bad_response", "detail": retrain_result.get("detail")}
    return {
        "status": "triggered",
        "dag_run_id": retrain_result["dag_run_id"],
        "fe_run_id": fe_run_id,
        "alerts": firing_alerts,
    }
```

- [ ] **Step 5.4: Run the full unit test suite**

```bash
pytest tests/test_retrain_trigger.py -v
```

Expected: all PASSED. If the concurrent test is flaky, run it 3 times — intermittent failure is a genuine race condition bug, not a test issue.

- [ ] **Step 5.5: Commit**

```bash
git add retrain_trigger/main.py tests/test_retrain_trigger.py
git commit -m "feat: complete /alert endpoint (debounce + trigger + polling)"
```

---

## Task 6: `airflow/dags/datasets.py`

**Files:**
- Create: `airflow/dags/datasets.py`

No unit tests for a constants file — correctness verified by DAG import tests in Task 8/9.

- [ ] **Step 6.1: Create `airflow/dags/datasets.py`**

```python
from airflow import Dataset

RAW_DATA       = Dataset("file:///opt/airflow/project/data/raw/data.csv")
FEATURES_TRAIN = Dataset("file:///opt/airflow/project/features/features_train.parquet")
```

- [ ] **Step 6.2: Commit**

```bash
git add airflow/dags/datasets.py
git commit -m "feat: add Airflow Dataset constants for event-driven DAG scheduling"
```

---

## Task 7: Modify `airflow/dags/feature_engineering_dag.py`

**Files:**
- Modify: `airflow/dags/feature_engineering_dag.py`

Three changes: import datasets, change `schedule`, add `max_active_runs`, add `outlets` on `transform_train`.

- [ ] **Step 7.1: Add the import**

Find the line `from airflow.operators.python import PythonOperator` and add after it:

```python
from datasets import RAW_DATA, FEATURES_TRAIN
```

- [ ] **Step 7.2: Update the DAG definition**

Find (lines 174–180):
```python
with DAG(
    dag_id="denttime_feature_engineering",
    schedule=None,
    start_date=days_ago(1),
    catchup=False,
    tags=["feature-engineering"],
) as dag:
```

Replace with:
```python
with DAG(
    dag_id="denttime_feature_engineering",
    schedule=[RAW_DATA],
    start_date=days_ago(1),
    catchup=False,
    max_active_runs=1,
    tags=["feature-engineering"],
) as dag:
```

- [ ] **Step 7.3: Add Dataset outlet to `transform_train`**

Find (lines 203–206):
```python
    transform_train = PythonOperator(
        task_id="task_transform_train",
        python_callable=_task_transform_train,
    )
```

Replace with:
```python
    transform_train = PythonOperator(
        task_id="task_transform_train",
        python_callable=_task_transform_train,
        outlets=[FEATURES_TRAIN],
    )
```

- [ ] **Step 7.4: Verify syntax**

```bash
python -c "import ast; ast.parse(open('airflow/dags/feature_engineering_dag.py').read()); print('OK')"
```

Expected: `OK`

- [ ] **Step 7.5: Commit**

```bash
git add airflow/dags/feature_engineering_dag.py
git commit -m "feat: add Dataset schedule and FEATURES_TRAIN outlet to feature_engineering_dag"
```

---

## Task 8: Create `airflow/dags/denttime_await_data_dag.py`

**Files:**
- Create: `airflow/dags/denttime_await_data_dag.py`

- [ ] **Step 8.1: Create the file**

```python
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path

from airflow import DAG
from airflow.operators.trigger_dagrun import TriggerDagRunOperator
from airflow.sensors.filesystem import FileSensor
from airflow.utils.dates import days_ago

SMTP_HOST      = os.getenv("SMTP_HOST",      "smtp.gmail.com")
SMTP_PORT      = int(os.getenv("SMTP_PORT",  "587"))
SMTP_USER      = os.getenv("SMTP_USER",      "")
SMTP_PASS      = os.getenv("SMTP_PASS",      "")
ENGINEER_EMAIL = os.getenv("ENGINEER_EMAIL", "")


def on_await_data_timeout(context: dict) -> None:
    msg = EmailMessage()
    msg["Subject"] = "[DentTime] Data wait timed out — 24h sensor expired"
    msg["From"] = SMTP_USER
    msg["To"] = ENGINEER_EMAIL
    msg.set_content(
        "The denttime_await_data DAG timed out after 24 h waiting for "
        "data/raw/data.csv to update.\n"
        f"DAG run: {context['dag_run'].run_id}\n"
        "Action required: supply new data and manually trigger denttime_feature_engineering."
    )
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(SMTP_USER, SMTP_PASS)
        smtp.send_message(msg)


with DAG(
    dag_id="denttime_await_data",
    schedule=None,
    start_date=days_ago(1),
    catchup=False,
    max_active_runs=1,
    on_failure_callback=on_await_data_timeout,
    tags=["monitoring", "human-in-loop", "sensor"],
) as dag:

    wait_for_raw_data = FileSensor(
        task_id="wait_for_raw_data",
        filepath="/opt/airflow/project/data/raw/data.csv",
        poke_interval=300,
        timeout=86400,
        mode="reschedule",
    )

    trigger_feature_engineering = TriggerDagRunOperator(
        task_id="trigger_feature_engineering",
        trigger_dag_id="denttime_feature_engineering",
        wait_for_completion=False,
        conf="{{dag_run.conf}}",
    )

    wait_for_raw_data >> trigger_feature_engineering
```

- [ ] **Step 8.2: Verify syntax**

```bash
python -c "import ast; ast.parse(open('airflow/dags/denttime_await_data_dag.py').read()); print('OK')"
```

Expected: `OK`

- [ ] **Step 8.3: Commit**

```bash
git add airflow/dags/denttime_await_data_dag.py
git commit -m "feat: add denttime_await_data DAG (FileSensor + TriggerDagRunOperator)"
```

---

## Task 9: Modify `airflow/dags/denttime_retrain_dag.py`

**Files:**
- Modify: `airflow/dags/denttime_retrain_dag.py`

Add `FEATURES_TRAIN` schedule, SMTP env vars, and two DAG-level callbacks.

- [ ] **Step 9.1: Add imports and env vars at the top of the file**

After the existing imports block (after `from airflow.utils.dates import days_ago`), add:

```python
import os
import smtplib
from email.message import EmailMessage
from datasets import FEATURES_TRAIN

SMTP_HOST      = os.getenv("SMTP_HOST",      "smtp.gmail.com")
SMTP_PORT      = int(os.getenv("SMTP_PORT",  "587"))
SMTP_USER      = os.getenv("SMTP_USER",      "")
SMTP_PASS      = os.getenv("SMTP_PASS",      "")
ENGINEER_EMAIL = os.getenv("ENGINEER_EMAIL", "")
```

- [ ] **Step 9.2: Add callback functions before the `with DAG(...)` block**

Add directly above `with DAG(`:

```python
def _smtp_send(msg: EmailMessage) -> None:
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(SMTP_USER, SMTP_PASS)
        smtp.send_message(msg)


def on_retrain_success(context: dict) -> None:
    import json
    dag_run = context["dag_run"]
    try:
        metrics_path = MODEL_DIR / "baseline_metrics.json"
        with open(metrics_path) as f:
            metrics = json.load(f)
        metrics_str = (
            f"macro_f1={metrics.get('macro_f1', 'N/A')}, "
            f"mae={metrics.get('mae', 'N/A')}, "
            f"feature_set={metrics.get('feature_set', 'N/A')}"
        )
    except Exception:
        metrics_str = "metrics unavailable"
    msg = EmailMessage()
    msg["Subject"] = "[DentTime] Retrain complete"
    msg["From"] = SMTP_USER
    msg["To"] = ENGINEER_EMAIL
    msg.set_content(
        f"DentTime model retrain completed successfully.\n"
        f"DAG run: {dag_run.run_id}\n"
        f"Metrics: {metrics_str}\n\n"
        f"Airflow UI: http://localhost:8080/dags/denttime_retrain\n"
        f"MLflow:     http://localhost:5008"
    )
    _smtp_send(msg)


def on_retrain_failure(context: dict) -> None:
    dag_run  = context["dag_run"]
    exc      = context.get("exception", "unknown error")
    msg = EmailMessage()
    msg["Subject"] = "[DentTime] Retrain rejected — new model below baseline"
    msg["From"] = SMTP_USER
    msg["To"] = ENGINEER_EMAIL
    msg.set_content(
        f"DentTime retrain DAG failed or was rejected.\n"
        f"DAG run: {dag_run.run_id}\n"
        f"Reason:  {exc}\n\n"
        f"Airflow UI: http://localhost:8080/dags/denttime_retrain/grid"
        f"?dag_run_id={dag_run.run_id}"
    )
    _smtp_send(msg)
```

- [ ] **Step 9.3: Update the DAG definition**

Find (line 362–370):
```python
with DAG(
    dag_id="denttime_retrain",
    schedule=None,
    start_date=days_ago(1),
    catchup=False,
    tags=["model-training", "retrain", "mlflow", "feature-ranking"],
    doc_md="""
```

Replace the opening arguments only (keep `doc_md` and everything else unchanged):
```python
with DAG(
    dag_id="denttime_retrain",
    schedule=[FEATURES_TRAIN],
    start_date=days_ago(1),
    catchup=False,
    on_success_callback=on_retrain_success,
    on_failure_callback=on_retrain_failure,
    tags=["model-training", "retrain", "mlflow", "feature-ranking"],
    doc_md="""
```

- [ ] **Step 9.4: Verify syntax**

```bash
python -c "import ast; ast.parse(open('airflow/dags/denttime_retrain_dag.py').read()); print('OK')"
```

Expected: `OK`

- [ ] **Step 9.5: Commit**

```bash
git add airflow/dags/denttime_retrain_dag.py
git commit -m "feat: add FEATURES_TRAIN schedule and email callbacks to denttime_retrain_dag"
```

---

## Task 10: DAG structure tests

**Files:**
- Create: `tests/dags/test_denttime_await_data_dag.py`
- Create: `tests/dags/test_modified_dags.py`

These use AST parsing — no Airflow import needed (same pattern as existing `test_feature_engineering_dag.py`).

- [ ] **Step 10.1: Create `tests/dags/test_denttime_await_data_dag.py`**

```python
import ast
import re
from pathlib import Path

DAG_PATH = Path("airflow/dags/denttime_await_data_dag.py")


def _source():
    return DAG_PATH.read_text()


def test_dag_file_exists():
    assert DAG_PATH.exists()


def test_dag_file_valid_syntax():
    ast.parse(_source())


def test_dag_id_is_correct():
    assert 'dag_id="denttime_await_data"' in _source()


def test_schedule_is_none():
    assert "schedule=None" in _source()


def test_max_active_runs_is_one():
    assert "max_active_runs=1" in _source()


def test_on_failure_callback_set():
    assert "on_failure_callback=on_await_data_timeout" in _source()


def test_has_file_sensor():
    assert "FileSensor" in _source()


def test_file_sensor_mode_is_reschedule():
    assert 'mode="reschedule"' in _source()


def test_file_sensor_timeout_is_86400():
    assert "timeout=86400" in _source()


def test_has_trigger_dag_run_operator():
    assert "TriggerDagRunOperator" in _source()


def test_trigger_target_is_feature_engineering():
    assert 'trigger_dag_id="denttime_feature_engineering"' in _source()


def test_wait_for_completion_is_false():
    assert "wait_for_completion=False" in _source()


def test_dependency_order():
    src = _source()
    sensor_pos  = src.index("wait_for_raw_data >> trigger_feature_engineering")
    assert sensor_pos > 0
```

- [ ] **Step 10.2: Run await_data DAG tests**

```bash
pytest tests/dags/test_denttime_await_data_dag.py -v
```

Expected: all PASSED

- [ ] **Step 10.3: Create `tests/dags/test_modified_dags.py`**

```python
import ast
from pathlib import Path

FE_PATH     = Path("airflow/dags/feature_engineering_dag.py")
RETRAIN_PATH = Path("airflow/dags/denttime_retrain_dag.py")
DATASETS_PATH = Path("airflow/dags/datasets.py")


def _src(path):
    return path.read_text()


# ── datasets.py ────────────────────────────────────────────────────────────

def test_datasets_file_exists():
    assert DATASETS_PATH.exists()


def test_datasets_defines_raw_data():
    assert "RAW_DATA" in _src(DATASETS_PATH)


def test_datasets_defines_features_train():
    assert "FEATURES_TRAIN" in _src(DATASETS_PATH)


# ── feature_engineering_dag.py ─────────────────────────────────────────────

def test_fe_imports_datasets():
    assert "from datasets import" in _src(FE_PATH)
    assert "RAW_DATA" in _src(FE_PATH)
    assert "FEATURES_TRAIN" in _src(FE_PATH)


def test_fe_schedule_is_raw_data():
    assert "schedule=[RAW_DATA]" in _src(FE_PATH)


def test_fe_max_active_runs_is_one():
    assert "max_active_runs=1" in _src(FE_PATH)


def test_fe_transform_train_has_outlets():
    src = _src(FE_PATH)
    # outlets=[FEATURES_TRAIN] must appear inside the transform_train PythonOperator block
    assert "outlets=[FEATURES_TRAIN]" in src


def test_fe_no_schedule_none():
    # schedule=None should no longer be present in the DAG definition
    src = _src(FE_PATH)
    # There must be no bare `schedule=None` in the DAG kwargs
    assert "schedule=None" not in src


# ── denttime_retrain_dag.py ────────────────────────────────────────────────

def test_retrain_imports_features_train():
    assert "from datasets import FEATURES_TRAIN" in _src(RETRAIN_PATH)


def test_retrain_schedule_is_features_train():
    assert "schedule=[FEATURES_TRAIN]" in _src(RETRAIN_PATH)


def test_retrain_has_success_callback():
    assert "on_success_callback=on_retrain_success" in _src(RETRAIN_PATH)


def test_retrain_has_failure_callback():
    assert "on_failure_callback=on_retrain_failure" in _src(RETRAIN_PATH)


def test_retrain_defines_success_callback_function():
    assert "def on_retrain_success" in _src(RETRAIN_PATH)


def test_retrain_defines_failure_callback_function():
    assert "def on_retrain_failure" in _src(RETRAIN_PATH)
```

- [ ] **Step 10.4: Run modified DAG tests**

```bash
pytest tests/dags/test_modified_dags.py -v
```

Expected: all PASSED

- [ ] **Step 10.5: Commit**

```bash
git add tests/dags/
git commit -m "test: add DAG structure tests for await_data and modified DAGs"
```

---

## Task 11: `alertmanager/alertmanager.yml`

**Files:**
- Create: `alertmanager/alertmanager.yml`

- [ ] **Step 11.1: Create `alertmanager/alertmanager.yml`**

```yaml
global:
  resolve_timeout: 5m

route:
  group_by: ['alertname']
  group_wait: 30s
  group_interval: 5m
  # repeat_interval MUST stay in sync with MIN_RETRAIN_INTERVAL_S=14400 in docker-compose.yml
  repeat_interval: 4h
  receiver: 'retrain-trigger'   # fallback; explicit routes below should match everything
  routes:
    # Critical alerts — retrain-worthy, send to webhook
    - match:
        alertname: DentTimeMacroF1Drop
      receiver: 'retrain-trigger'
    - match:
        alertname: DentTimeUnderEstimationHigh
      receiver: 'retrain-trigger'
    # Warning but retrain-worthy — must appear BEFORE the MissingRateHigh route
    - match:
        alertname: DentTimeFeatureDriftHigh
      receiver: 'retrain-trigger'
    # Warning and NOT retrain-worthy — silence (data quality issue, retrain won't help)
    - match:
        alertname: DentTimeMissingRateHigh
      receiver: 'null'

receivers:
  - name: 'null'
  - name: 'retrain-trigger'
    webhook_configs:
      - url: 'http://retrain_trigger:5001/alert'
        send_resolved: false

inhibit_rules:
  # Suppress warning-severity noise when a critical alert is already firing.
  # equal:[] means any active critical silences any warning regardless of label values.
  - source_match:
      severity: 'critical'
    target_match:
      severity: 'warning'
    equal: []
```

- [ ] **Step 11.2: Commit**

```bash
git add alertmanager/alertmanager.yml
git commit -m "feat: add Alertmanager config with explicit routing for all 4 DentTime alerts"
```

---

## Task 12: Modify `prometheus/prometheus.yml`

**Files:**
- Modify: `prometheus/prometheus.yml`

- [ ] **Step 12.1: Replace the file content**

Current content:
```yaml
global:
  scrape_interval: 15s

rule_files:
  - /etc/prometheus/alerts.yml

scrape_configs:
  - job_name: denttime_api
    metrics_path: /metrics
    static_configs:
      - targets: ["api:8000"]
```

New content:
```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

alerting:
  alertmanagers:
    - static_configs:
        - targets:
          - alertmanager:9093

rule_files:
  - /etc/prometheus/alerts.yml

scrape_configs:
  - job_name: denttime_api
    metrics_path: /metrics
    static_configs:
      - targets: ["api:8000"]
```

- [ ] **Step 12.2: Commit**

```bash
git add prometheus/prometheus.yml
git commit -m "feat: wire Prometheus to Alertmanager with 15s evaluation interval"
```

---

## Task 13: Modify `docker-compose.yml`

**Files:**
- Modify: `docker-compose.yml`

Two new services must be added to the `serving` profile.

- [ ] **Step 13.1: Add `alertmanager` service**

In `docker-compose.yml`, after the `prometheus:` service block and before `grafana:`, add:

```yaml
  alertmanager:
    image: prom/alertmanager:v0.27.0
    container_name: denttime_alertmanager
    profiles: [serving]
    ports:
      - "9093:9093"
    volumes:
      - ./alertmanager/alertmanager.yml:/etc/alertmanager/alertmanager.yml:ro
    depends_on:
      - prometheus
```

- [ ] **Step 13.2: Add `retrain_trigger` service**

After the `alertmanager` service block, add:

```yaml
  retrain_trigger:
    build: ./retrain_trigger
    container_name: denttime_retrain_trigger
    profiles: [serving]
    ports:
      - "5001:5001"
    extra_hosts:
      - "host.docker.internal:host-gateway"
    volumes:
      - ./:/opt/airflow/project:ro
    env_file:
      - .env
    environment:
      - AIRFLOW_URL=http://host.docker.internal:8080
      - AIRFLOW_USER=admin
      - AIRFLOW_PASS=admin
      - MIN_RETRAIN_INTERVAL_S=14400
      - RAW_DATA_PATH=/opt/airflow/project/data/raw/data.csv
      - FEATURES_PATH=/opt/airflow/project/features/features_train.parquet
      - SMTP_PORT=587
    depends_on:
      - alertmanager
```

`SMTP_HOST`, `SMTP_USER`, `SMTP_PASS`, `ENGINEER_EMAIL` are read from `.env` via `env_file`.

- [ ] **Step 13.3: Validate compose syntax**

```bash
make validate
```

Expected: exits 0 with no errors

- [ ] **Step 13.4: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: add alertmanager and retrain_trigger services to serving profile"
```

---

## Task 14: SMTP credentials in `.env`

**Files:**
- Modify: `.env`

- [ ] **Step 14.1: Append SMTP vars to `.env`**

Open `.env` (currently contains only DagsHub vars) and append:

```
SMTP_HOST=smtp.gmail.com
SMTP_USER=sunalert0383@gmail.com
SMTP_PASS=kfeh gmmo scnl gpoj
ENGINEER_EMAIL=6870038321@student.chula.ac.th
```

- [ ] **Step 14.2: Verify `.env` is in `.gitignore`**

```bash
grep "^\.env$" .gitignore
```

Expected: prints `.env`. If missing, add it: `echo '.env' >> .gitignore`

- [ ] **Step 14.3: Commit `.gitignore` only (never commit `.env`)**

```bash
git add .gitignore
git commit -m "chore: confirm .env in gitignore (SMTP secrets)"
```

---

## Task 15: Integration tests

**Files:**
- Create: `tests/integration/__init__.py`
- Create: `tests/integration/test_retrain_trigger_integration.py`
- Create: `tests/conftest.py`

- [ ] **Step 15.1: Create `tests/conftest.py`** (registers the integration marker)

```python
import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: mark test as integration test — requires `make up-serve` to be running",
    )
```

- [ ] **Step 15.2: Create `tests/integration/__init__.py`**

Empty file:
```python
```

- [ ] **Step 15.3: Create `tests/integration/test_retrain_trigger_integration.py`**

```python
"""
Integration tests — require the serving stack to be running:

    make up-serve        # or: docker compose --profile serving up -d

Run with:
    pytest tests/integration/ -m integration -v
"""
import pytest
import httpx

BASE_URL = "http://localhost:5001"

pytestmark = pytest.mark.integration

SKIP_PAYLOAD = {
    "alerts": [
        {
            "status": "firing",
            "labels": {"alertname": "DentTimeMissingRateHigh", "severity": "warning"},
        }
    ]
}

CRITICAL_PAYLOAD = {
    "alerts": [
        {
            "status": "firing",
            "labels": {"alertname": "DentTimeMacroF1Drop", "severity": "critical"},
        }
    ]
}

RESOLVED_PAYLOAD = {
    "alerts": [
        {
            "status": "resolved",
            "labels": {"alertname": "DentTimeMacroF1Drop", "severity": "critical"},
        }
    ]
}


def test_health_returns_ok():
    r = httpx.get(f"{BASE_URL}/health", timeout=5)
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_alert_missing_rate_is_skipped():
    r = httpx.post(f"{BASE_URL}/alert", json=SKIP_PAYLOAD, timeout=5)
    assert r.status_code == 200
    assert r.json()["status"] == "skipped"
    assert r.json()["reason"] == "no_retrain_worthy_alerts"


def test_alert_resolved_is_skipped():
    r = httpx.post(f"{BASE_URL}/alert", json=RESOLVED_PAYLOAD, timeout=5)
    assert r.status_code == 200
    assert r.json()["status"] == "skipped"


def test_alert_returns_200_always():
    """Alertmanager retries on non-2xx — service must always return 200."""
    r = httpx.post(f"{BASE_URL}/alert", json=CRITICAL_PAYLOAD, timeout=30)
    assert r.status_code == 200
    assert r.json()["status"] in {"triggered", "waiting", "debounced", "error", "skipped"}


def test_second_rapid_call_is_debounced():
    """Two back-to-back calls — the second must be debounced."""
    r1 = httpx.post(f"{BASE_URL}/alert", json=CRITICAL_PAYLOAD, timeout=30)
    r2 = httpx.post(f"{BASE_URL}/alert", json=CRITICAL_PAYLOAD, timeout=5)
    # r1 can be anything; r2 should be debounced (first call set _last_trigger_ts)
    statuses = [r1.json()["status"], r2.json()["status"]]
    assert "debounced" in statuses, f"Expected debounced in {statuses}"
```

- [ ] **Step 15.4: Run unit tests to confirm nothing broke**

```bash
pytest tests/ -v --ignore=tests/integration
```

Expected: all PASSED

- [ ] **Step 15.5: Commit**

```bash
git add tests/conftest.py tests/integration/
git commit -m "test: add integration test suite for retrain_trigger service"
```

---

## Self-Review Checklist

Spec sections vs plan coverage:

| Spec section | Task |
|---|---|
| `retrain_trigger` FastAPI service — all 6 states | Tasks 1–5 |
| `check_new_data_available()` three outcomes | Task 2 |
| `send_email()` async SMTP helper | Task 3 |
| `/alert` steps [A]–[F] | Tasks 4–5 |
| `denttime_await_data` DAG | Task 8 |
| Airflow Dataset definitions | Task 6 |
| `feature_engineering_dag.py` modifications | Task 7 |
| `denttime_retrain_dag.py` modifications + callbacks | Task 9 |
| `alertmanager/alertmanager.yml` explicit routes | Task 11 |
| `prometheus/prometheus.yml` alerting block | Task 12 |
| `docker-compose.yml` two new services | Task 13 |
| `.env` SMTP secrets | Task 14 |
| Unit tests — all 11 spec scenarios | Tasks 2–5 |
| DAG structure tests | Task 10 |
| Integration tests | Task 15 |
| `tests/dags/test_modified_dags.py` | Task 10 |
