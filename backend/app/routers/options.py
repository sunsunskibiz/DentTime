from fastapi import APIRouter, Request
import os

from app.schemas import OptionsResponse

router = APIRouter(tags=["options"])

MODE = os.getenv("APP_MODE", "prod")

@router.get("/options", response_model=OptionsResponse)
def get_options(request: Request):
    doctor_profile = request.app.state.doctor_profile
    clinic_profile = request.app.state.clinic_profile
    treatment_encoding = request.app.state.treatment_encoding

    treatments = [
        {"id": str(idx), "treatment": treatment_name.replace("_", " ").title()}
        for treatment_name, idx in sorted(treatment_encoding.items(), key=lambda x: x[1])
    ]

    def get_display_id(entity_id, profile):
        if MODE == "demo":
            return str(profile[entity_id]["id(only_demo)"])
        return entity_id  # prod ใช้ id จริง

    doctors = [
        {
            "id": doctor_id,
            "doctor": get_display_id(doctor_id, doctor_profile),
        }
        for doctor_id in sorted(doctor_profile.keys())
    ]

    clinics = [
        {
            "id": clinic_id,
            "clinic": get_display_id(clinic_id, clinic_profile),
        }
        for clinic_id in sorted(clinic_profile.keys())
    ]

    return {
        "treatments": treatments,
        "doctors": doctors,
        "clinics": clinics,
    }