from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import os
from typing import Any, Dict, Optional, Tuple

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from config.firebase import db

# Optional Argon2 support if argon2-cffi is installed in the environment
try:
    from argon2 import PasswordHasher
    from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError
    _argon2_hasher = PasswordHasher()
except ImportError:
    _argon2_hasher = None

# Use JWT_SECRET_KEY from environment, or derive a deterministic local secret
# without exposing or logging sensitive environment variables.
_raw_secret = os.getenv("JWT_SECRET_KEY") or os.getenv("SECRET_KEY")
if not _raw_secret:
    _fallback_seed = f"lungs-assistant-jwt-secret::{os.getenv('GOOGLE_API_KEY', 'local-dev-key')}"
    _raw_secret = hashlib.sha256(_fallback_seed.encode("utf-8")).hexdigest()

JWT_SECRET_KEY: str = _raw_secret
JWT_ALGORITHM: str = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_HOURS", "168"))  # 7 days default

bearer_scheme = HTTPBearer(auto_error=False)

ADMIN_ROLES = {"master", "admin"}
DOCTOR_ROLES = {"dokter", "doctor"}
PATIENT_ROLES = {"user", "patient"}


def hash_password(password: str) -> str:
    """
    Hash a plaintext password using Argon2id (if available) or bcrypt (cost=12).
    Never logs or exposes plaintext passwords or hashes.
    """
    if _argon2_hasher is not None:
        return _argon2_hasher.hash(password)
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def _is_legacy_sha256_hash(stored_hash: str) -> bool:
    if len(stored_hash) != 64:
        return False
    return all(c in "0123456789abcdefABCDEF" for c in stored_hash)


def verify_password(plain_password: str, stored_hash: str) -> Tuple[bool, bool]:
    """
    Verify a plaintext password against a stored hash.
    Returns (is_valid, needs_rehash).
    Supports:
    - Argon2id ($argon2id$...)
    - bcrypt ($2a$, $2b$, $2y$)
    - Legacy 64-char hex SHA-256 (for backward compatibility with existing accounts;
      marks needs_rehash=True so accounts can be migrated without data loss).
    """
    if not plain_password or not stored_hash:
        return False, False

    if stored_hash.startswith("$argon2"):
        if _argon2_hasher is None:
            return False, False
        try:
            is_valid = _argon2_hasher.verify(stored_hash, plain_password)
            needs_rehash = _argon2_hasher.check_needs_rehash(stored_hash)
            return is_valid, needs_rehash
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False, False

    if stored_hash.startswith(("$2a$", "$2b$", "$2y$")):
        try:
            is_valid = bcrypt.checkpw(
                plain_password.encode("utf-8"),
                stored_hash.encode("utf-8"),
            )
            needs_rehash = _argon2_hasher is not None
            return is_valid, needs_rehash
        except ValueError:
            return False, False

    if _is_legacy_sha256_hash(stored_hash):
        candidate_sha256 = hashlib.sha256(plain_password.encode("utf-8")).hexdigest()
        is_valid = hmac.compare_digest(candidate_sha256.lower(), stored_hash.lower())
        return is_valid, is_valid

    return False, False


def create_access_token(
    uid: str,
    role: str = "user",
    expires_delta: Optional[timedelta] = None,
) -> str:
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS))
    payload = {
        "sub": str(uid),
        "role": str(role).lower().strip(),
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> Dict[str, Any]:
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        uid = payload.get("sub")
        if not uid:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication token.",
            )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired. Please log in again.",
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token.",
        )


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> Dict[str, Any]:
    """
    FastAPI dependency that validates the Bearer token and fetches the current user's
    authoritative role and metadata from Firestore.
    """
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )

    payload = decode_access_token(credentials.credentials)
    uid = payload["sub"]

    doc = db.collection("accounts").document(uid).get()
    if not doc.exists:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authenticated user account no longer exists.",
        )

    user_data = doc.to_dict() or {}
    role = str(user_data.get("role", "user")).lower().strip()
    return {
        "uid": uid,
        "role": role,
        "email": user_data.get("email", ""),
        "name": user_data.get("name", ""),
        "assigned_patients": user_data.get("assigned_patients")
        or user_data.get("authorized_patients")
        or [],
    }


def is_admin(current_user: Dict[str, Any]) -> bool:
    return str(current_user.get("role", "")).lower() in ADMIN_ROLES


def is_doctor(current_user: Dict[str, Any]) -> bool:
    return str(current_user.get("role", "")).lower() in DOCTOR_ROLES


def require_admin(current_user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    if not is_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only administrator accounts are authorized to perform this action.",
        )
    return current_user


def is_doctor_authorized_for_patient(
    doctor_user: Dict[str, Any],
    patient_uid: str,
    patient_data: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Checks whether a doctor is explicitly authorized to manage a specific patient.
    Authorization is granted ONLY if:
    1. The patient has role 'user'/'patient', AND
    2. Either:
       - The patient document explicitly lists the doctor's UID in
         `assigned_doctor_uid`, `doctor_uid`, `authorized_doctors`, or `assigned_doctors`, OR
       - The doctor document explicitly lists the patient's UID in
         `assigned_patients` or `authorized_patients`.
    """
    doctor_uid = doctor_user.get("uid")
    if not doctor_uid or not is_doctor(doctor_user):
        return False

    if patient_data is None:
        patient_doc = db.collection("accounts").document(patient_uid).get()
        if not patient_doc.exists:
            return False
        patient_data = patient_doc.to_dict() or {}

    patient_role = str(patient_data.get("role", "user")).lower().strip()
    if patient_role not in PATIENT_ROLES:
        return False

    # Check doctor's explicit patient list
    doctor_patients = doctor_user.get("assigned_patients") or []
    if isinstance(doctor_patients, list) and patient_uid in doctor_patients:
        return True

    # Check patient's explicit doctor fields
    if patient_data.get("assigned_doctor_uid") == doctor_uid:
        return True
    if patient_data.get("doctor_uid") == doctor_uid:
        return True

    auth_doctors = (
        patient_data.get("authorized_doctors")
        or patient_data.get("assigned_doctors")
        or []
    )
    if isinstance(auth_doctors, list) and doctor_uid in auth_doctors:
        return True

    return False


def assert_self_access(current_user: Dict[str, Any], target_uid: str) -> None:
    if current_user.get("uid") != target_uid:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are only authorized to modify your own account.",
        )


def assert_self_or_admin(current_user: Dict[str, Any], target_uid: str) -> None:
    if current_user.get("uid") == target_uid or is_admin(current_user):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You are not authorized to access or modify this account.",
    )


def assert_patient_access(
    current_user: Dict[str, Any],
    target_uid: str,
    patient_data: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Enforces that `target_uid` can only be accessed by:
    - The user themselves (`current_user['uid'] == target_uid`)
    - An Admin (`master` / `admin`)
    - A Doctor (`dokter` / `doctor`) who is explicitly authorized to manage `target_uid`
    """
    if current_user.get("uid") == target_uid:
        return
    if is_admin(current_user):
        return
    if is_doctor(current_user) and is_doctor_authorized_for_patient(
        current_user, target_uid, patient_data=patient_data
    ):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You are not authorized to access this patient's information.",
    )
