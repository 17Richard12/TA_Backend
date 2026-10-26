import uuid
import datetime
import base64
import google.generativeai as genai
from fastapi import BackgroundTasks # <-- Tambahkan import ini
from config.firebase import db
from schemas.photo_schema import PostPhotoSchema
from google.cloud.firestore_v1.base_query import FieldFilter

PHOTOS_COL = "photos"

# Pastikan API Key diisi, atau gunakan environment variable
genai.configure(api_key="AIzaSyD-UCOZbYMrpCjFlN3PZ7KCNUcfdTV75Fs")  

def analyze_mri_with_gemini(old_base64: str, new_base64: str) -> str:
    try:
        # Menggunakan versi model yang valid
        model = genai.GenerativeModel('gemini-3.7`-flash') 
        old_data = old_base64.split(",")[-1] if "," in old_base64 else old_base64
        new_data = new_base64.split(",")[-1] if "," in new_base64 else new_base64
        
        # PROMPT DIREVISI: Lebih pendek, tegas, dan fokus pada kondisi medis
        prompt = """Anda adalah asisten medis AI. Bandingkan dua gambar MRI ini (Gambar 1: Lama, Gambar 2: Terbaru). 
        Berikan analisis maksimal 2 kalimat yang HANYA FOKUS pada perubahan kondisi fisik jaringan/organ yang terlihat (misal: massa, lesi, atau kelainan). 
        Abaikan perbedaan resolusi, kecerahan, ukuran, atau penanda digital pada gambar. 
        Tutup dengan: '*Catatan: Ini bukan diagnosis medis. Harap konsultasikan dengan dokter.*'"""
        
        image_parts = [
            {"mime_type": "image/jpeg", "data": base64.b64decode(old_data)},
            {"mime_type": "image/jpeg", "data": base64.b64decode(new_data)},
        ]
        
        response = model.generate_content([prompt, image_parts[0], image_parts[1]])
        return response.text
    except Exception as e:
        return f"Gagal menghasilkan analisis : {str(e)}"

# FUNGSI BARU: Untuk menjalankan analisis Gemini di latar belakang
def background_analyze_mri(doc_id: str, old_base64: str, new_base64: str):
    analysis_text = analyze_mri_with_gemini(old_base64, new_base64)
    # Update dokumen MRI di Firestore setelah Gemini selesai berpikir
    db.collection(PHOTOS_COL).document(doc_id).update({
        "analysis_result": analysis_text
    })

def _new_id() -> str:
    return str(uuid.uuid4()).replace("-", "")[:20]

# FUNGSI DIREVISI: Tambahkan parameter background_tasks
def save_photo(payload: PostPhotoSchema, background_tasks: BackgroundTasks) -> dict:
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
    
    doc_id = _new_id()
    
    # Simpan data MRI DENGAN status sementara
    data = {
        "userID": payload.user_id,
        "foto": payload.foto_base64,
        "timestamp": datetime.datetime.now(datetime.timezone.utc),
        "analysis_result": "Sedang dianalisis oleh Agent... Silakan muat ulang (refresh) halaman beberapa saat lagi." 
    }
    
    db.collection(PHOTOS_COL).document(doc_id).set(data)
    
    # Lempar tugas Gemini ke Background Task agar upload langsung selesai
    if len(photos_raw) > 0:
        old_foto = photos_raw[0].get("foto", "")
        background_tasks.add_task(background_analyze_mri, doc_id, old_foto, payload.foto_base64)
    
    return {
        "id": doc_id,
        "action": "inserted",
        "message": "Foto MRI berhasil diupload. Analisis AI sedang berjalan."
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
                "analysis": analysis_text
            }
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e)
        }