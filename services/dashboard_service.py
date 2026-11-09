import json
import os
import re
from datetime import datetime, timezone, timedelta
from typing import Optional
import google.generativeai as genai
from google.cloud.firestore_v1.base_query import FieldFilter
from config.firebase import db
from schemas.smoke_schema import normalize_smoke_date_str
from services.smoke_service import JAKARTA_TZ, find_smoke_doc_ref, aggregate_hourly_logs
from services.user_service import find_or_get_daily_activity_ref

SMOKES_COL = "smokeCount"
ACTIVITIES_COL = "dailyActivities"
CHATS_COL = "chats"
DAILY_SUMMARIES_COL = "dailyAiSummaries"
DASHBOARD_SUMMARY_MODEL = os.getenv("MEDICAL_ANALYSIS_MODEL", "gemini-2.5-flash")


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


def _build_fallback_yesterday_summary(
    yesterday_date: str,
    smoke_count_total: int,
    smoke_limit_low: int,
    smoke_limit_high: int,
    daily_activities_done_total: int,
    daily_activities_total: int,
    activities_done_list: list,
    activities_pending_list: list,
    chat_total: int,
    peak_smoke_hour: Optional[str] = None,
) -> tuple[str, str]:
    """
    Membangun rangkuman dan feedback deterministik berbahasa Indonesia
    jika layanan AI tidak tersedia atau digunakan sebagai fallback.
    """
    # 1. Rangkuman Kemarin
    if smoke_count_total == 0:
        smoke_summary = f"Kemarin ({yesterday_date}) Anda berhasil bebas rokok (0 batang)"
    elif smoke_count_total <= smoke_limit_low:
        smoke_summary = (
            f"Kemarin ({yesterday_date}) Anda merokok {smoke_count_total} batang, "
            f"masih terkendali di dalam batas rendah ({smoke_limit_low} batang)"
        )
    elif smoke_count_total <= smoke_limit_high:
        smoke_summary = (
            f"Kemarin ({yesterday_date}) Anda merokok {smoke_count_total} batang, "
            f"melewati batas rendah ({smoke_limit_low} batang) dan mendekati batas maksimum ({smoke_limit_high} batang)"
        )
    else:
        smoke_summary = (
            f"Kemarin ({yesterday_date}) konsumsi rokok Anda mencapai {smoke_count_total} batang, "
            f"melebihi batas tinggi harian ({smoke_limit_high} batang)"
        )

    if peak_smoke_hour and smoke_count_total > 0:
        smoke_summary += f" dengan waktu merokok terbanyak pada pukul {peak_smoke_hour}"

    if daily_activities_total > 0:
        act_summary = (
            f"serta menyelesaikan {daily_activities_done_total} dari {daily_activities_total} aktivitas sehat harian"
        )
    elif daily_activities_done_total > 0:
        act_summary = f"serta menyelesaikan {daily_activities_done_total} aktivitas sehat harian"
    else:
        act_summary = "namun belum ada aktivitas harian yang tercatat selesai"

    chat_summary = (
        f" dan melakukan {chat_total} sesi konsultasi chat."
        if chat_total > 0
        else "."
    )

    summary_text = f"{smoke_summary}, {act_summary}{chat_summary}"

    # 2. Feedback & Rekomendasi Hari Ini
    feedback_parts = []
    if smoke_count_total == 0:
        feedback_parts.append(
            "Pertahankan pencapaian bebas rokok Anda hari ini karena paru-paru Anda sedang beradaptasi positif."
        )
    elif smoke_count_total <= smoke_limit_low:
        feedback_parts.append(
            "Kontrol merokok Anda kemarin sudah baik; hari ini cobalah kurangi 1 batang lagi atau tunda rokok pertama Anda."
        )
    else:
        trigger_hint = (
            f" terutama menjelang pukul {peak_smoke_hour}"
            if peak_smoke_hour
            else ""
        )
        feedback_parts.append(
            f"Hari ini mari tekan kembali jumlah rokok di bawah {smoke_limit_low} batang dengan mengganti keinginan merokok{trigger_hint} menggunakan air hangat atau latihan napas dalam."
        )

    if daily_activities_total > 0 and daily_activities_done_total < daily_activities_total:
        pending_sample = ", ".join(a for a in activities_pending_list[:2] if a)
        if pending_sample:
            feedback_parts.append(
                f"Jangan lupa jadwalkan kembali aktivitas yang sempat tertunda seperti {pending_sample} agar kebugaran paru tetap terjaga."
            )
        else:
            feedback_parts.append(
                "Usahakan menyelesaikan seluruh target aktivitas harian Anda hari ini untuk mendukung fungsi pernapasan."
            )
    elif daily_activities_done_total > 0:
        feedback_parts.append(
            "Konsistensi aktivitas fisik Anda sangat membantu kapasitas paru-paru, terus lanjutkan kebiasaan baik ini hari ini!"
        )
    else:
        feedback_parts.append(
            "Mulailah hari ini dengan menyelesaikan minimal 1-2 aktivitas sehat harian pada daftar Anda."
        )

    return summary_text, " ".join(feedback_parts)


