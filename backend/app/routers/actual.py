from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.schemas import ActualRequest, ActualResponse

router = APIRouter(tags=["actual"])

# mock storage
actual_logs = []
@router.post("/actual", response_model=ActualResponse)
def log_actual(data: ActualRequest):
    try:
        print("Received actual data:", data)

        # logging (mock)
        actual_logs.append(data.model_dump())

        return {
            "request_id": data.request_id,
            "status": "logged",
            "logged_at": datetime.now(timezone.utc)
        }

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "error": "ActualLoggingError",
                "message": str(e),
                "code": 500
            }
        )