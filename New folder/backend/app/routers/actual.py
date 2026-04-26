from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.db import get_conn
from app.schemas import ActualRequest, ActualResponse

router = APIRouter(tags=["actual"])


@router.post("/actual", response_model=ActualResponse)
def log_actual(data: ActualRequest):
    conn = get_conn()
    cur = conn.execute(
        """
        UPDATE predictions
        SET actual_slot = ?
        WHERE request_id = ?
        """,
        (data.actual_duration, data.request_id),
    )
    conn.commit()
    updated = cur.rowcount
    conn.close()

    if updated == 0:
        raise HTTPException(status_code=404, detail="request_id not found")

    return {
        "request_id": data.request_id,
        "status": "logged",
        "logged_at": datetime.now(timezone.utc),
    }
