import os
import json
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from fastapi import HTTPException
import google.generativeai as genai
from google.cloud.firestore_v1.base_query import FieldFilter

from config.firebase import db
from config.security import assert_patient_access
from schemas.activity_schema import GenerateActivitySchema
from schemas.smoke_schema import normalize_smoke_date_str

DAILY_ACT_COL = "dailyActivities"
ACT_SUB_COL = "activities"


def _new_id() -> str:
    return str(uuid.uuid4()).replace("-", "")[:20]


def _parse_any_date(date_str: str) -> datetime:
    cleaned = (date_str or "").strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    raise ValueError("Format tanggal tidak valid. Gunakan dd/mm/yyyy atau YYYY-MM-DD")


def _find_daily_activity_doc(user_uid: str, timestamp: str):
    """
    Mencari apakah record aktivitas untuk user pada tanggal ini sudah ada.
    Prioritas: deterministic ID `{user_uid}_{timestamp}` lalu fallback query legacy.
    """
    normalized_ts = normalize_smoke_date_str(timestamp)
    det_id = f"{user_uid}_{normalized_ts}"
    det_snap = db.collection(DAILY_ACT_COL).document(det_id).get()
    if det_snap.exists:
        return det_snap.id, det_snap.to_dict()

    docs = (
        db.collection(DAILY_ACT_COL)
        .where(filter=FieldFilter("userID", "==", user_uid))
        .where(filter=FieldFilter("timestamp", "==", normalized_ts))
        .limit(1)
        .stream()
    )
    for doc in docs:
        return doc.id, doc.to_dict()
    return None, None


def get_or_create_daily_activities(payload: GenerateActivitySchema) -> dict:
    user_uid = payload.user_uid
    timestamp = normalize_smoke_date_str(payload.timestamp)

    # 1. CEK DATA: Cari data berdasarkan userID dan timestamp hari ini
    doc_id, existing_data = _find_daily_activity_doc(user_uid, timestamp)

    if existing_data:
        sub_docs = (
            db.collection(DAILY_ACT_COL).document(doc_id)
            .collection(ACT_SUB_COL)
            .stream()
        )

        activities_list = []
        for doc in sub_docs:
            data = doc.to_dict() or {}
            activities_list.append({
                "id": doc.id,
                "activity": data.get("activity"),
                "done": bool(data.get("done", False)),
            })

        return {
            "id": doc_id,
            "user_uid": user_uid,
            "timestamp": timestamp,
            "action": "fetched_existing",
            "activities": activities_list,
        }

    # 2. JIKA BELUM ADA: Baru lakukan fetch ke AI dan Insert ke Firebase
    try:
        smoke_docs = (
            db.collection("smokeCount")
            .where(filter=FieldFilter("userID", "==", user_uid))
            .stream()
        )

        smoke_records = []
        for doc in smoke_docs:
            data = doc.to_dict() or {}
            doc_ts = data.get("timestamp", "")
            try:
                dt = _parse_any_date(doc_ts)
                smoke_records.append({"date": dt, "count": int(data.get("count", 0) or 0)})
            except ValueError:
                continue

        smoke_records.sort(key=lambda x: x["date"], reverse=True)
        today_dt = datetime.strptime(timestamp, "%Y-%m-%d")

        smoke_count_yesterday = 0
        for record in smoke_records:
            if record["date"] < today_dt:
                smoke_count_yesterday = max(0, record["count"])
                break

    except Exception as e:
        print(f"Error fetching smokeCount: {e}")
        smoke_count_yesterday = 0

    target_activities_count = min(max(1, smoke_count_yesterday + 1), 15)

    genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
    model = genai.GenerativeModel("gemini-2.5-flash")

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
        raw_text = response.text.strip().strip("```json").strip("```").strip()
        activities_list = json.loads(raw_text)
        if not isinstance(activities_list, list):
            raise ValueError("Invalid JSON list from Gemini")
    except Exception as e:
        print(f"Error calling Gemini or parsing JSON: {e}")
        default_acts = [
            "Latihan Napas Dalam (Deep Breathing)",
            "Minum segelas air putih perlahan",
            "Jalan kaki singkat selama 10 menit",
            "Mengunyah permen karet bebas gula",
            "Mencuci muka dengan air dingin",
            "Mendengarkan musik relaksasi",
            "Membaca buku 15 menit",
        ]
        activities_list = default_acts[:target_activities_count]
        while len(activities_list) < target_activities_count:
            activities_list.append("Minum air putih tambahan")

    # Gunakan deterministic ID agar tidak terjadi duplikasi dokumen harian
    daily_activity_id = f"{user_uid}_{timestamp}"
    db.collection(DAILY_ACT_COL).document(daily_activity_id).set({
        "userID": user_uid,
        "timestamp": timestamp,
    })

    saved_activities = []
    for act in activities_list[:target_activities_count]:
        act_id = _new_id()
        act_data = {
            "activity": str(act),
            "done": False,
        }

        db.collection(DAILY_ACT_COL).document(daily_activity_id) \
          .collection(ACT_SUB_COL).document(act_id).set(act_data)

        saved_activities.append({
            "id": act_id,
            **act_data,
        })

    return {
        "id": daily_activity_id,
        "user_uid": user_uid,
        "timestamp": timestamp,
        "action": "generated_and_inserted",
        "activities": saved_activities,
    }


