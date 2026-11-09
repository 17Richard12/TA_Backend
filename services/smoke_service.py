import uuid
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException
from google.cloud.firestore_v1.base_query import FieldFilter
from config.firebase import db
from schemas.smoke_schema import SmokeCountSchema, normalize_smoke_date_str

SMOKE_COL = "smokeCount"
JAKARTA_TZ = timezone(timedelta(hours=7))


def _new_id() -> str:
    return str(uuid.uuid4()).replace("-", "")[:20]


def jakarta_today_str() -> str:
    """Tanggal hari ini (YYYY-MM-DD) berdasarkan zona waktu WIB, bukan zona waktu server (UTC)."""
    return datetime.now(JAKARTA_TZ).strftime("%Y-%m-%d")


def _deterministic_doc_id(user_id: str, timestamp: str) -> str:
    """ID dokumen deterministik agar request paralel tidak membuat dokumen ganda untuk hari yang sama."""
    return f"{user_id}_{timestamp}"


def _find_smoke_doc(user_id: str, timestamp: str):
    """Mencari dokumen berdasarkan userID dan timestamp (dinormalisasi ke YYYY-MM-DD)."""
    normalized_ts = normalize_smoke_date_str(timestamp)
    # 1. Cek ID deterministik terlebih dahulu (data baru)
    det_ref = db.collection(SMOKE_COL).document(_deterministic_doc_id(user_id, normalized_ts))
    det_doc = det_ref.get()
    if det_doc.exists:
        return det_doc.id, det_doc.to_dict()

    # 2. Fallback ke query (data lama dengan ID acak)
    docs = (
        db.collection(SMOKE_COL)
        .where(filter=FieldFilter("userID", "==", user_id))
        .where(filter=FieldFilter("timestamp", "==", normalized_ts))
        .limit(1)
        .stream()
    )
    for doc in docs:
        return doc.id, doc.to_dict()
    return None, None


def find_smoke_doc_ref(user_id: str, timestamp: str):
    """Helper publik untuk service lain (dashboard / laporan dokter)."""
    doc_id, data = _find_smoke_doc(user_id, timestamp)
    if not doc_id:
        return None, None
    return db.collection(SMOKE_COL).document(doc_id), data


def aggregate_hourly_logs(doc_ref) -> dict:
    """
    Menghitung grafik per jam (WIB) dari subcollection logs.
    - Log baru menyimpan field `amount` (delta, bisa negatif saat dikurangi).
    - Log lama tanpa `amount` dihitung sebagai +1.
    """
    hourly = {f"{i:02d}:00": 0 for i in range(24)}
    if doc_ref is None:
        return hourly

    for log in doc_ref.collection("logs").stream():
        log_data = log.to_dict() or {}
        logdate = log_data.get("logdate") or log_data.get("timestamp")
        if not logdate:
            continue
        try:
            if isinstance(logdate, str):
                logdate = datetime.fromisoformat(logdate.replace("Z", "+00:00"))
            if logdate.tzinfo is None:
                logdate = logdate.replace(tzinfo=timezone.utc)
            key = f"{logdate.astimezone(JAKARTA_TZ).hour:02d}:00"
            amount = log_data.get("amount", 1)
            hourly[key] += int(amount) if isinstance(amount, (int, float)) else 1
        except Exception as e:
            print(f"[DEBUG] Error parsing logdate '{logdate}': {e}")

    # Jam dengan pengurangan bersih tidak boleh negatif di grafik
    return {k: max(v, 0) for k, v in hourly.items()}


def set_smoke_total(user_id: str, timestamp: str, new_total: int, action: str = "updated") -> dict:
    """
    Menyimpan total rokok harian dan mencatat log berisi delta perubahan.
    Dipakai oleh POST/PUT smoke-count dan proses onboarding.
    """
    if int(new_total) < 0 or int(new_total) > 200:
        raise HTTPException(
            status_code=400,
            detail="Jumlah rokok harus bernilai antara 0 hingga 200.",
        )

    normalized_ts = normalize_smoke_date_str(timestamp)
    new_total = int(new_total)
    doc_id, existing_data = _find_smoke_doc(user_id, normalized_ts)

    if existing_data:
        previous_total = int(existing_data.get("count", 0) or 0)
        doc_ref = db.collection(SMOKE_COL).document(doc_id)
        doc_ref.update({"count": new_total, "updatedAt": datetime.now(timezone.utc)})
        result_action = action
    else:
        previous_total = 0
        doc_id = _deterministic_doc_id(user_id, normalized_ts)
        doc_ref = db.collection(SMOKE_COL).document(doc_id)
        doc_ref.set({
            "userID": user_id,
            "timestamp": normalized_ts,
            "count": new_total,
            "updatedAt": datetime.now(timezone.utc),
        }, merge=True)
        result_action = "inserted"

    delta = new_total - previous_total
    if delta != 0:
        doc_ref.collection("logs").document(_new_id()).set({
            "logdate": datetime.now(timezone.utc),
            "action": action,
            "amount": delta,
            "previous_total": previous_total,
            "new_total": new_total,
        })

    return {
        "id": doc_id,
        "action": result_action,
        "count": new_total,
        "previous_count": previous_total,
        "message": "Data smoke count berhasil disimpan",
    }


def post_smoke_count(payload: SmokeCountSchema) -> dict:
    return set_smoke_total(payload.user_id, payload.timestamp, payload.total)


def update_smoke_count(payload: SmokeCountSchema) -> dict:
    return set_smoke_total(payload.user_id, payload.timestamp, payload.total)


def get_smoke_count(user_id: str, timestamp: str) -> dict:
    """Mengambil data smoke count berdasarkan userID dan timestamp harian."""
    normalized_ts = normalize_smoke_date_str(timestamp)
    doc_id, existing_data = _find_smoke_doc(user_id, normalized_ts)

    if not existing_data:
        return {
            "id": None,
            "userID": user_id,
            "timestamp": normalized_ts,
            "count": 0,
        }

    return {
        "id": doc_id,
        "userID": existing_data.get("userID"),
        "timestamp": existing_data.get("timestamp"),
        "count": max(0, int(existing_data.get("count", 0) or 0)),
    }


def get_weekly_report(user_id: str, end_timestamp: str) -> list:
    cleaned = (end_timestamp or "").strip()
    end_date = None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            end_date = datetime.strptime(cleaned, fmt)
            break
        except ValueError:
            continue
    if end_date is None:
        raise ValueError("Format tanggal tidak valid. Gunakan dd/mm/yyyy atau YYYY-MM-DD")

    date_list_db = [(end_date - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
    date_list_db.reverse()

    date_list_display = [(end_date - timedelta(days=i)).strftime("%d/%m/%Y") for i in range(7)]
    date_list_display.reverse()

    docs = (
        db.collection(SMOKE_COL)
        .where(filter=FieldFilter("userID", "==", user_id))
        .where(filter=FieldFilter("timestamp", "in", date_list_db))
        .stream()
    )

    db_data = {}
    for doc in docs:
        data = doc.to_dict() or {}
        ts = data.get("timestamp")
        if ts:
            db_data[ts] = max(0, int(data.get("count", 0) or 0))

    report_data = []
    for i in range(7):
        db_format = date_list_db[i]
        display_format = date_list_display[i]

        report_data.append({
            "timestamp": display_format,
            "count": db_data.get(db_format, 0),
        })

    return report_data
