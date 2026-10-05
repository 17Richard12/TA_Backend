import uuid
import datetime
from config.firebase import db
from schemas.photo_schema import PostPhotoSchema
from google.cloud.firestore_v1.base_query import FieldFilter

PHOTOS_COL = "photos"

def _new_id() -> str:
    return str(uuid.uuid4()).replace("-", "")[:20]

def save_photo(payload: PostPhotoSchema) -> dict:
    doc_id = _new_id()
    
    # Menggunakan datetime timezone UTC agar sesuai format Timestamp di Firestore
    data = {
        "userID": payload.user_id,
        "foto": payload.foto_base64,
        "timestamp": datetime.datetime.now(datetime.timezone.utc)
    }
    
    db.collection(PHOTOS_COL).document(doc_id).set(data)
    
    return {
        "id": doc_id,
        "action": "inserted",
        "message": "Foto MRI berhasil disimpan"
    }

def get_latest_two_photos(user_id: str) -> dict:
    try:
        docs = (
            db.collection(PHOTOS_COL)
            .where(filter=FieldFilter("userID", "==", user_id))
            .stream()
        )
        
        photos_raw = []
        for doc in docs:
            data = doc.to_dict()
            photos_raw.append({
                "id": doc.id,
                "foto": data.get("foto", ""),
                "timestamp": data.get("timestamp")
            })
            
        # Urutkan berdasarkan timestamp menurun
        photos_raw.sort(
            key=lambda x: x["timestamp"] if x["timestamp"] else datetime.datetime.min.replace(tzinfo=datetime.timezone.utc), 
            reverse=True
        )
        
        photos = []
        for p in photos_raw[:2]: # Ambil 2 teratas
            ts = p["timestamp"]
            if isinstance(ts, datetime.datetime):
                ts = ts.isoformat()
            
            photos.append({
                "id": p["id"],
                "foto": p["foto"],
                "timestamp": ts
            })
            
        return {
            "status": "success",
            "data": {
                "user_id": user_id,
                "photos": photos
            }
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e)
        }