def get_yesterday_ai_summary(
    user_id: str,
    date_str: Optional[str] = None,
    force_refresh: bool = False,
) -> dict:
    """
    Mengambil data pasien kemarin (H-1 dari date_str atau hari ini di WIB)
    dan menghasilkan analisa AI berupa rangkuman serta feedback harian.
    """
    try:
        if date_str:
            normalized_today = normalize_smoke_date_str(date_str)
            today_dt = datetime.strptime(normalized_today, "%Y-%m-%d")
        else:
            today_dt = datetime.now(JAKARTA_TZ)

        yesterday_dt = today_dt - timedelta(days=1)
        yesterday_date = yesterday_dt.strftime("%Y-%m-%d")

        # 1. Data rokok kemarin
        smoke_ref, smoke_data = find_smoke_doc_ref(user_id, yesterday_date)
        smoke_count_total = max(0, int((smoke_data or {}).get("count", 0) or 0))
        smoke_hourly_dict = aggregate_hourly_logs(smoke_ref)

        peak_smoke_hour = None
        peak_smoke_val = 0
        for hr, cnt in smoke_hourly_dict.items():
            if cnt > peak_smoke_val:
                peak_smoke_val = cnt
                peak_smoke_hour = hr

        # 2. Data aktivitas harian kemarin (selesai & belum selesai)
        activities_done_list = []
        activities_pending_list = []
        act_ref, act_data = find_or_get_daily_activity_ref(user_id, yesterday_date)
        if act_data is not None:
            sub_docs = act_ref.collection("activities").stream()
            for sub in sub_docs:
                d = sub.to_dict() or {}
                title = str(d.get("activity") or "").strip()
                if not title:
                    continue
                if d.get("done") is True:
                    activities_done_list.append(title)
                else:
                    activities_pending_list.append(title)

        daily_activities_done_total = len(activities_done_list)
        daily_activities_total = daily_activities_done_total + len(activities_pending_list)

        # 3. Data chat konsultasi kemarin
        chat_total = 0
        chat_topics = []
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
                if msg_date == yesterday_date:
                    chat_total += 1
                    content = str(d.get("content") or d.get("text") or "").strip()
                    if content and len(chat_topics) < 3:
                        chat_topics.append(content[:120])

        # 4. Data profil dan batas rokok pasien
        smoke_limit_low = 5
        smoke_limit_high = 10
        patient_name = "Pasien"
        medical_history = ""
        try:
            user_doc = db.collection("accounts").document(user_id).get()
            if user_doc.exists:
                u_data = user_doc.to_dict() or {}
                smoke_limit_low = int(u_data.get("smoke_limit_low") or 5)
                smoke_limit_high = int(u_data.get("smoke_limit_high") or 10)
                patient_name = str(u_data.get("name") or "Pasien").strip()
                medical_history = str(u_data.get("history") or "").strip()
        except Exception:
            pass

        data_signature = (
            f"{smoke_count_total}:{smoke_limit_low}:{smoke_limit_high}:"
            f"{daily_activities_done_total}:{daily_activities_total}:{chat_total}"
        )
        summary_doc_id = f"{user_id}_{yesterday_date}"

        # 5. Cek cache di Firestore jika tidak force_refresh dan tanda tangan data masih sama
        if not force_refresh:
            try:
                cached_doc = db.collection(DAILY_SUMMARIES_COL).document(summary_doc_id).get()
                if cached_doc.exists:
                    cached = cached_doc.to_dict() or {}
                    if (
                        cached.get("data_signature") == data_signature
                        and cached.get("summary")
                        and cached.get("feedback")
                    ):
                        return {
                            "status": "success",
                            "data": {
                                "yesterday_date": yesterday_date,
                                "smoke_count_total": smoke_count_total,
                                "smoke_limit_low": smoke_limit_low,
                                "smoke_limit_high": smoke_limit_high,
                                "daily_activities_done_total": daily_activities_done_total,
                                "daily_activities_total": daily_activities_total,
                                "activities_done_list": activities_done_list,
                                "activities_pending_list": activities_pending_list,
                                "chat_total": chat_total,
                                "peak_smoke_hour": peak_smoke_hour,
                                "summary": cached.get("summary"),
                                "feedback": cached.get("feedback"),
                                "source": "cache",
                            },
                        }
            except Exception:
                pass

        # 6. Bangun fallback terlebih dahulu
        summary_text, feedback_text = _build_fallback_yesterday_summary(
            yesterday_date=yesterday_date,
            smoke_count_total=smoke_count_total,
            smoke_limit_low=smoke_limit_low,
            smoke_limit_high=smoke_limit_high,
            daily_activities_done_total=daily_activities_done_total,
            daily_activities_total=daily_activities_total,
            activities_done_list=activities_done_list,
            activities_pending_list=activities_pending_list,
            chat_total=chat_total,
            peak_smoke_hour=peak_smoke_hour,
        )
        source_type = "fallback"

        # 7. Panggil Gemini AI jika GOOGLE_API_KEY tersedia
        api_key = os.getenv("GOOGLE_API_KEY", "").strip()
        if api_key:
            try:
                genai.configure(api_key=api_key)
                model = genai.GenerativeModel(DASHBOARD_SUMMARY_MODEL)
                done_str = ", ".join(activities_done_list) if activities_done_list else "Belum ada"
                pending_str = ", ".join(activities_pending_list) if activities_pending_list else "Tidak ada"
                topics_str = "; ".join(chat_topics) if chat_topics else "Tidak ada sesi chat kemarin"
                peak_str = f"{peak_smoke_hour} ({peak_smoke_val} batang)" if peak_smoke_hour else "Tidak ada"

                prompt = (
                    "Anda adalah asisten kesehatan paru AI (Lungs Assistant) yang suportif, empatik, dan ringkas.\n"
                    f"Buat analisa harian berdasarkan data aktivitas pasien kemarin ({yesterday_date}):\n"
                    f"- Nama Pasien: {patient_name}\n"
                    f"- Riwayat Medis: {medical_history or 'Tidak disebutkan'}\n"
                    f"- Jumlah Rokok Kemarin: {smoke_count_total} batang "
                    f"(Batas Rendah: {smoke_limit_low} batang, Batas Tinggi: {smoke_limit_high} batang)\n"
                    f"- Jam Merokok Terbanyak Kemarin: {peak_str}\n"
                    f"- Aktivitas Harian Selesai: {daily_activities_done_total} dari {daily_activities_total} aktivitas ({done_str})\n"
                    f"- Aktivitas Harian Belum Selesai: {pending_str}\n"
                    f"- Jumlah Chat Konsultasi Kemarin: {chat_total} pesan (Topik: {topics_str})\n\n"
                    "Kembalikan HANYA JSON valid dengan format persis berikut (tanpa markdown blok kode):\n"
                    "{\n"
                    '  "summary": "Rangkuman singkat 2 kalimat mengenai data rokok, aktivitas harian, dan konsultasi pasien kemarin dalam Bahasa Indonesia.",\n'
                    '  "feedback": "Feedback suportif dan saran praktis 2 kalimat yang dapat dilakukan pasien hari ini untuk menjaga kesehatan paru-paru dan mengendalikan rokok dalam Bahasa Indonesia."\n'
                    "}"
                )
                response = model.generate_content(prompt)
                raw_text = (getattr(response, "text", None) or "").strip()
                if raw_text:
                    cleaned_json = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text, flags=re.IGNORECASE).strip()
                    parsed = json.loads(cleaned_json)
                    ai_summary = str(parsed.get("summary") or "").strip()
                    ai_feedback = str(parsed.get("feedback") or "").strip()
                    if ai_summary and ai_feedback:
                        summary_text = ai_summary
                        feedback_text = ai_feedback
                        source_type = "gemini"
            except Exception:
                # Gunakan fallback deterministik jika Gemini gagal / kuota habis
                pass

        # 8. Simpan ke cache Firestore
        try:
            db.collection(DAILY_SUMMARIES_COL).document(summary_doc_id).set(
                {
                    "userID": user_id,
                    "yesterday_date": yesterday_date,
                    "data_signature": data_signature,
                    "summary": summary_text,
                    "feedback": feedback_text,
                    "source": source_type,
                    "updated_at": datetime.now(JAKARTA_TZ).isoformat(),
                },
                merge=True,
            )
        except Exception:
            pass

        return {
            "status": "success",
            "data": {
                "yesterday_date": yesterday_date,
                "smoke_count_total": smoke_count_total,
                "smoke_limit_low": smoke_limit_low,
                "smoke_limit_high": smoke_limit_high,
                "daily_activities_done_total": daily_activities_done_total,
                "daily_activities_total": daily_activities_total,
                "activities_done_list": activities_done_list,
                "activities_pending_list": activities_pending_list,
                "chat_total": chat_total,
                "peak_smoke_hour": peak_smoke_hour,
                "summary": summary_text,
                "feedback": feedback_text,
                "source": source_type,
            },
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
        }
