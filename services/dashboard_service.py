from config.firebase import db
from google.cloud.firestore_v1.base_query import FieldFilter
from datetime import datetime, timezone, timedelta

def get_dashboard_report(user_id: str, date_str: str) -> dict:
    """
    date_str: format 'YYYY-MM-DD'
    """
    # 1. Total Smoke & 4. Hourly Graph
    smoke_count_total = 0
    # Initialize hours from 00:00 to 23:00
    smoke_hourly_dict = {f"{i:02d}:00": 0 for i in range(24)}
    
    smoke_docs = list(db.collection("smokeCount")
                      .where(filter=FieldFilter("userID", "==", user_id))
                      .where(filter=FieldFilter("timestamp", "==", date_str))
                      .stream())
    
    if smoke_docs:
        smoke_doc = smoke_docs[0]
        smoke_count_total = smoke_doc.to_dict().get("count", 0)
        
        # Subcollection logs
        logs = smoke_doc.reference.collection("logs").stream()
        for log in logs:
            log_data = log.to_dict()
            logdate = log_data.get("logdate")
            if logdate:
                # Convert to local time UTC+7 for Jakarta
                local_time = logdate + timedelta(hours=7)
                hour_local = local_time.hour
                key = f"{hour_local:02d}:00"
                smoke_hourly_dict[key] += 1
                
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
                
    return {
        "smoke_count_total": smoke_count_total,
        "daily_activities_done_total": daily_activities_done_total,
        "chat_total": chat_total,
        "smoke_hourly_graph": smoke_hourly_graph,
        "activities_done_list": activities_done_list
    }
