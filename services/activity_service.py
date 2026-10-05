import os
import json
import uuid
from fastapi import HTTPException
import google.generativeai as genai
from google.cloud.firestore_v1.base_query import FieldFilter # <-- IMPORT PENTING
from config.firebase import db
from schemas.activity_schema import GenerateActivitySchema
from datetime import datetime, timedelta

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
        from datetime import datetime
        smoke_docs = (
            db.collection("smokeCount")
            .where(filter=FieldFilter("userID", "==", user_uid))
            .stream()
        )
        
        # Ambil semua data smoke count user ini
        smoke_records = []
        for doc in smoke_docs:
            data = doc.to_dict()
            doc_ts = data.get("timestamp", "")
            try:
                # Format dari database ternyata adalah YYYY-MM-DD
                dt = datetime.strptime(doc_ts, "%Y-%m-%d")
                smoke_records.append({"date": dt, "count": data.get("count", 0)})
            except ValueError:
                continue
                
        # Urutkan berdasarkan tanggal menurun (terbaru ke terlama)
        smoke_records.sort(key=lambda x: x["date"], reverse=True)
        
        # Konversi timestamp hari ini ke datetime
        today_dt = datetime.strptime(timestamp, "%Y-%m-%d")
        
        smoke_count_yesterday = 0
        for record in smoke_records:
            # Ambil record pertama yang tanggalnya sebelum hari ini
            if record["date"] < today_dt:
                smoke_count_yesterday = record["count"]
                break
                
    except Exception as e:
        print(f"Error fetching smokeCount: {e}")
        smoke_count_yesterday = 0

    target_activities_count = smoke_count_yesterday + 1

    # Panggil Gemini
    genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
    model = genai.GenerativeModel("gemini-3.5-flash")
    
    prompt = f"""
    Pengguna ini sedang mencoba berhenti merokok.
    Kemarin, mereka telah mengonsumsi total {smoke_count_yesterday} batang rokok.
    Berikan tepat {target_activities_count} aktivitas harian spesifik, praktis, dan sehat yang dapat dilakukan hari ini untuk menggantikan kebiasaan merokok dan mengatasi rasa ingin merokok (craving).
    
    Format output HARUS HANYA berupa array JSON berisi {target_activities_count} string, tanpa markdown, tanpa penjelasan tambahan.
    Contoh output yang valid jika diminta 2 aktivitas: 
    ["Minum air putih perlahan", "Jalan kaki 15 menit"]
    """

    try:
        response = model.generate_content(prompt)
        raw_text = response.text.strip().strip('```json').strip('```').strip()
        activities_list = json.loads(raw_text)
    except Exception as e:
        print(f"Error calling Gemini or parsing JSON: {e}")
        # Default jika gagal (sejumlah target_activities_count)
        default_acts = [
            "Latihan Napas Dalam (Deep Breathing)",
            "Minum segelas air putih perlahan",
            "Jalan kaki singkat selama 10 menit",
            "Mengunyah permen karet bebas gula",
            "Mencuci muka dengan air dingin",
            "Mendengarkan musik relaksasi",
            "Membaca buku 15 menit"
        ]
        activities_list = default_acts[:target_activities_count]
        # Jika kurang dari target, ulangi beberapa aktivitas
        while len(activities_list) < target_activities_count:
            activities_list.append("Minum air putih tambahan")

    # Simpan ke Firestore (Tabel Utama)
    daily_activity_id = _new_id()
    db.collection(DAILY_ACT_COL).document(daily_activity_id).set({
        "userID": user_uid,
        "timestamp": timestamp
    })

    # Simpan ke Sub-collection
    saved_activities = []
    for act in activities_list[:target_activities_count]:
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

def update_activity_status(daily_activity_id: str, activity_id: str, is_done: bool) -> dict:
    """Mengupdate status done pada sub-collection activities."""
    activity_ref = db.collection(DAILY_ACT_COL).document(daily_activity_id) \
                     .collection(ACT_SUB_COL).document(activity_id)

    # Cek apakah dokumen ada
    activity_doc = activity_ref.get()
    if not activity_doc.exists:
        return None

    # Update field 'done'
    activity_ref.update({
        'done': is_done
    })

    return {
        "daily_activity_id": daily_activity_id,
        "activity_id": activity_id,
        "done": is_done
    }

