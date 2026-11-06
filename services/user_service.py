from config.firebase import db
from schemas.user_schema import RegisterSchema, LoginSchema, EditAccountSchema, ChangePasswordSchema
from fastapi import HTTPException
import hashlib
import uuid
from google.cloud.firestore_v1 import FieldFilter
from datetime import datetime, timedelta
from services.smoke_service import (
    find_smoke_doc_ref,
    aggregate_hourly_logs,
    set_smoke_total,
    jakarta_today_str,
)


ACCOUNTS_COL = "accounts"


# ---------- helpers ----------

def _hash_password(password: str) -> str:
    """Simple SHA-256 hash. Replace with bcrypt for production."""
    return hashlib.sha256(password.encode()).hexdigest()


def _get_user_by_email(email: str):
    docs = (
        db.collection(ACCOUNTS_COL)
        .where(filter=FieldFilter("email", "==", email))
        .limit(1)
        .stream()
    )
    for doc in docs:
        return doc.id, doc.to_dict()
    return None, None


def _get_user_by_uid(uid: str):
    doc = db.collection(ACCOUNTS_COL).document(uid).get()
    if not doc.exists:
        return None
    return doc.to_dict()


PATIENT_ROLES = {"user", "pasien", "patient"}


def is_profile_complete(user: dict) -> bool:
    """
    Profil dianggap lengkap jika:
    - role bukan pasien (dokter/master tidak wajib onboarding), ATAU
    - onboarding sudah diselesaikan, ATAU
    - field profil wajib sudah terisi (untuk user lama yang mengisi via Edit Profil).
    """
    role = str(user.get("role", "user")).strip().lower()
    if role not in PATIENT_ROLES:
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


def _with_profile_flag(uid: str, user: dict) -> dict:
    return {"uid": uid, **user, "profile_completed": is_profile_complete(user)}


# ---------- service functions ----------

