from schemas.user_schema import (
    RegisterSchema,
    LoginSchema,
    EditAccountSchema,
    ChangePasswordSchema,
    UserResponse,
)
from services import user_service


def register(payload: RegisterSchema):
    user = user_service.register_user(payload)
    return {
        "status": "success",
        "message": "User registered successfully",
        "data": _sanitize(user),
    }


def login(payload: LoginSchema):
    user = user_service.login_user(payload)
    return {
        "status": "success",
        "message": "Login successful",
        "data": _sanitize(user),
    }


def get_user(uid: str):
    user = user_service.get_user(uid)
    return {
        "status": "success",
        "data": _sanitize(user),
    }


def edit_account(uid: str, payload: EditAccountSchema):
    user = user_service.edit_account(uid, payload)
    return {
        "status": "success",
        "message": "Account updated successfully",
        "data": _sanitize(user),
    }


def change_password(uid: str, payload: ChangePasswordSchema):
    result = user_service.change_password(uid, payload)
    return {
        "status": "success",
        "message": result["message"],
    }


def _sanitize(user: dict) -> dict:
    """Remove sensitive fields before returning to client."""
    return {k: v for k, v in user.items() if k != "password"}