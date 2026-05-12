from pydantic import BaseModel, EmailStr, Field
from typing import Optional
from datetime import date


class RegisterSchema(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(..., min_length=6)


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


class ChangePasswordSchema(BaseModel):
    new_password: str = Field(..., min_length=6)


class UserResponse(BaseModel):
    uid: str
    name: str
    email: str
    birth: Optional[str] = None
    bloodType: Optional[str] = None
    gender: Optional[str] = None
    height: Optional[float] = None
    weight: Optional[float] = None
    phone: Optional[str] = None
    history: Optional[str] = None