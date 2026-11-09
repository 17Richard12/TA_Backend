from typing import Any, Dict
from fastapi import APIRouter, Depends, HTTPException, Query, status
from config.security import (
    assert_patient_access,
    assert_self_access,
    assert_self_or_admin,
    get_current_user,
    is_admin,
    is_doctor,
    require_admin,
)
from schemas.user_schema import (
    ActivityRequestSchema,
    RegisterSchema,
    LoginSchema,
    EditAccountSchema,
    ChangePasswordSchema,
    OnboardingPayload,
)
from controllers import user_controller
from services import user_service

router = APIRouter(prefix="/users", tags=["Users"])


@router.post("/register", summary="Register new user")
def register(payload: RegisterSchema):
    return user_controller.register(payload)


@router.post("/login", summary="Login user")
def login(payload: LoginSchema):
    return user_controller.login(payload)


@router.get("/", summary="Get all users (Admin only)")
def get_all_users(_admin: Dict[str, Any] = Depends(require_admin)):
    return user_controller.get_all_users()


@router.get("/patients", summary="Get authorized patients with role 'user'")
def get_patients(current_user: Dict[str, Any] = Depends(get_current_user)):
    if not (is_admin(current_user) or is_doctor(current_user)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only doctors and administrators can list patients.",
        )
    return user_controller.get_patients(current_user=current_user)


@router.get("/{uid}", summary="Get user account")
def get_user(uid: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    assert_patient_access(current_user, uid)
    return user_controller.get_user(uid)


@router.put("/{uid}/edit", summary="Edit account profile")
def edit_account(
    uid: str,
    payload: EditAccountSchema,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    assert_self_or_admin(current_user, uid)
    return user_controller.edit_account(uid, payload, current_user=current_user)


@router.put("/{uid}/change-password", summary="Change password")
def change_password(
    uid: str,
    payload: ChangePasswordSchema,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    assert_self_access(current_user, uid)
    return user_controller.change_password(uid, payload)


@router.get("/{uid}/daily-report", summary="Laporan Harian Pasien (Detail Profil, Rokok, Aktivitas)")
def get_patient_daily_report(
    uid: str,
    date: str = Query(..., description="Tanggal laporan, format: YYYY-MM-DD"),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    assert_patient_access(current_user, uid)
    return user_controller.get_patient_daily_report(uid, date)


@router.post("/{uid}/activities", summary="Tambah aktivitas harian pasien")
def add_activity(
    uid: str,
    req: ActivityRequestSchema,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    assert_patient_access(current_user, uid)
    try:
        act_id = user_service.add_daily_activity(uid, req.date, req.activity)
        return {"status": "success", "data": {"id": act_id}}
    except HTTPException:
        raise
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.put("/{uid}/activities/{activity_id}", summary="Edit aktivitas harian pasien")
def edit_activity(
    uid: str,
    activity_id: str,
    req: ActivityRequestSchema,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    assert_patient_access(current_user, uid)
    try:
        user_service.edit_daily_activity(uid, req.date, activity_id, req.activity, req.done)
        return {"status": "success", "message": "Berhasil diupdate"}
    except HTTPException:
        raise
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.delete("/{uid}/activities/{activity_id}", summary="Hapus aktivitas harian pasien")
def delete_activity(
    uid: str,
    activity_id: str,
    date: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    assert_patient_access(current_user, uid)
    try:
        user_service.delete_daily_activity(uid, date, activity_id)
        return {"status": "success", "message": "Berhasil dihapus"}
    except HTTPException:
        raise
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.post("/{uid}/onboarding")
def submit_onboarding(
    uid: str,
    payload: OnboardingPayload,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    assert_self_access(current_user, uid)
    return user_controller.submit_onboarding(uid, payload)
