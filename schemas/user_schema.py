from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Optional
from datetime import date
import re


class RegisterSchema(BaseModel):
    name: str
    email: EmailStr
    password: str
    role: str = "user"

    @field_validator('password')
    @classmethod
    def validate_password(cls, v):
        if len(v) < 8:
            raise ValueError('Password minimal 8 karakter.')
        if not re.search(r'[A-Z]', v):
            raise ValueError('Password harus mengandung huruf besar.')
        if not re.search(r'[a-z]', v):
            raise ValueError('Password harus mengandung huruf kecil.')
        if not re.search(r'\d', v):
            raise ValueError('Password harus mengandung angka.')
        return v



class LoginSchema(BaseModel):
    email: EmailStr
    password: str


class EditAccountSchema(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    birth: Optional[str] = None          # format: "YYYY-MM-DD"
    bloodType: Optional[str] = None      # e.g. "A", "B", "AB", "O"
    gender: Optional[str] = None         # "male" / "female"
    height: Optional[float] = None
    weight: Optional[float] = None
    phone: Optional[str] = None
    history: Optional[str] = None
    role: Optional[str] = None


class ChangePasswordSchema(BaseModel):
    new_password: str = Field(..., min_length=6)


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

class ActivityRequestSchema(BaseModel):
    date: str                        # Format YYYY-MM-DD
    activity: Optional[str] = None   # Nama aktivitas
    done: Optional[bool] = None      # Status selesai/belum

class OnboardingPayload(BaseModel):
    birth: Optional[str] = None
    bloodType: Optional[str] = None
    gender: Optional[str] = None
    height: Optional[float] = 0.0
    weight: Optional[float] = 0.0
    phone: Optional[str] = None
    history: Optional[str] = None
    initial_smoke_count: int = 0