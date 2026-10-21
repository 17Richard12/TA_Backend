import uuid
import datetime
import base64
import google.generativeai as genai
from config.firebase import db
from schemas.photo_schema import PostPhotoSchema
from google.cloud.firestore_v1.base_query import FieldFilter

PHOTOS_COL = "photos"

def analyze_mri_with_gemini(old_base64: str, new_base64: str) -> str:
    try:
        model = genai.GenerativeModel('gemini-3.7-flash')
        old_data = old_base64.split(",")[-1] if "," in old_base64 else old_base64
        new_data = new_base64.split(",")[-1] if "," in new_base64 else new_base64
        
        prompt = """Anda adalah asisten medis AI. Bandingkan dua gambar MRI ini. 
        Gambar 1 adalah MRI sebelumnya (lama), dan Gambar 2 adalah MRI terbaru. 
        Berikan penjelasan singkat (maksimal 3-4 kalimat) tentang perbedaan visual yang terlihat. 
        Akhiri dengan disclaimer singkat bahwa pengguna harus tetap berkonsultasi dengan dokter sungguhan."""
        
        image_parts = [
            {"mime_type": "image/jpeg", "data": base64.b64decode(old_data)},
            {"mime_type": "image/jpeg", "data": base64.b64decode(new_data)},
        ]
        
        response = model.generate_content([prompt, image_parts[0], image_parts[1]])
        return response.text
    except Exception as e:
        return f"Gagal menghasilkan analisis AI: {str(e)}"

def _new_id() -> str:
    return str(uuid.uuid4()).replace("-", "")[:20]

def save_photo(payload: PostPhotoSchema) -> dict:
    # 1. Ambil MRI TERBARU saat ini sebelum data baru disimpan
    docs = (
        db.collection(PHOTOS_COL)
        .where(filter=FieldFilter("userID", "==", payload.user_id))
        .stream()
    )
    
    photos_raw = [doc.to_dict() for doc in docs]
    photos_raw.sort(
        key=lambda x: x.get("timestamp") if x.get("timestamp") else datetime.datetime.min.replace(tzinfo=datetime.timezone.utc), 
        reverse=True
    )
    
    # 2. Proses Analisis AI JIKA ada foto sebelumnya
    analysis_text = "Upload satu gambar MRI lagi untuk melihat perbandingan dan analisis AI."
    if len(photos_raw) > 0:
        old_foto = photos_raw[0].get("foto", "")
        # Lakukan proses Gemini saat upload berjalan
        analysis_text = analyze_mri_with_gemini(
            old_base64=old_foto, 
            new_base64=payload.foto_base64
        )
        
    # 3. Simpan data baru BESERTA hasil analisisnya
    doc_id = _new_id()
    data = {
        "userID": payload.user_id,
        "foto": payload.foto_base64,
        "timestamp": datetime.datetime.now(datetime.timezone.utc),
        "analysis_result": analysis_text  # <-- Simpan ke Firestore
    }
    
    db.collection(PHOTOS_COL).document(doc_id).set(data)
    
    return {
        "id": doc_id,
        "action": "inserted",
        "message": "Foto MRI berhasil disimpan dan dianalisis"
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
                "timestamp": data.get("timestamp"),
                "analysis_result": data.get("analysis_result", "") # Ambil field analisis dari DB
            })
            
        photos_raw.sort(
            key=lambda x: x["timestamp"] if x["timestamp"] else datetime.datetime.min.replace(tzinfo=datetime.timezone.utc), 
            reverse=True
        )
        
        photos = []
        for p in photos_raw[:2]: 
            ts = p["timestamp"]
            if isinstance(ts, datetime.datetime):
                ts = ts.isoformat()
            
            photos.append({
                "id": p["id"],
                "foto": p["foto"],
                "timestamp": ts
            })
            
        # 4. Ambil teks analisis langsung dari dokumen MRI terbaru
        analysis_text = ""
        if len(photos_raw) > 0:
            analysis_text = photos_raw[0].get("analysis_result", "Upload satu gambar MRI lagi untuk melihat perbandingan dan analisis AI.")
        else:
            analysis_text = "Belum ada data MRI yang diupload."
            
        return {
            "status": "success",
            "data": {
                "user_id": user_id,
                "photos": photos,
                "analysis": analysis_text  # Response ke Frontend tetap sama
            }
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e)
        }