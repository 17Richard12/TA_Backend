from config.firebase import db
from schemas.user_schema import RegisterSchema, LoginSchema, EditAccountSchema, ChangePasswordSchema
from fastapi import HTTPException
import hashlib
import uuid
from google.cloud.firestore_v1 import FieldFilter
from datetime import timedelta


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
    }
    db.collection(ACCOUNTS_COL).document(new_uid).set(user_data)
    return {"uid": new_uid, **user_data}


def login_user(payload: LoginSchema) -> dict:
    uid, user = _get_user_by_email(payload.email)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.get("password") != _hash_password(payload.password):
        raise HTTPException(status_code=401, detail="Invalid password")

    return {"uid": uid, **user}


def get_user(uid: str) -> dict:
    user = _get_user_by_uid(uid)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {"uid": uid, **user}


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

    # 2. Ambil Total Smoke Count + Hourly Graph
    smoke_hourly_dict = {f"{i:02d}:00": 0 for i in range(24)}

    smoke_docs = list(
        db.collection("smokeCount")
        .where(filter=FieldFilter("userID", "==", uid))
        .where(filter=FieldFilter("timestamp", "==", date_str))
        .limit(1)
        .stream()
    )

    total_smoke = 0
    if smoke_docs:
        smoke_doc = smoke_docs[0]
        total_smoke = smoke_doc.to_dict().get("count", 0)

        # Ambil subcollection logs untuk grafik per jam
        logs = smoke_doc.reference.collection("logs").stream()
        for log in logs:
            log_data = log.to_dict()
            
            # Cek properti logdate atau timestamp untuk fleksibilitas
            logdate = log_data.get("logdate") or log_data.get("timestamp")
            
            if logdate:
                try:
                    # Parsing data jika Flutter mengirimkan Date String (ISO 8601)
                    if isinstance(logdate, str):
                        # Handle 'Z' suffix yang sering dikirim oleh Dart toIso8601String()
                        parsed_date = datetime.fromisoformat(logdate.replace('Z', '+00:00'))
                    else:
                        # Asumsi objek sudah berupa Firestore Datetime / Python datetime
                        parsed_date = logdate

                    # Konversi ke waktu lokal UTC+7
                    local_time = parsed_date + timedelta(hours=7)
                    key = f"{local_time.hour:02d}:00"
                    
                    if key in smoke_hourly_dict:
                        smoke_hourly_dict[key] += 1

                except Exception as e:
                    # Munculkan error di terminal untuk keperluan debugging (jangan di-pass saja)
                    print(f"[DEBUG] Error parsing logdate '{logdate}' untuk user {uid}: {e}")

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