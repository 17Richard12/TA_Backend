import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from google.cloud.firestore_v1 import FieldFilter

from config.firebase import db
from config.security import (
    ADMIN_ROLES,
    DOCTOR_ROLES,
    PATIENT_ROLES,
    create_access_token,
    hash_password,
    is_admin,
    is_doctor,
    is_doctor_authorized_for_patient,
    verify_password,
)
from schemas.smoke_schema import normalize_smoke_date_str
from schemas.user_schema import (
    ChangePasswordSchema,
    EditAccountSchema,
    LoginSchema,
    RegisterSchema,
)
from services.smoke_service import (
    aggregate_hourly_logs,
    find_smoke_doc_ref,
    jakarta_today_str,
    set_smoke_total,
)

ACCOUNTS_COL = "accounts"
DAILY_ACT_COL = "dailyActivities"


# ---------- helpers ----------

def _get_user_by_email(email: str):
    normalized_email = (email or "").strip()
    docs = (
        db.collection(ACCOUNTS_COL)
        .where(filter=FieldFilter("email", "==", normalized_email))
        .limit(1)
        .stream()
    )
    for doc in docs:
        return doc.id, doc.to_dict()

    # Fallback case-insensitive check if email casing differed
    if normalized_email != normalized_email.lower():
        docs_lower = (
            db.collection(ACCOUNTS_COL)
            .where(filter=FieldFilter("email", "==", normalized_email.lower()))
            .limit(1)
            .stream()
        )
        for doc in docs_lower:
            return doc.id, doc.to_dict()
    return None, None


def _get_user_by_uid(uid: str):
    doc = db.collection(ACCOUNTS_COL).document(uid).get()
    if not doc.exists:
        return None
    return doc.to_dict()


def is_profile_complete(user: dict) -> bool:
    """
    Profil dianggap lengkap jika:
    - role bukan pasien (dokter/master tidak wajib onboarding), ATAU
    - onboarding sudah diselesaikan, ATAU
    - field profil wajib sudah terisi (untuk user lama yang mengisi via Edit Profil).
    """
    role = str(user.get("role", "user")).strip().lower()
    if role not in {"user", "pasien", "patient"}:
        return True
    if user.get("onboarding_completed") is True:
        return True

    def _filled(key):
        val = user.get(key)
        return val is not None and str(val).strip() != ""

    def _positive(key):
        try:
            return float(user.get(key) or 0) > 0
        except (TypeError, ValueError):
            return False

    return _filled("gender") and _filled("bloodType") and _positive("height") and _positive("weight")


def _with_profile_flag(uid: str, user: dict, include_token: bool = False) -> dict:
    role = str(user.get("role", "user")).strip().lower()
    safe_user = {k: v for k, v in user.items() if k != "password"}
    result = {
        "uid": uid,
        **safe_user,
        "role": role,
        "profile_completed": is_profile_complete(user),
    }
    if include_token:
        result["access_token"] = create_access_token(uid=uid, role=role)
        result["token_type"] = "bearer"
    return result


def find_or_get_daily_activity_ref(uid: str, date_str: str):
    """
    Finds an existing dailyActivities document for (uid, date_str) using the
    deterministic ID `{uid}_{date_str}` first, falling back to a query on legacy docs.
    Returns (doc_ref, doc_dict_or_None).
    """
    normalized_date = normalize_smoke_date_str(date_str)
    deterministic_id = f"{uid}_{normalized_date}"
    det_ref = db.collection(DAILY_ACT_COL).document(deterministic_id)
    det_snap = det_ref.get()
    if det_snap.exists:
        return det_ref, det_snap.to_dict()

    query_docs = (
        db.collection(DAILY_ACT_COL)
        .where(filter=FieldFilter("userID", "==", uid))
        .where(filter=FieldFilter("timestamp", "==", normalized_date))
        .limit(1)
        .stream()
    )
    for doc in query_docs:
        return doc.reference, doc.to_dict()

    return det_ref, None


# ---------- service functions ----------

