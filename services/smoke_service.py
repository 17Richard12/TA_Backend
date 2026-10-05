import uuid
from config.firebase import db
from schemas.smoke_schema import SmokeCountSchema
from google.cloud.firestore_v1.base_query import FieldFilter
from datetime import datetime, timedelta, timezone

SMOKE_COL = "smokeCount"

def _new_id() -> str:
    return str(uuid.uuid4()).replace("-", "")[:20]

from google.cloud.firestore_v1.base_query import FieldFilter

def _find_smoke_doc(user_id: str, timestamp: str):
    """Mencari dokumen berdasarkan userID dan timestamp."""
    docs = (
        db.collection(SMOKE_COL)
        .where(filter=FieldFilter("userID", "==", user_id))
        .where(filter=FieldFilter("timestamp", "==", timestamp))
        .limit(1)
        .stream()
    )
    for doc in docs:
        return doc.id, doc.to_dict()
    return None, None

def post_smoke_count(payload: SmokeCountSchema) -> dict:
    # Cari apakah data hari ini dengan userID ini sudah ada
    doc_id, existing_data = _find_smoke_doc(payload.user_id, payload.timestamp)
    
    if existing_data:
        # Jika data sudah ada, langsung belokkan ke fungsi update
        return update_smoke_count(payload)
        
    # Jika data belum ada, lakukan POST (buat baru)
    new_doc_id = _new_id()
    db.collection(SMOKE_COL).document(new_doc_id).set({
        "userID": payload.user_id,
        "timestamp": payload.timestamp,
        "count": payload.total  # Disimpan sebagai 'count' sesuai struktur gambar Firestore
    })
    
    # Simpan logdate di subcollection
    log_id = _new_id()
    db.collection(SMOKE_COL).document(new_doc_id).collection("logs").document(log_id).set({
        "logdate": datetime.now(timezone.utc)
    })
    
    return {
        "id": new_doc_id,
        "action": "inserted",
        "message": "Data smoke count baru berhasil ditambahkan"
    }

def update_smoke_count(payload: SmokeCountSchema) -> dict:
    doc_id, existing_data = _find_smoke_doc(payload.user_id, payload.timestamp)
    
    if not doc_id:
        # Jika frontend menembak endpoint update tapi data belum ada, otomatis buat baru
        new_doc_id = _new_id()
        db.collection(SMOKE_COL).document(new_doc_id).set({
            "userID": payload.user_id,
            "timestamp": payload.timestamp,
            "count": payload.total
        })
        
        log_id = _new_id()
        db.collection(SMOKE_COL).document(new_doc_id).collection("logs").document(log_id).set({
            "logdate": datetime.now(timezone.utc)
        })

        return {
            "id": new_doc_id,
            "action": "inserted_via_update",
            "message": "Data belum ada, otomatis membuat data baru"
        }
        
    # Jika data sudah ada, lakukan update saja pada field count
    db.collection(SMOKE_COL).document(doc_id).update({
        "count": payload.total
    })
    
    log_id = _new_id()
    db.collection(SMOKE_COL).document(doc_id).collection("logs").document(log_id).set({
        "logdate": datetime.now(timezone.utc)
    })
    
    return {
        "id": doc_id,
        "action": "updated",
        "message": "Data smoke count berhasil diperbarui"
    }

def get_smoke_count(user_id: str, timestamp: str) -> dict:
    """Mengambil data smoke count berdasarkan userID dan timestamp harian."""
    doc_id, existing_data = _find_smoke_doc(user_id, timestamp)
    
    if not existing_data:
        # Jika data belum ada untuk hari ini, kembalikan default 0 agar frontend tidak error
        return {
            "id": None,
            "userID": user_id,
            "timestamp": timestamp,
            "count": 0
        }
        
    return {
        "id": doc_id,
        "userID": existing_data.get("userID"),
        "timestamp": existing_data.get("timestamp"),
        "count": existing_data.get("count", 0)
    }

def get_weekly_report(user_id: str, end_timestamp: str) -> list:
    try:
        # 1. Konversi input (dd/mm/yyyy dari frontend/postman) menjadi objek datetime
        end_date = datetime.strptime(end_timestamp, "%d/%m/%Y")
    except ValueError:
        raise ValueError("Format tanggal tidak valid. Gunakan dd/mm/yyyy")

    # 2. Buat list tanggal untuk Query ke Database (Wajib YYYY-MM-DD sesuai screenshot)
    date_list_db = [(end_date - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
    date_list_db.reverse() 
    
    # Cetak ke terminal untuk memastikan format yang dicari benar
    print("Mencari di DB dengan tanggal:", date_list_db)

    # Buat list tanggal untuk ditampilkan ke Response JSON (Format DD/MM/YYYY)
    date_list_display = [(end_date - timedelta(days=i)).strftime("%d/%m/%Y") for i in range(7)]
    date_list_display.reverse()

    # 3. Query ke Firestore
    docs = (
        db.collection(SMOKE_COL)
        .where(filter=FieldFilter("userID", "==", user_id))
        .where(filter=FieldFilter("timestamp", "in", date_list_db))
        .stream()
    )

    # 4. Mapping data hasil pencarian
    db_data = {}
    for doc in docs:
        data = doc.to_dict()
        # Menyimpan data dengan key format YYYY-MM-DD
        db_data[data["timestamp"]] = data.get("count", 0)

    # 5. Gabungkan menjadi array response akhir
    report_data = []
    for i in range(7):
        db_format = date_list_db[i]           # contoh: "2026-09-24"
        display_format = date_list_display[i] # contoh: "24/09/2026"
        
        report_data.append({
            "timestamp": display_format, 
            "count": db_data.get(db_format, 0) 
        })

    return report_data