from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, field_validator


class PredictRequest(BaseModel):
    clinic_pseudo_id: str
    dentist_pseudo_id: Optional[str] = None
    has_dentist_id: int
    treatment: str
    tooth_no: Optional[str] = None
    surfaces: Optional[str] = None
    total_amount: float
    has_notes: int
    appt_day_of_week: int
    appt_hour_bucket: int
    is_first_case: int
    appointment_rank_in_day: Optional[int] = None


class APIRequest(BaseModel):
    treatmentSymptoms: str
    toothNumbers: Optional[str] = None
    surfaces: Optional[str] = None
    totalAmount: float
    selectedDateTime: str
    doctorId: Optional[str] = None
    clinicId: str
    notes: Optional[str] = None
    request_time: Optional[str] = None

    @field_validator("treatmentSymptoms")
    @classmethod
    def validate_treatment_symptoms(cls, v):
        if not v or not v.strip():
            raise ValueError("treatmentSymptoms is required")
        return v

    @field_validator("clinicId")
    @classmethod
    def validate_clinic_id(cls, v):
        if not v or not v.strip():
            raise ValueError("clinicId is required")
        return v


class PredictResponse(BaseModel):
    predicted_duration_class: int
    confidence: List[float]
    unit: str = "minutes"
    model_version: str
    timestamp: datetime
    request_id: str
    status: str


class DoctorOption(BaseModel):
    id: str
    doctor: str


class ClinicOption(BaseModel):
    id: str
    clinic: str


class TreatmentOption(BaseModel):
    id: str
    treatment: str


class OptionsResponse(BaseModel):
    treatments: List[TreatmentOption]
    doctors: List[DoctorOption]
    clinics: List[ClinicOption]


class ActualRequest(BaseModel):
    request_id: str
    actual_duration: int
    unit: str = "minutes"
    completed_at: Optional[datetime] = None


class ActualResponse(BaseModel):
    request_id: str
    status: str
    logged_at: datetime
