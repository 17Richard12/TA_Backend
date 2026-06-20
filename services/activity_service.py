import os
import json
import uuid
from fastapi import HTTPException
import google.generativeai as genai
from google.cloud.firestore_v1.base_query import FieldFilter # <-- IMPORT PENTING
from config.firebase import db
from schemas.activity_schema import GenerateActivitySchema

DAILY_ACT_COL = "dailyActivities"
ACT_SUB_COL = "activities"

def _new_id() -> str:
    return str(uuid.uuid4()).replace("-", "")[:20]

def _find_daily_activity_doc(user_uid: str, timestamp: str):
    """Mencari apakah record aktivitas untuk user pada tanggal ini sudah ada."""
    docs = (
        db.collection(DAILY_ACT_COL)
        # Gunakan FieldFilter agar pembacaan multiple where akurat di Firebase Admin terbaru
        .where(filter=FieldFilter("userID", "==", user_uid))
        .where(filter=FieldFilter("timestamp", "==", timestamp))
        .limit(1)
        .stream()
    )
    for doc in docs:
        return doc.id, doc.to_dict()
    return None, None

def get_or_create_daily_activities(payload: GenerateActivitySchema) -> dict:
    user_uid = payload.user_uid
    timestamp = payload.timestamp

    # 1. CEK DATA: Cari data berdasarkan userID dan timestamp hari ini
    doc_id, existing_data = _find_daily_activity_doc(user_uid, timestamp)

    if existing_data:
        # JIKA SUDAH ADA: Jangan hit Gemini. Cukup ambil (GET) data dari sub-collection yang sudah ada.
        sub_docs = (
            db.collection(DAILY_ACT_COL).document(doc_id)
            .collection(ACT_SUB_COL)
            .stream()
        )
        
        activities_list = []
        for doc in sub_docs:
            data = doc.to_dict()
            activities_list.append({
                "id": doc.id,
                "activity": data.get("activity"),
                "done": data.get("done", False)
            })
            
        return {
            "id": doc_id,
            "user_uid": user_uid,
            "timestamp": timestamp,
            "action": "fetched_existing", # Status bahwa ini mengambil data yang sudah ada
            "activities": activities_list
        }

    # 2. JIKA BELUM ADA: Baru lakukan fetch ke AI dan Insert ke Firebase
    try:
        smoke_docs = (
            db.collection("smokeCount")
            .where(filter=FieldFilter("userID", "==", user_uid))
            .order_by("timestamp", direction="DESCENDING")
            .limit(5)
            .stream()
        )
        
        total_smokes = 0
        days_counted = 0
        for doc in smoke_docs:
            data = doc.to_dict()
            total_smokes += data.get("count", 0)
            days_counted += 1
    except Exception as e:
        print(f"Error fetching smokeCount: {e}")
        total_smokes = 0
        days_counted = 0

    # Panggil Gemini
    genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
    model = genai.GenerativeModel("gemini-3.5-flash")
    
    prompt = f"""
    Pengguna ini sedang mencoba berhenti merokok.
    Dalam {max(days_counted, 1)} catatan hari terakhir, mereka telah mengonsumsi total {total_smokes} batang rokok.
    Berikan tepat 5 aktivitas harian spesifik, praktis, dan sehat yang dapat dilakukan untuk menggantikan kebiasaan merokok dan mengatasi rasa ingin merokok (craving).
    
    Format output HARUS HANYA berupa array JSON berisi 5 string, tanpa markdown, tanpa penjelasan tambahan.
    Contoh output yang valid: 
    ["Minum air putih perlahan", "Jalan kaki 15 menit", "Latihan napas dalam 5 menit", "Mengunyah permen karet bebas gula", "Mendengarkan musik relaksasi"]
    """

    try:
        response = model.generate_content(prompt)
        raw_text = response.text.strip().strip('```json').strip('```').strip()
        activities_list = json.loads(raw_text)
    except Exception as e:
        print(f"Error calling Gemini or parsing JSON: {e}")
        # Default jika gagal
        activities_list = [
            "Latihan Napas Dalam (Deep Breathing)",
            "Minum segelas air putih perlahan",
            "Jalan kaki singkat selama 10 menit",
            "Mengunyah permen karet bebas gula",
            "Mencuci muka dengan air dingin"
        ]

    # Simpan ke Firestore (Tabel Utama)
    daily_activity_id = _new_id()
    db.collection(DAILY_ACT_COL).document(daily_activity_id).set({
        "userID": user_uid,
        "timestamp": timestamp
    })

    # Simpan ke Sub-collection
    saved_activities = []
    for act in activities_list[:5]:
        act_id = _new_id()
        act_data = {
            "activity": act,
            "done": False
        }
        
        db.collection(DAILY_ACT_COL).document(daily_activity_id) \
          .collection(ACT_SUB_COL).document(act_id).set(act_data)

        saved_activities.append({
            "id": act_id,
            **act_data
        })

    return {
        "id": daily_activity_id,
        "user_uid": user_uid,
        "timestamp": timestamp,
        "action": "generated_and_inserted", # Status bahwa ini dari AI
        "activities": saved_activities
    }