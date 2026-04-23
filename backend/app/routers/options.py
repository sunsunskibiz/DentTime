from fastapi import APIRouter, Request

router = APIRouter(tags=["options"])


@router.get("/options")
def get_options(request: Request):
    doctor_profile = request.app.state.doctor_profile
    clinic_profile = request.app.state.clinic_profile

    doctors = [
        {"id": doctor_id, "doctor": doctor_id}
        for doctor_id in sorted(doctor_profile.keys())
    ]

    clinics = [
        {"id": clinic_id, "clinic": clinic_id}
        for clinic_id in sorted(clinic_profile.keys())
    ]

    return {
        "doctors": doctors,
        "clinics": clinics,
    }