from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.schemas import OptionsResponse

router = APIRouter(tags=["options"])

_INTERNAL_PROFILE_KEYS = {"__global__"}
_DISPLAY_KEYS = (
    "id(only_demo)",
    "display_id",
    "display_name",
    "name",
    "doctor",
    "clinic",
)


def _public_profile_ids(profile: dict[str, Any]) -> list[str]:
    """Return selectable profile IDs only, excluding aggregate fallback rows."""
    return sorted(str(key) for key in profile.keys() if str(key) not in _INTERNAL_PROFILE_KEYS)


def _profile_label(profile: dict[str, Any], profile_id: str) -> str:
    """Use a human display field when present; otherwise fall back to the real ID.

    The feature-engineering pipeline stores doctor/clinic profiles as statistical
    lookup tables. Those generated JSON files do not always contain a demo label
    like `id(only_demo)`, so `/options` must not assume that field exists.
    """
    entry = profile.get(profile_id, {})
    if isinstance(entry, dict):
        for key in _DISPLAY_KEYS:
            value = entry.get(key)
            if value not in (None, ""):
                return str(value)
    return str(profile_id)


def _treatment_label(treatment_name: str) -> str:
    return treatment_name.replace("_", " ").title()


@router.get("/options", response_model=OptionsResponse)
def get_options(request: Request):
    doctor_profile = request.app.state.doctor_profile
    clinic_profile = request.app.state.clinic_profile
    treatment_encoding = request.app.state.treatment_encoding

    treatments = [
        {"id": str(idx), "treatment": _treatment_label(treatment_name)}
        for treatment_name, idx in sorted(treatment_encoding.items(), key=lambda x: x[1])
        if treatment_name != "UNKNOWN"
    ]

    doctors = [
        {"id": doctor_id, "doctor": _profile_label(doctor_profile, doctor_id)}
        for doctor_id in _public_profile_ids(doctor_profile)
    ]

    clinics = [
        {"id": clinic_id, "clinic": _profile_label(clinic_profile, clinic_id)}
        for clinic_id in _public_profile_ids(clinic_profile)
    ]

    return {
        "treatments": treatments,
        "doctors": doctors,
        "clinics": clinics,
    }