def register_user(payload: RegisterSchema) -> dict:
    uid, existing = _get_user_by_email(payload.email)
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    new_uid = str(uuid.uuid4()).replace("-", "")[:20]
    # Security requirement: Public registration ALWAYS creates a Patient ("user")
    user_data = {
        "name": payload.name.strip(),
        "email": payload.email.strip(),
        "password": hash_password(payload.password),
        "role": "user",
        "birth": "",
        "bloodType": "",
        "gender": "",
        "height": 0,
        "weight": 0,
        "phone": "",
        "history": "",
        "smoke_limit_low": 5,
        "smoke_limit_high": 10,
        "onboarding_completed": False,
    }
    db.collection(ACCOUNTS_COL).document(new_uid).set(user_data)
    return _with_profile_flag(new_uid, user_data, include_token=True)


def login_user(payload: LoginSchema) -> dict:
    uid, user = _get_user_by_email(payload.email)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    stored_hash = user.get("password", "")
    is_valid, needs_rehash = verify_password(payload.password, stored_hash)
    if not is_valid:
        raise HTTPException(status_code=401, detail="Invalid password")

    # Optional non-destructive auto-upgrade of legacy SHA-256 hash upon verified login
    # when AUTO_MIGRATE_PASSWORD_HASH=true is explicitly set in environment.
    if needs_rehash and os.getenv("AUTO_MIGRATE_PASSWORD_HASH", "false").lower() in {"1", "true", "yes"}:
        new_hash = hash_password(payload.password)
        db.collection(ACCOUNTS_COL).document(uid).update({"password": new_hash})
        user["password"] = new_hash

    return _with_profile_flag(uid, user, include_token=True)


def get_user(uid: str) -> dict:
    user = _get_user_by_uid(uid)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return _with_profile_flag(uid, user)


def edit_account(
    uid: str,
    payload: EditAccountSchema,
    current_user: Optional[Dict[str, Any]] = None,
) -> dict:
    user = _get_user_by_uid(uid)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    raw_updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not raw_updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    admin_caller = current_user is not None and is_admin(current_user)
    self_caller = current_user is not None and current_user.get("uid") == uid

    if not admin_caller and not self_caller:
        raise HTTPException(
            status_code=403,
            detail="You are only authorized to modify your own account.",
        )

    admin_only_fields = {"role", "assigned_doctor_uid", "authorized_doctors", "assigned_patients"}
    if not admin_caller:
        # Regular users and doctors cannot change roles or doctor/patient authorization bindings
        attempted_admin_fields = admin_only_fields.intersection(raw_updates.keys())
        if attempted_admin_fields:
            for field in list(attempted_admin_fields):
                # If the user sent the exact same role they already have, ignore it; otherwise 403
                if field == "role" and str(raw_updates["role"]).lower().strip() == str(user.get("role", "user")).lower().strip():
                    raw_updates.pop("role", None)
                else:
                    raise HTTPException(
                        status_code=403,
                        detail="Only administrator accounts can manage user roles or doctor assignments.",
                    )
    else:
        if "role" in raw_updates:
            normalized_role = str(raw_updates["role"]).lower().strip()
            valid_roles = ADMIN_ROLES | DOCTOR_ROLES | PATIENT_ROLES
            if normalized_role not in valid_roles:
                raise HTTPException(status_code=400, detail=f"Invalid role: {raw_updates['role']}")
            raw_updates["role"] = normalized_role

        if not self_caller:
            # Admin editing another user's account can only manage role, name, and doctor/patient assignments
            allowed_admin_other = {"name", "role", "assigned_doctor_uid", "authorized_doctors", "assigned_patients"}
            disallowed = set(raw_updates.keys()) - allowed_admin_other
            if disallowed:
                raise HTTPException(
                    status_code=403,
                    detail="Users can only change their own personal profile details.",
                )

    # Validate smoke_limit_low <= smoke_limit_high against existing user values
    eff_low = raw_updates.get("smoke_limit_low", user.get("smoke_limit_low", 5))
    eff_high = raw_updates.get("smoke_limit_high", user.get("smoke_limit_high", 10))
    if eff_low is not None and eff_high is not None and int(eff_high) < int(eff_low):
        raise HTTPException(
            status_code=400,
            detail="Batas rokok tinggi tidak boleh lebih kecil dari batas rendah.",
        )

    if not raw_updates:
        return _with_profile_flag(uid, user)

    db.collection(ACCOUNTS_COL).document(uid).update(raw_updates)
    updated_user = _get_user_by_uid(uid)
    return _with_profile_flag(uid, updated_user)