def update_activity_status(
    daily_activity_id: str,
    activity_id: str,
    is_done: bool,
    current_user: Optional[Dict[str, Any]] = None,
) -> dict:
    """Mengupdate status done pada sub-collection activities dengan verifikasi kepemilikan."""
    parent_ref = db.collection(DAILY_ACT_COL).document(daily_activity_id)
    parent_doc = parent_ref.get()
    if not parent_doc.exists:
        return None

    parent_data = parent_doc.to_dict() or {}
    owner_uid = parent_data.get("userID")
    if current_user is not None and owner_uid:
        assert_patient_access(current_user, owner_uid)

    activity_ref = parent_ref.collection(ACT_SUB_COL).document(activity_id)
    activity_doc = activity_ref.get()
    if not activity_doc.exists:
        return None

    activity_ref.update({
        "done": bool(is_done),
    })

    return {
        "daily_activity_id": daily_activity_id,
        "activity_id": activity_id,
        "done": bool(is_done),
    }


def get_weekly_activity_report(user_id: str, end_timestamp: str) -> list:
    """
    Mengambil data jumlah aktivitas yang selesai (done == True) selama 7 hari ke belakang.
    Format input end_timestamp: dd/mm/yyyy atau YYYY-MM-DD
    """
    end_date = _parse_any_date(end_timestamp)

    date_list_db = [(end_date - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
    date_list_db.reverse()

    date_list_display = [(end_date - timedelta(days=i)).strftime("%d/%m/%Y") for i in range(7)]
    date_list_display.reverse()

    docs = (
        db.collection(DAILY_ACT_COL)
        .where(filter=FieldFilter("userID", "==", user_id))
        .where(filter=FieldFilter("timestamp", "in", date_list_db))
        .stream()
    )

    db_data = {}
    for doc in docs:
        data = doc.to_dict() or {}
        timestamp_db = data.get("timestamp")

        sub_docs = (
            doc.reference.collection(ACT_SUB_COL)
            .where(filter=FieldFilter("done", "==", True))
            .stream()
        )

        done_count = sum(1 for _ in sub_docs)
        db_data[timestamp_db] = max(db_data.get(timestamp_db, 0), done_count)

    report_data = []
    for i in range(7):
        db_format = date_list_db[i]
        display_format = date_list_display[i]

        report_data.append({
            "timestamp": display_format,
            "count": db_data.get(db_format, 0),
        })

    return report_data


def get_activity_streak(user_id: str, today_timestamp: str) -> int:
    """
    Menghitung jumlah hari berturut-turut user menyelesaikan minimal 1 aktivitas.
    Input today_timestamp: dd/mm/yyyy atau YYYY-MM-DD
    """
    today_date = _parse_any_date(today_timestamp)

    streak_count = 0
    current_date = today_date

    while True:
        date_str_db = current_date.strftime("%Y-%m-%d")

        doc_id, _ = _find_daily_activity_doc(user_id, date_str_db)
        if not doc_id:
            if current_date == today_date:
                current_date -= timedelta(days=1)
                continue
            else:
                break

        sub_docs = (
            db.collection(DAILY_ACT_COL)
            .document(doc_id)
            .collection(ACT_SUB_COL)
            .where(filter=FieldFilter("done", "==", True))
            .limit(1)
            .stream()
        )

        has_done = any(True for _ in sub_docs)

        if has_done:
            streak_count += 1
            current_date -= timedelta(days=1)
        else:
            if current_date == today_date:
                current_date -= timedelta(days=1)
            else:
                break

    return streak_count
