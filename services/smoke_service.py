import uuid
from config.firebase import db
from schemas.smoke_schema import SmokeCountSchema

SMOKE_COL = "smokeCount"

def _new_id() -> str:
    return str(uuid.uuid4()).replace("-", "")[:20]

def _find_smoke_doc(user_id: str, timestamp: str):
    """Mencari dokumen berdasarkan userID dan timestamp."""
    docs = (
        db.collection(SMOKE_COL)
        .where("userID", "==", user_id)
        .where("timestamp", "==", timestamp)
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
        return {
            "id": new_doc_id,
            "action": "inserted_via_update",
            "message": "Data belum ada, otomatis membuat data baru"
        }
        
    # Jika data sudah ada, lakukan update saja pada field count
    db.collection(SMOKE_COL).document(doc_id).update({
        "count": payload.total
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