def change_password(uid: str, payload: ChangePasswordSchema) -> dict:
    user = _get_user_by_uid(uid)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    stored_hash = user.get("password", "")
    is_valid, _ = verify_password(payload.current_password, stored_hash)
    if not is_valid:
        raise HTTPException(status_code=401, detail="Password saat ini tidak sesuai.")

    db.collection(ACCOUNTS_COL).document(uid).update(
        {"password": hash_password(payload.new_password)}
    )
    return {"message": "Password changed successfully"}


def get_all_users() -> list:
    """Mengambil semua data user dari Firestore (khusus Admin)."""
    docs = db.collection(ACCOUNTS_COL).stream()
    users = []
    for doc in docs:
        users.append(_with_profile_flag(doc.id, doc.to_dict() or {}))
    return users


def get_patients(current_user: Optional[Dict[str, Any]] = None) -> list:
    """
    Mengambil data pasien (role 'user').
    - Admin: dapat melihat seluruh pasien.
    - Dokter: HANYA dapat mengakses pasien yang secara eksplisit diotorisasi untuk dikelola dokter tersebut.
    """
    docs = (
        db.collection(ACCOUNTS_COL)
        .where(filter=FieldFilter("role", "==", "user"))
        .stream()
    )
    users = []
    for doc in docs:
        patient_data = doc.to_dict() or {}
        patient_uid = doc.id
        if current_user is not None:
            if is_admin(current_user):
                users.append(_with_profile_flag(patient_uid, patient_data))
            elif is_doctor(current_user):
                if is_doctor_authorized_for_patient(
                    current_user, patient_uid, patient_data=patient_data
                ):
                    users.append(_with_profile_flag(patient_uid, patient_data))
        else:
            users.append(_with_profile_flag(patient_uid, patient_data))
    return users


def get_patient_daily_report(uid: str, date_str: str) -> dict:
    """
    Mengambil data lengkap pasien beserta laporan aktivitas dan rokok pada tanggal tertentu.
    Format date_str: YYYY-MM-DD
    """
    normalized_date = normalize_smoke_date_str(date_str)

    # 1. Ambil Data Pasien
    user = _get_user_by_uid(uid)
    if not user:
        raise HTTPException(status_code=404, detail="Pasien tidak ditemukan")

    # Hapus field password agar aman dikirim ke frontend
    user_data = {k: v for k, v in user.items() if k != "password"}
    user_data.setdefault("smoke_limit_low", 5)
    user_data.setdefault("smoke_limit_high", 10)
    smoke_limit_low = int(user_data.get("smoke_limit_low") or 5)
    smoke_limit_high = int(user_data.get("smoke_limit_high") or 10)

    # 2. Ambil Total Smoke Count + Hourly Graph
    smoke_ref, smoke_data = find_smoke_doc_ref(uid, normalized_date)
    total_smoke = (smoke_data or {}).get("count", 0)
    smoke_hourly_dict = aggregate_hourly_logs(smoke_ref)

    smoke_hourly_graph = [{"hour": k, "count": v} for k, v in smoke_hourly_dict.items()]

    # 3. Ambil Daily Activities
    parent_ref, parent_data = find_or_get_daily_activity_ref(uid, normalized_date)

    activities_list = []
    total_act = 0
    total_act_done = 0

    if parent_data is not None:
        sub_docs = parent_ref.collection("activities").stream()
        for sub in sub_docs:
            sub_data = sub.to_dict() or {}
            is_done = bool(sub_data.get("done", False))

            activities_list.append({
                "id": sub.id,
                "activity": sub_data.get("activity", "Aktivitas tidak bernama"),
                "done": is_done,
            })

            total_act += 1
            if is_done:
                total_act_done += 1

    return {
        "report_date": normalized_date,
        "patient_data": user_data,
        "total_smoke": total_smoke,
        "smoke_limit_low": smoke_limit_low,
        "smoke_limit_high": smoke_limit_high,
        "total_activity": total_act,
        "total_activity_done": total_act_done,
        "activities_list": activities_list,
        "smoke_hourly_graph": smoke_hourly_graph,
    }


def add_daily_activity(uid: str, date_str: str, activity_text: str) -> str:
    if not activity_text or not str(activity_text).strip():
        raise HTTPException(status_code=400, detail="Nama aktivitas tidak boleh kosong")

    normalized_date = normalize_smoke_date_str(date_str)
    parent_ref, parent_data = find_or_get_daily_activity_ref(uid, normalized_date)

    if parent_data is None:
        parent_ref.set({
            "userID": uid,
            "timestamp": normalized_date,
        })

    new_act_ref = parent_ref.collection("activities").document()
    new_act_ref.set({
        "activity": activity_text.strip(),
        "done": False,
    })
    return new_act_ref.id


