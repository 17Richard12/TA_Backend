from datetime import datetime, timezone, timedelta
from typing import Optional
from google.cloud.firestore_v1.base_query import FieldFilter
from config.firebase import db
from schemas.smoke_schema import normalize_smoke_date_str
from services.smoke_service import JAKARTA_TZ, find_smoke_doc_ref, aggregate_hourly_logs
from services.user_service import find_or_get_daily_activity_ref

SMOKES_COL = "smokeCount"
ACTIVITIES_COL = "dailyActivities"
CHATS_COL = "chats"


def _parse_timestamp_to_jakarta_date_str(ts_val) -> Optional[str]:
    """
    Converts an ISO timestamp string, YYYY-MM-DD string, DD/MM/YYYY string,
    or datetime object into a YYYY-MM-DD date string in WIB (UTC+7).
    """
    if not ts_val:
        return None
    if isinstance(ts_val, datetime):
        dt = ts_val if ts_val.tzinfo else ts_val.replace(tzinfo=timezone.utc)
        return dt.astimezone(JAKARTA_TZ).strftime("%Y-%m-%d")
    if isinstance(ts_val, str):
        cleaned = ts_val.strip()
        # Plain date strings
        for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
            try:
                return datetime.strptime(cleaned, fmt).strftime("%Y-%m-%d")
            except ValueError:
                pass
        # Full ISO datetime string
        try:
            dt = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(JAKARTA_TZ).strftime("%Y-%m-%d")
        except ValueError:
            if len(cleaned) >= 10:
                return cleaned[:10]
    return None


def get_dashboard_report(user_id: str, date_str: str) -> dict:
    """
    date_str: format 'YYYY-MM-DD' or 'DD/MM/YYYY'
    """
    normalized_date = normalize_smoke_date_str(date_str)

    # 1. Total Smoke & 4. Hourly Graph
    smoke_ref, smoke_data = find_smoke_doc_ref(user_id, normalized_date)
    smoke_count_total = max(0, int((smoke_data or {}).get("count", 0) or 0))
    smoke_hourly_dict = aggregate_hourly_logs(smoke_ref)

    smoke_hourly_graph = [{"hour": k, "count": v} for k, v in smoke_hourly_dict.items()]

    # 2. Daily Activities Total & 5. List Activities
    daily_activities_done_total = 0
    activities_done_list = []

    act_ref, act_data = find_or_get_daily_activity_ref(user_id, normalized_date)
    if act_data is not None:
        sub_docs = act_ref.collection("activities").stream()
        for sub in sub_docs:
            d = sub.to_dict() or {}
            if d.get("done") is True:
                daily_activities_done_total += 1
                activities_done_list.append(d.get("activity", ""))

    # 3. Chat total (user messages on normalized_date in WIB/UTC+7)
    chat_total = 0
    chat_docs = list(
        db.collection(CHATS_COL)
        .where(filter=FieldFilter("user", "==", user_id))
        .stream()
    )

    for chat in chat_docs:
        msgs = (
            chat.reference.collection("messages")
            .where(filter=FieldFilter("role", "==", "user"))
            .stream()
        )
        for m in msgs:
            d = m.to_dict() or {}
            msg_date = _parse_timestamp_to_jakarta_date_str(d.get("timestamp"))
            if msg_date == normalized_date:
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
        "activities_done_list": activities_done_list,
    }


def get_monthly_report(user_id: str, year: int, month: int) -> dict:
    try:
        if month < 1 or month > 12:
            raise ValueError("Bulan harus bernilai antara 1 hingga 12.")

        month_prefix = f"{year:04d}-{month:02d}-"

        available_dates = set()
        total_smoke = 0
        total_activities = 0
        total_chats = 0

        # 1. Hitung Aktivitas Harian yang Selesai (done == True)
        activities = (
            db.collection(ACTIVITIES_COL)
            .where(filter=FieldFilter("userID", "==", user_id))
            .stream()
        )
        seen_activity_dates = set()
        for doc in activities:
            data = doc.to_dict() or {}
            doc_date = _parse_timestamp_to_jakarta_date_str(data.get("timestamp"))
            if not doc_date or not doc_date.startswith(month_prefix):
                continue
            if doc_date in seen_activity_dates:
                continue
            seen_activity_dates.add(doc_date)

            sub_docs = doc.reference.collection("activities").stream()
            done_in_day = 0
            any_sub = False
            for sub in sub_docs:
                any_sub = True
                sub_data = sub.to_dict() or {}
                if sub_data.get("done") is True:
                    done_in_day += 1

            total_activities += done_in_day
            if any_sub or done_in_day > 0:
                available_dates.add(doc_date)

        # 2. Hitung Rokok
        smokes = (
            db.collection(SMOKES_COL)
            .where(filter=FieldFilter("userID", "==", user_id))
            .stream()
        )
        seen_smoke_dates = {}
        for doc in smokes:
            data = doc.to_dict() or {}
            doc_date = _parse_timestamp_to_jakarta_date_str(data.get("timestamp"))
            if not doc_date or not doc_date.startswith(month_prefix):
                continue
            count_val = max(0, int(data.get("count", 0) or 0))
            # Jika ada dokumen deterministik (id == f"{user_id}_{doc_date}"), prioritaskan
            if doc.id == f"{user_id}_{doc_date}" or doc_date not in seen_smoke_dates:
                seen_smoke_dates[doc_date] = count_val
            available_dates.add(doc_date)

        total_smoke = sum(seen_smoke_dates.values())

        # 3. Hitung Chat (koleksi chats menggunakan field "user" dan subkoleksi "messages")
        chats = (
            db.collection(CHATS_COL)
            .where(filter=FieldFilter("user", "==", user_id))
            .stream()
        )
        for chat_doc in chats:
            msgs = (
                chat_doc.reference.collection("messages")
                .where(filter=FieldFilter("role", "==", "user"))
                .stream()
            )
            for msg in msgs:
                msg_data = msg.to_dict() or {}
                msg_date = _parse_timestamp_to_jakarta_date_str(msg_data.get("timestamp"))
                if msg_date and msg_date.startswith(month_prefix):
                    total_chats += 1
                    available_dates.add(msg_date)

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
                "available_dates": sorted(list(available_dates)),
            },
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
        }
