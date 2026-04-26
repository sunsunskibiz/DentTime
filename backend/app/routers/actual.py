from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.db import get_conn, init_db
from app.schemas import ActualRequest, ActualResponse

router = APIRouter(tags=["actual"])


@router.post("/actual", response_model=ActualResponse)
def log_actual(data: ActualRequest):
    init_db()
    logged_at = datetime.now(timezone.utc)

    conn = get_conn()
    try:
        cur = conn.execute(
            """
            UPDATE predictions
            SET actual_slot = ?
            WHERE request_id = ?
            """,
            (int(data.actual_duration), data.request_id),
        )
        conn.commit()
    finally:
        conn.close()

    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="request_id not found in prediction log")

    return {
        "request_id": data.request_id,
        "status": "logged",
        "logged_at": logged_at,
    }