def edit_daily_activity(
    uid: str,
    date_str: str,
    activity_id: str,
    activity_text: str = None,
    done: bool = None,
):
    normalized_date = normalize_smoke_date_str(date_str)
    parent_ref, parent_data = find_or_get_daily_activity_ref(uid, normalized_date)
    if parent_data is None:
        raise HTTPException(status_code=404, detail="Data aktivitas harian tidak ditemukan")

    act_ref = parent_ref.collection("activities").document(activity_id)
    if not act_ref.get().exists:
        raise HTTPException(status_code=404, detail="Item aktivitas tidak ditemukan")

    updates = {}
    if activity_text is not None:
        updates["activity"] = activity_text.strip()
    if done is not None:
        updates["done"] = bool(done)
    if updates:
        act_ref.update(updates)


def delete_daily_activity(uid: str, date_str: str, activity_id: str):
    normalized_date = normalize_smoke_date_str(date_str)
    parent_ref, parent_data = find_or_get_daily_activity_ref(uid, normalized_date)
    if parent_data is None:
        raise HTTPException(status_code=404, detail="Data aktivitas harian tidak ditemukan")

    parent_ref.collection("activities").document(activity_id).delete()


def process_onboarding(uid: str, data: dict):
    try:
        user_ref = db.collection(ACCOUNTS_COL).document(uid)
        existing_user = user_ref.get()
        if not existing_user.exists:
            raise HTTPException(status_code=404, detail="User not found")
        already_onboarded = (existing_user.to_dict() or {}).get("onboarding_completed") is True

        smoke_low = max(0, int(data.get("smoke_limit_low") if data.get("smoke_limit_low") is not None else 5))
        smoke_high = max(smoke_low, int(data.get("smoke_limit_high") if data.get("smoke_limit_high") is not None else 10))

        # 1. Update profil pengguna (ke koleksi "accounts")
        user_ref.set({
            "birth": data.get("birth"),
            "bloodType": data.get("bloodType"),
            "gender": data.get("gender"),
            "height": data.get("height"),
            "weight": data.get("weight"),
            "phone": data.get("phone"),
            "history": data.get("history"),
            "smoke_limit_low": smoke_low,
            "smoke_limit_high": smoke_high,
            "onboarding_completed": True,
        }, merge=True)

        # Jika onboarding sudah pernah dilakukan, jangan duplikasi smoke count & aktivitas
        if already_onboarded:
            return True

        # 2. Inisialisasi Smoke Count & Logs
        raw_ts = data.get("timestamp")
        today_str = normalize_smoke_date_str(raw_ts) if raw_ts else jakarta_today_str()
        initial_smoke = max(0, int(data.get("initial_smoke_count", 0) or 0))

        set_smoke_total(uid, today_str, initial_smoke, action="inserted_via_onboarding")

        # 3. Generate Daily Activities tanpa duplikasi dokumen parent
        activities_to_add = [
            "Minum air putih minimal 8 gelas hari ini",
            "Lakukan peregangan tubuh ringan selama 10 menit",
        ]

        if initial_smoke > 10:
            activities_to_add.append("Tarik napas dalam 4-7-8 setiap ada hasrat merokok")
            activities_to_add.append("Ganti jadwal merokok dengan mengunyah permen karet")
        elif initial_smoke > 0:
            activities_to_add.append("Tunda merokok 10 menit jika hasrat muncul")

        history = str(data.get("history", "")).lower()
        if "asma" in history:
            activities_to_add.append("Hindari debu dan pastikan inhaler mudah dijangkau")
        elif "hipertensi" in history or "darah tinggi" in history:
            activities_to_add.append("Kurangi konsumsi garam berlebih hari ini")

        parent_ref, parent_data = find_or_get_daily_activity_ref(uid, today_str)
        if parent_data is None:
            parent_ref.set({
                "userID": uid,
                "timestamp": today_str,
            })

        for act_text in activities_to_add:
            parent_ref.collection("activities").document().set({
                "activity": act_text,
                "done": False,
            })

        return True
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error in process_onboarding: {e}")
        raise e
