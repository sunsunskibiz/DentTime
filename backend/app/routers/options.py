from fastapi import APIRouter

from app.schemas import OptionsResponse

router = APIRouter(tags=["options"])

@router.get("/options", response_model=OptionsResponse)
def get_options():
    return {
        "symptoms": [
            {"id": "1", "symptom": "Tooth pain"},
            {"id": "2", "symptom": "Swelling"},
            {"id": "3", "symptom": "Bleeding gums"},
        ],
        "doctors": [
            {"id": "1", "doctor": "Dr. Smith"},
            {"id": "2", "doctor": "Dr. John"},
            {"id": "3", "doctor": "Dr. Emily"},
        ],
    }