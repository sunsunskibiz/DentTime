from pydantic import BaseModel, field_validator
from typing import List, Optional
from datetime import datetime

# class Symptom(BaseModel):
#     id: str
#     symptom: str


class PredictRequest(BaseModel):
    clinic_pseudo_id: str
    dentist_pseudo_id: Optional[str] = None
    has_dentist_id: int                       # 0 or 1 not done
    treatment: str
    tooth_no: Optional[str] = None
    surfaces: Optional[str] = None
    total_amount: float
    has_notes: int                            # 0 or 1
    appt_day_of_week: int                     # 0–6
    appt_hour_bucket: int                     # 0 if unknown, else {4,8,12,16,20}
    is_first_case: int                        # 0 or 1
    appointment_rank_in_day: Optional[int] = None


class APIRequest(BaseModel):
    treatmentSymptoms: str
    toothNumbers: Optional[str] = None
    surfaces: Optional[str] = None
    timeOfDay: str
    dayOfWeek: str
    appointmentRankInDay: Optional[int] = None
    doctorId: Optional[str] = None
    clinicId: str
    isFirstCase: bool
    notes: Optional[str] = None
    request_time: datetime
    @field_validator("treatmentSymptoms")
    @classmethod
    def validate_treatment_symptoms(cls, v):
        if not v or not v.strip():
            raise ValueError("treatmentSymptoms is required")
        return v

    @field_validator("timeOfDay")
    @classmethod
    def validate_time_of_day(cls, v):
        if not v or not v.strip():
            raise ValueError("timeOfDay is required")
        allowed = {"0", "4", "8", "12", "16", "20"}
        if v not in allowed:
            raise ValueError("timeOfDay must be one of: 0, 4, 8, 12, 16, 20")
        return v

    @field_validator("dayOfWeek")
    @classmethod
    def validate_day_of_week(cls, v):
        if not v or not v.strip():
            raise ValueError("dayOfWeek is required")
        allowed = {"0", "1", "2", "3", "4", "5", "6"}
        if v not in allowed:
            raise ValueError("dayOfWeek must be one of: 0, 1, 2, 3, 4, 5, 6")
        return v
    
    @field_validator("clinicId")
    @classmethod
    def validate_clinic_id(cls, v):
        if not v or not v.strip():
            raise ValueError("clinicId is required")
        return v

class PredictResponse(BaseModel):
    predicted_duration_class: int
    unit: str
    model_version: str
    timestamp: datetime
    request_id: str
    status: str
    processing_time_ms: float

class SymptomOption(BaseModel):
    id: str
    symptom: str

class DoctorOption(BaseModel):
    id: str
    doctor: str

class OptionsResponse(BaseModel):
    symptoms: List[SymptomOption]
    doctors: List[DoctorOption]


class ActualRequest(BaseModel):
    request_id: str
    actual_duration: int
    unit: str = "minutes"
    completed_at: Optional[datetime] = None


class ActualResponse(BaseModel):
    request_id: str
    status: str
    logged_at: datetime