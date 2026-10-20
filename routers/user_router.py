from fastapi import APIRouter, Query
from schemas.user_schema import (
    ActivityRequestSchema,
    ActivityRequestSchema,
    RegisterSchema,
    LoginSchema,
    EditAccountSchema,
    ChangePasswordSchema,
)
from controllers import user_controller
from services import user_service
from services import user_service

router = APIRouter(prefix="/users", tags=["Users"])


@router.post("/register", summary="Register new user")
def register(payload: RegisterSchema):
    return user_controller.register(payload)

@router.post("/login", summary="Login user")
def login(payload: LoginSchema):
    return user_controller.login(payload)

@router.get("/", summary="Get all users")
def get_all_users():
    return user_controller.get_all_users()

@router.get("/patients", summary="Get all users with role 'user' (untuk list pasien dokter)")
def get_patients():
    return user_controller.get_patients()

@router.get("/{uid}", summary="Get user account")
def get_user(uid: str):
    return user_controller.get_user(uid)

@router.put("/{uid}/edit", summary="Edit account profile")
def edit_account(uid: str, payload: EditAccountSchema):
    return user_controller.edit_account(uid, payload)


@router.put("/{uid}/change-password", summary="Change password")
def change_password(uid: str, payload: ChangePasswordSchema):
    return user_controller.change_password(uid, payload)

@router.get("/{uid}/daily-report", summary="Laporan Harian Pasien (Detail Profil, Rokok, Aktivitas)")
def get_patient_daily_report(
    uid: str, 
    date: str = Query(..., description="Tanggal laporan, format: YYYY-MM-DD")
):
    return user_controller.get_patient_daily_report(uid, date)

@router.post("/{uid}/activities", summary="Tambah aktivitas harian pasien")
def add_activity(uid: str, req: ActivityRequestSchema):
    try:
        act_id = user_service.add_daily_activity(uid, req.date, req.activity)
        return {"status": "success", "data": {"id": act_id}}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.put("/{uid}/activities/{activity_id}", summary="Edit aktivitas harian pasien")
def edit_activity(uid: str, activity_id: str, req: ActivityRequestSchema):
    try:
        user_service.edit_daily_activity(uid, req.date, activity_id, req.activity, req.done)
        return {"status": "success", "message": "Berhasil diupdate"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.delete("/{uid}/activities/{activity_id}", summary="Hapus aktivitas harian pasien")
def delete_activity(uid: str, activity_id: str, date: str):
    try:
        user_service.delete_daily_activity(uid, date, activity_id)
        return {"status": "success", "message": "Berhasil dihapus"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    