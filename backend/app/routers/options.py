from fastapi import APIRouter, Request

from app.schemas import OptionsResponse

router = APIRouter(tags=["options"])


@router.get("/options", response_model=OptionsResponse)
def get_options(request: Request):
    doctor_profile = request.app.state.doctor_profile
    clinic_profile = request.app.state.clinic_profile
    treatment_encoding = request.app.state.treatment_encoding

    treatments = [
        {"id": str(idx), "treatment": treatment_name.replace("_", " ").title()}
        for treatment_name, idx in sorted(treatment_encoding.items(), key=lambda x: x[1])
    ]
    doctors = [
        {"id": doctor_id, "doctor": str(doctor_profile[doctor_id]["id(only_demo)"])}
        for doctor_id in sorted(doctor_profile.keys())
    ]

    clinics = [
        {"id": clinic_id, "clinic": str(clinic_profile[clinic_id]["id(only_demo)"])}
        for clinic_id in sorted(clinic_profile.keys())
    ]
    return {
        "treatments": treatments,
        "doctors": doctors,
        "clinics": clinics,
    }