def get_weekly_activity_report(user_id: str, end_timestamp: str) -> list:
    """
    Mengambil data jumlah aktivitas yang selesai (done == True) selama 7 hari ke belakang.
    Format input end_timestamp: dd/mm/yyyy
    """
    try:
        # Konversi input (dd/mm/yyyy) menjadi objek datetime
        end_date = datetime.strptime(end_timestamp, "%d/%m/%Y")
    except ValueError:
        raise ValueError("Format tanggal tidak valid. Gunakan dd/mm/yyyy")

    # Buat list tanggal untuk Query ke Database (Format YYYY-MM-DD sesuai struktur database)
    date_list_db = [(end_date - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
    date_list_db.reverse() 
    
    # Buat list tanggal untuk ditampilkan ke Response JSON (Format DD/MM/YYYY)
    date_list_display = [(end_date - timedelta(days=i)).strftime("%d/%m/%Y") for i in range(7)]
    date_list_display.reverse()

    # Query ke Firestore untuk mencari dokumen harian dalam 7 hari terakhir
    docs = (
        db.collection(DAILY_ACT_COL)
        .where(filter=FieldFilter("userID", "==", user_id))
        .where(filter=FieldFilter("timestamp", "in", date_list_db))
        .stream()
    )

    db_data = {}
    for doc in docs:
        data = doc.to_dict()
        timestamp_db = data.get("timestamp")
        
        # Ambil sub-collection 'activities' pada dokumen harian ini
        # Dan filter HANYA yang status done == True
        sub_docs = (
            doc.reference.collection(ACT_SUB_COL)
            .where(filter=FieldFilter("done", "==", True))
            .stream()
        )
        
        # Hitung jumlah aktivitas yang selesai
        done_count = sum(1 for _ in sub_docs)
        db_data[timestamp_db] = done_count

    # Gabungkan menjadi array response
    report_data = []
    for i in range(7):
        db_format = date_list_db[i]           
        display_format = date_list_display[i] 
        
        report_data.append({
            "timestamp": display_format, 
            "count": db_data.get(db_format, 0) # Jika tidak ada data, hitungan selesai = 0
        })

    return report_data

def get_activity_streak(user_id: str, today_timestamp: str) -> int:
    """
    Menghitung jumlah hari berturut-turut user menyelesaikan minimal 1 aktivitas.
    Input today_timestamp: dd/mm/yyyy
    """
    try:
        today_date = datetime.strptime(today_timestamp, "%d/%m/%Y")
    except ValueError:
        raise ValueError("Format tanggal tidak valid. Gunakan dd/mm/yyyy")

    streak_count = 0
    current_date = today_date

    while True:
        # Format ke bentuk DB (YYYY-MM-DD)
        date_str_db = current_date.strftime("%Y-%m-%d")
        
        # Cari dokumen harian untuk tanggal yang sedang dicek
        docs = (
            db.collection(DAILY_ACT_COL)
            .where(filter=FieldFilter("userID", "==", user_id))
            .where(filter=FieldFilter("timestamp", "==", date_str_db))
            .limit(1)
            .stream()
        )
        
        doc_found = None
        for d in docs:
            doc_found = d
            break
            
        if not doc_found:
            # Jika hari ini tidak ada dokumen, cek hari sebelumnya.
            # Tapi jika dokumen yang hilang adalah kemarin atau sebelumnya, streak putus.
            if current_date == today_date:
                current_date -= timedelta(days=1)
                continue
            else:
                break
        
        # Jika dokumen harian ada, cek apakah ada minimal 1 aktivitas yang 'done'
        sub_docs = (
            doc_found.reference.collection(ACT_SUB_COL)
            .where(filter=FieldFilter("done", "==", True))
            .limit(1) # Cukup cari 1 saja agar query cepat
            .stream()
        )
        
        has_done = False
        for _ in sub_docs:
            has_done = True
            break
            
        if has_done:
            # Ada aktivitas selesai, streak bertambah, lanjut cek hari sebelumnya
            streak_count += 1
            current_date -= timedelta(days=1)
        else:
            # Tidak ada yang selesai
            if current_date == today_date:
                # Wajar jika hari ini belum ada yang selesai, lanjut cek streak kemarin
                current_date -= timedelta(days=1)
            else:
                # Jika hari kemarin tidak ada yang selesai, streak benar-benar putus
                break
                
    return streak_count