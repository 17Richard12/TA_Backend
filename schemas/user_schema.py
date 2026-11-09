from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator
from typing import List, Optional
from datetime import date
import re


def validate_password_strength(v: str) -> str:
    if not isinstance(v, str) or len(v) < 8:
        raise ValueError("Password minimal 8 karakter.")
    if len(v) > 128:
        raise ValueError("Password maksimal 128 karakter.")
    if not re.search(r"[A-Z]", v):
        raise ValueError("Password harus mengandung huruf besar.")
    if not re.search(r"[a-z]", v):
        raise ValueError("Password harus mengandung huruf kecil.")
    if not re.search(r"\d", v):
        raise ValueError("Password harus mengandung angka.")
    return v


class RegisterSchema(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    email: EmailStr
    password: str
    # Public registration always creates a patient ("user"); client-supplied roles are ignored.
    role: Optional[str] = "user"

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        return validate_password_strength(v)

    @field_validator("role", mode="before")
    @classmethod
    def force_patient_role(cls, _v: Optional[str]) -> str:
        return "user"


class LoginSchema(BaseModel):
    email: EmailStr
    password: str


class EditAccountSchema(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    birth: Optional[str] = None          # format: "YYYY-MM-DD"
    bloodType: Optional[str] = None      # e.g. "A", "B", "AB", "O"
    gender: Optional[str] = None         # "male" / "female"
    height: Optional[float] = Field(None, ge=0, le=300)
    weight: Optional[float] = Field(None, ge=0, le=500)
    phone: Optional[str] = None
    history: Optional[str] = None
    role: Optional[str] = None
    smoke_limit_low: Optional[int] = Field(None, ge=0, le=200)
    smoke_limit_high: Optional[int] = Field(None, ge=0, le=200)
    assigned_doctor_uid: Optional[str] = None
    authorized_doctors: Optional[List[str]] = None
    assigned_patients: Optional[List[str]] = None

    @model_validator(mode="after")
    def validate_smoke_limits(self):
        if (
            self.smoke_limit_low is not None
            and self.smoke_limit_high is not None
            and self.smoke_limit_high < self.smoke_limit_low
        ):
            raise ValueError("Batas rokok tinggi tidak boleh lebih kecil dari batas rendah.")
        return self


class ChangePasswordSchema(BaseModel):
    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, v: str) -> str:
        return validate_password_strength(v)


class UserResponse(BaseModel):
    uid: str
    name: str
    email: str
    role: str = "user"
    birth: Optional[str] = None
    bloodType: Optional[str] = None
    gender: Optional[str] = None
    height: Optional[float] = None
    weight: Optional[float] = None
    phone: Optional[str] = None
    history: Optional[str] = None
    smoke_limit_low: Optional[int] = 5
    smoke_limit_high: Optional[int] = 10
    assigned_doctor_uid: Optional[str] = None
    authorized_doctors: Optional[List[str]] = None
    assigned_patients: Optional[List[str]] = None


class ActivityRequestSchema(BaseModel):
    date: str                        # Format YYYY-MM-DD or DD/MM/YYYY
    activity: Optional[str] = None   # Nama aktivitas
    done: Optional[bool] = None      # Status selesai/belum


class OnboardingPayload(BaseModel):
    birth: Optional[str] = None
    bloodType: Optional[str] = None
    gender: Optional[str] = None
    height: Optional[float] = Field(0.0, ge=0, le=300)
    weight: Optional[float] = Field(0.0, ge=0, le=500)
    phone: Optional[str] = None
    history: Optional[str] = None
    initial_smoke_count: int = Field(0, ge=0, le=200)
    smoke_limit_low: int = Field(5, ge=0, le=200)
    smoke_limit_high: int = Field(10, ge=0, le=200)
    timestamp: Optional[str] = None  # Tanggal lokal device user, format YYYY-MM-DD

    @model_validator(mode="after")
    def validate_onboarding_limits(self):
        if self.smoke_limit_high < self.smoke_limit_low:
            raise ValueError("Batas rokok tinggi tidak boleh lebih kecil dari batas rendah.")
        return self
