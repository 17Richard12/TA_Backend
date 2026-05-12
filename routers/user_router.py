from fastapi import APIRouter
from schemas.user_schema import (
    RegisterSchema,
    LoginSchema,
    EditAccountSchema,
    ChangePasswordSchema,
)
from controllers import user_controller

router = APIRouter(prefix="/users", tags=["Users"])


@router.post("/register", summary="Register new user")
def register(payload: RegisterSchema):
    return user_controller.register(payload)


@router.post("/login", summary="Login user")
def login(payload: LoginSchema):
    return user_controller.login(payload)


@router.get("/{uid}", summary="Get user account")
def get_user(uid: str):
    return user_controller.get_user(uid)


@router.put("/{uid}/edit", summary="Edit account profile")
def edit_account(uid: str, payload: EditAccountSchema):
    return user_controller.edit_account(uid, payload)


@router.put("/{uid}/change-password", summary="Change password")
def change_password(uid: str, payload: ChangePasswordSchema):
    return user_controller.change_password(uid, payload)