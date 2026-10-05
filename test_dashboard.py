import asyncio
from config.firebase import db
from google.cloud.firestore_v1.base_query import FieldFilter
from datetime import datetime, timezone

def test(user_id, date_str):
    # date_str: YYYY-MM-DD
    print("Testing dashboard queries...")
    
    # 1. Total Smoke
    smoke_docs = list(db.collection("smokeCount").where(filter=FieldFilter("userID", "==", user_id)).where(filter=FieldFilter("timestamp", "==", date_str)).stream())
    total_smoke = 0
    if smoke_docs:
        doc = smoke_docs[0]
        total_smoke = doc.to_dict().get("count", 0)
        logs = list(doc.reference.collection("logs").stream())
        print(f"Total smoke logs: {len(logs)}")
        for log in logs:
            ld = log.to_dict().get("logdate")
            print("Logdate:", ld)
            
    print(f"Total smoke: {total_smoke}")
    
    # 2. Activities
    act_docs = list(db.collection("dailyActivities").where(filter=FieldFilter("userID", "==", user_id)).where(filter=FieldFilter("timestamp", "==", date_str)).stream())
    done_activities = []
    if act_docs:
        doc = act_docs[0]
        sub_docs = doc.reference.collection("activities").stream()
        for sub in sub_docs:
            d = sub.to_dict()
            if d.get("done") is True:
                done_activities.append(d.get("activity"))
    print(f"Done activities ({len(done_activities)}): {done_activities}")
    
    # 3. Chats
    chat_docs = list(db.collection("chats").where(filter=FieldFilter("user", "==", user_id)).stream())
    chat_count = 0
    for chat in chat_docs:
        # Check messages
        # string comparison for timestamp
        # ISO format: 2026-10-01T...
        msgs = chat.reference.collection("messages").where(filter=FieldFilter("role", "==", "user")).stream()
        for m in msgs:
            d = m.to_dict()
            ts = d.get("timestamp", "")
            if ts.startswith(date_str):
                chat_count += 1
    print(f"Total chat messages today: {chat_count}")

test("066098a4048040119b51", "2026-10-01")
