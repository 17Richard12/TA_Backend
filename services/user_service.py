from config.firebase import db
from schemas.user_schema import RegisterSchema, LoginSchema, EditAccountSchema, ChangePasswordSchema
from fastapi import HTTPException
import hashlib
import uuid
from google.cloud.firestore_v1 import FieldFilter


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