def register_user(payload: RegisterSchema) -> dict:
    uid, existing = _get_user_by_email(payload.email)
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    new_uid = str(uuid.uuid4()).replace("-", "")[:20]
    user_data = {
        "name": payload.name,
        "email": payload.email,
        "password": _hash_password(payload.password),
        "role": payload.role,
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
    return _with_profile_flag(new_uid, user_data)


def login_user(payload: LoginSchema) -> dict:
    uid, user = _get_user_by_email(payload.email)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.get("password") != _hash_password(payload.password):
        raise HTTPException(status_code=401, detail="Invalid password")

    return _with_profile_flag(uid, user)


def get_user(uid: str) -> dict:
    user = _get_user_by_uid(uid)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return _with_profile_flag(uid, user)


def edit_account(uid: str, payload: EditAccountSchema) -> dict:
    user = _get_user_by_uid(uid)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    db.collection(ACCOUNTS_COL).document(uid).update(updates)
    updated_user = _get_user_by_uid(uid)
    return {"uid": uid, **updated_user}


def change_password(uid: str, payload: ChangePasswordSchema) -> dict:
    user = _get_user_by_uid(uid)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    db.collection(ACCOUNTS_COL).document(uid).update(
        {"password": _hash_password(payload.new_password)}
    )
    return {"message": "Password changed successfully"}

def get_all_users() -> list:
    """Mengambil semua data user dari Firestore."""
    docs = db.collection(ACCOUNTS_COL).stream()
    users = []
    for doc in docs:
        users.append({"uid": doc.id, **doc.to_dict()})
    return users

def get_patients() -> list:
    """Mengambil semua data user yang memiliki role 'user' saja (untuk dokter)."""
    docs = (
        db.collection(ACCOUNTS_COL)
        .where(filter=FieldFilter("role", "==", "user"))
        .stream()
    )
    users = []
    for doc in docs:
        users.append({"uid": doc.id, **doc.to_dict()})
    return users

def get_patient_daily_report(uid: str, date_str: str) -> dict:
    """
    Mengambil data lengkap pasien beserta laporan aktivitas dan rokok pada tanggal tertentu.
    Format date_str: YYYY-MM-DD
    """
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
    # Gunakan helper yang sama dengan endpoint smoke-count agar sinkron dengan Home & Report pasien
    smoke_ref, smoke_data = find_smoke_doc_ref(uid, date_str)
    total_smoke = (smoke_data or {}).get("count", 0)
    smoke_hourly_dict = aggregate_hourly_logs(smoke_ref)

    smoke_hourly_graph = [{"hour": k, "count": v} for k, v in smoke_hourly_dict.items()]

    # 3. Ambil Daily Activities
    act_query = (
        db.collection("dailyActivities")
        .where(filter=FieldFilter("userID", "==", uid))
        .where(filter=FieldFilter("timestamp", "==", date_str))
        .limit(1)
        .stream()
    )
    
    activities_list = []
    total_act = 0
    total_act_done = 0
    
    for doc in act_query:
        sub_docs = doc.reference.collection("activities").stream()
        for sub in sub_docs:
            sub_data = sub.to_dict()
            is_done = sub_data.get("done", False)
            
            activities_list.append({
                "id": sub.id,
                "activity": sub_data.get("activity", "Aktivitas tidak bernama"),
                "done": is_done
            })
            
            total_act += 1
            if is_done:
                total_act_done += 1
        break

    return {
        "report_date": date_str,
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
    parent_query = db.collection("dailyActivities").where(filter=FieldFilter("userID", "==", uid)).where(filter=FieldFilter("timestamp", "==", date_str)).limit(1).stream()
    parent_doc = None
    for doc in parent_query:
        parent_doc = doc.reference
        break
    
    # Jika dokumen parent untuk tanggal tersebut belum ada, buat baru
    if not parent_doc:
        _, parent_doc = db.collection("dailyActivities").add({
            "userID": uid,
            "timestamp": date_str
        })
    
    new_act_ref = parent_doc.collection("activities").document()
    new_act_ref.set({
        "activity": activity_text,
        "done": False,
    })
    return new_act_ref.id

def edit_daily_activity(uid: str, date_str: str, activity_id: str, activity_text: str = None, done: bool = None):
    parent_query = db.collection("dailyActivities").where(filter=FieldFilter("userID", "==", uid)).where(filter=FieldFilter("timestamp", "==", date_str)).limit(1).stream()
    for doc in parent_query:
        act_ref = doc.reference.collection("activities").document(activity_id)
        updates = {}
        if activity_text is not None:
            updates["activity"] = activity_text
        if done is not None:
            updates["done"] = done
        if updates:
            act_ref.update(updates)
        break

def delete_daily_activity(uid: str, date_str: str, activity_id: str):
    parent_query = db.collection("dailyActivities").where(filter=FieldFilter("userID", "==", uid)).where(filter=FieldFilter("timestamp", "==", date_str)).limit(1).stream()
    for doc in parent_query:
        doc.reference.collection("activities").document(activity_id).delete()
        break

import uuid
from datetime import datetime, timezone
from google.cloud.firestore_v1.base_query import FieldFilter

def process_onboarding(uid: str, data: dict):
    try:
        user_ref = db.collection(ACCOUNTS_COL).document(uid)
        existing_user = user_ref.get()
        if not existing_user.exists:
            raise HTTPException(status_code=404, detail="User not found")
        already_onboarded = (existing_user.to_dict() or {}).get("onboarding_completed") is True

        # 1. Update profil pengguna (ke koleksi "accounts")
        user_ref.set({
            "birth": data.get("birth"),
            "bloodType": data.get("bloodType"),
            "gender": data.get("gender"),
            "height": data.get("height"),
            "weight": data.get("weight"),
            "phone": data.get("phone"),
            "history": data.get("history"),
            "smoke_limit_low": int(data.get("smoke_limit_low") or 5),
            "smoke_limit_high": int(data.get("smoke_limit_high") or 10),
            "onboarding_completed": True
        }, merge=True)

        # Jika onboarding sudah pernah dilakukan, jangan duplikasi smoke count & aktivitas
        if already_onboarded:
            return True

        # 2. Inisialisasi Smoke Count & Logs
        # Gunakan tanggal dari device user (format YYYY-MM-DD) agar sama dengan yang dibaca Home,
        # fallback ke tanggal WIB (server Cloud Run berjalan di UTC).
        today_str = data.get("timestamp") or jakarta_today_str()
        initial_smoke = int(data.get("initial_smoke_count", 0) or 0)

        # Simpan nilai smoke count via helper yang sama dengan endpoint smoke-count
        # (otomatis mencatat log delta agar grafik per jam akurat)
        set_smoke_total(uid, today_str, initial_smoke, action="inserted_via_onboarding")

        # --- PERBAIKAN 2: GENERATE MULTIPLE DAILY ACTIVITIES ---
        # Siapkan array penampung. Aktivitas dasar yang selalu ada:
        activities_to_add = [
            "Minum air putih minimal 8 gelas hari ini",
            "Lakukan peregangan tubuh ringan selama 10 menit"
        ]

        # RUMUS 1: Berdasarkan jumlah rokok awal
        if initial_smoke > 10:
            activities_to_add.append("Tarik napas dalam 4-7-8 setiap ada hasrat merokok")
            activities_to_add.append("Ganti jadwal merokok dengan mengunyah permen karet")
        elif initial_smoke > 0:
            activities_to_add.append("Tunda merokok 10 menit jika hasrat muncul")

        # RUMUS 2: Berdasarkan riwayat medis (history)
        history = str(data.get("history", "")).lower()
        if "asma" in history:
            activities_to_add.append("Hindari debu dan pastikan inhaler mudah dijangkau")
        elif "hipertensi" in history or "darah tinggi" in history:
            activities_to_add.append("Kurangi konsumsi garam berlebih hari ini")

        # Cari dokumen induk untuk Daily Activities hari ini
        parent_query = (
            db.collection("dailyActivities")
            .where(filter=FieldFilter("userID", "==", uid))
            .where(filter=FieldFilter("timestamp", "==", today_str))
            .limit(1)
            .stream()
        )
        
        parent_doc = None
        for doc in parent_query:
            parent_doc = doc.reference
            break
        
        # Jika belum ada parent dokumen untuk tanggal ini, buat baru
        if not parent_doc:
            _, parent_doc = db.collection("dailyActivities").add({
                "userID": uid,
                "timestamp": today_str
            })
        
        # Looping untuk memasukkan SEMUA aktivitas dari rumus ke subkoleksi
        for act_text in activities_to_add:
            parent_doc.collection("activities").document().set({
                "activity": act_text,
                "done": False,
            })

        return True
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error in process_onboarding: {e}")
        raise e