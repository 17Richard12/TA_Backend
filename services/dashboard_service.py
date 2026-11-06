from config.firebase import db
from google.cloud.firestore_v1.base_query import FieldFilter
from datetime import datetime, timezone, timedelta
from services.smoke_service import find_smoke_doc_ref, aggregate_hourly_logs

SMOKES_COL = "smokeCount" 
ACTIVITIES_COL = "dailyActivities"
CHATS_COL = "chats"

def get_dashboard_report(user_id: str, date_str: str) -> dict:
    """
    date_str: format 'YYYY-MM-DD'
    """
    # 1. Total Smoke & 4. Hourly Graph
    # Gunakan helper yang sama dengan endpoint smoke-count agar angka di Home & Report selalu sinkron
    smoke_ref, smoke_data = find_smoke_doc_ref(user_id, date_str)
    smoke_count_total = (smoke_data or {}).get("count", 0)
    smoke_hourly_dict = aggregate_hourly_logs(smoke_ref)
                
    smoke_hourly_graph = [{"hour": k, "count": v} for k, v in smoke_hourly_dict.items()]
    
    # 2. Daily Activities Total & 5. List Activities
    daily_activities_done_total = 0
    activities_done_list = []
    
    act_docs = list(db.collection("dailyActivities")
                    .where(filter=FieldFilter("userID", "==", user_id))
                    .where(filter=FieldFilter("timestamp", "==", date_str))
                    .stream())
    
    if act_docs:
        act_doc = act_docs[0]
        sub_docs = act_doc.reference.collection("activities").stream()
        for sub in sub_docs:
            d = sub.to_dict()
            if d.get("done") is True:
                daily_activities_done_total += 1
                activities_done_list.append(d.get("activity", ""))
                
    # 3. Chat total (user messages today)
    chat_total = 0
    chat_docs = list(db.collection("chats")
                     .where(filter=FieldFilter("user", "==", user_id))
                     .stream())
    
    for chat in chat_docs:
        msgs = chat.reference.collection("messages").where(filter=FieldFilter("role", "==", "user")).stream()
        for m in msgs:
            d = m.to_dict()
            ts = d.get("timestamp", "")
            # ISO format: 2026-10-01T...
            if ts.startswith(date_str):
                chat_total += 1
                
    # Ambil batas rendah & batas tinggi rokok dari profil user
    smoke_limit_low = 5
    smoke_limit_high = 10
    try:
        user_doc = db.collection("accounts").document(user_id).get()
        if user_doc.exists:
            u_data = user_doc.to_dict() or {}
            smoke_limit_low = int(u_data.get("smoke_limit_low") or 5)
            smoke_limit_high = int(u_data.get("smoke_limit_high") or 10)
    except Exception:
        pass

    return {
        "smoke_count_total": smoke_count_total,
        "smoke_limit_low": smoke_limit_low,
        "smoke_limit_high": smoke_limit_high,
        "daily_activities_done_total": daily_activities_done_total,
        "chat_total": chat_total,
        "smoke_hourly_graph": smoke_hourly_graph,
        "activities_done_list": activities_done_list
    }

def get_monthly_report(user_id: str, year: int, month: int) -> dict:
    try:
        # Tentukan rentang waktu: awal bulan ini sampai awal bulan depan
        start_date = datetime(year, month, 1, tzinfo=timezone.utc)
        
        if month == 12:
            end_date = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            end_date = datetime(year, month + 1, 1, tzinfo=timezone.utc)

        available_dates = set()
        total_smoke = 0
        total_activities = 0
        total_chats = 0

        # 1. Hitung Aktivitas Harian
        activities = db.collection(ACTIVITIES_COL).where(filter=FieldFilter("userID", "==", user_id)).stream()
        for doc in activities:
            data = doc.to_dict()
            ts = data.get("timestamp")
            
            if isinstance(ts, str):
                try:
                    ts = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                except ValueError:
                    pass 
            
            if isinstance(ts, datetime):
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                    
                if start_date <= ts < end_date:
                    total_activities += 1
                    available_dates.add(ts.strftime("%Y-%m-%d"))

        # 2. Hitung Rokok
        smokes = db.collection(SMOKES_COL).where(filter=FieldFilter("userID", "==", user_id)).stream()
        for doc in smokes:
            data = doc.to_dict()
            ts = data.get("timestamp")
            
            if isinstance(ts, str):
                try:
                    ts = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                except ValueError:
                    pass
            
            if isinstance(ts, datetime):
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                    
                if start_date <= ts < end_date:
                    total_smoke += data.get("count", 1) 
                    available_dates.add(ts.strftime("%Y-%m-%d"))

        # 3. Hitung Chat
        chats = db.collection(CHATS_COL).where(filter=FieldFilter("userID", "==", user_id)).stream()
        for doc in chats:
            data = doc.to_dict()
            ts = data.get("timestamp")
            
            if isinstance(ts, str):
                try:
                    ts = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                except ValueError:
                    pass
            
            if isinstance(ts, datetime):
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                    
                if start_date <= ts < end_date:
                    total_chats += 1
                    available_dates.add(ts.strftime("%Y-%m-%d"))

        # Ambil batas rendah & batas tinggi rokok dari profil user
        smoke_limit_low = 5
        smoke_limit_high = 10
        try:
            user_doc = db.collection("accounts").document(user_id).get()
            if user_doc.exists:
                u_data = user_doc.to_dict() or {}
                smoke_limit_low = int(u_data.get("smoke_limit_low") or 5)
                smoke_limit_high = int(u_data.get("smoke_limit_high") or 10)
        except Exception:
            pass

        return {
            "status": "success",
            "data": {
                "year": year,
                "month": month,
                "total_smoke_count": total_smoke,
                "smoke_limit_low": smoke_limit_low,
                "smoke_limit_high": smoke_limit_high,
                "total_activities": total_activities,
                "total_chats": total_chats,
                "available_dates": sorted(list(available_dates)) 
            }
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e)
        }