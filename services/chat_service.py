import asyncio
import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, Optional

from fastapi import HTTPException
import google.generativeai as genai
from google.genai import types
from google.adk.events import Event
from google.cloud.firestore_v1.base_query import FieldFilter

from config.firebase import db
from config.security import assert_patient_access
from schemas.chat_schema import PostChatSchema

CHATS_COL = "chats"
MESSAGES_COL = "messages"


# ---------- Emergency Detection ----------
# Categories covered:
# 1. Chest pain (nyeri dada, serangan jantung, angina)
# 2. Severe breathing difficulties (sesak napas berat/parah, henti napas, sianosis/bibir biru)
# 3. Possible stroke symptoms (wajah mencong/asimetris, bicara pelo/cadel mendadak, lumpuh sebelah)
# 4. Loss of consciousness (pingsan, tidak sadarkan diri, penurunan/hilang kesadaran)
# 5. Coughing up significant blood (batuk darah, batuk berdarah, hemoptisis, muntah darah)
# Note: Generic words like "tolong" or "darurat" are intentionally excluded to prevent false positives.

EMERGENCY_PATTERNS = [
    # 1. Chest pain
    r"\bnyeri\s+dada\b",
    r"\bdada\s+(?:terasa\s+|sangat\s+)?(?:nyeri|sakit\s+hebat|tertekan\s+kuat|terhimpit)\b",
    r"\bserangan\s+jantung\b",
    r"\bnyeri\s+menjalar\s+ke\s+(?:lengan|rahang|punggung|bahu)\b",
    # 2. Severe breathing difficulties & cyanosis
    r"\bhenti\s+napas\b",
    r"\btidak\s+bisa\s+bernapas\b",
    r"\bsulit\s+sekali\s+bernapas\b",
    r"\bsesak\s+napas\s+(?:berat|parah|hebat|mendadak|akut|ekstrem)\b",
    r"\bnapas\s+(?:sangat|amat)\s+sesak\b",
    r"\bsesak\s+(?:sangat\s+)?berat\b",
    r"\bbibir\s+(?:tampak\s+|mulai\s+|menjadi\s+)?(?:kebiruan|biru|ungu)\b",
    r"\bkuku\s+(?:tampak\s+|mulai\s+)?(?:kebiruan|biru)\b",
    r"\bsianosis\b",
    r"\btersedak\s+parah\b",
    r"\bnapas\s+tersengal\b",
    # 3. Possible stroke symptoms
    r"\bgejala\s+stroke\b",
    r"\bserangan\s+stroke\b",
    r"\bwajah\s+(?:mencong|asimetris|perot|turun\s+sebelah)\b",
    r"\bbicara\s+(?:pelo|cadel\s+mendadak|tidak\s+jelas\s+mendadak)\b",
    r"\blumpuh\s+(?:sebelah|separuh\s+badan|mendadak)\b",
    r"\btangan\s+(?:atau\s+kaki\s+)?lemah\s+sebelah\b",
    # 4. Loss of consciousness
    r"\bpingsan\b",
    r"\btidak\s+sadarkan\s+diri\b",
    r"\bhilang\s+kesadaran\b",
    r"\bpenurunan\s+kesadaran\b",
    r"\bsekarat\b",
    # 5. Coughing up significant blood / hemoptysis
    r"\bbatuk\s+(?:ber)?darah\b",
    r"\bbatuk\s+(?:disertai|mengeluarkan|keluar)\s+darah\b",
    r"\bdahak\s+berdarah\s+(?:banyak|segar)\b",
    r"\bhemoptisis\b",
    r"\bmuntah\s+darah\b",
]

# Negation prefixes that commonly precede a symptom in patient denials
# e.g., "Tidak ada nyeri dada", "tidak batuk darah", "tanpa sesak napas berat"
_NEGATION_PREFIX_RE = re.compile(
    r"(?:tidak\s+ada|tidak\s+mengalami|tidak\s+merasakan|tidak\s+pernah|tidak|bukan|tanpa|belum\s+ada)\s+$",
    re.IGNORECASE,
)

# Clause boundary delimiters so negation only applies within the same clause
# Note: coordinating conjunctions like "dan"/"atau" keep the negation active in lists
# like "Tidak ada demam dan tidak ada darah pada dahak" or "tanpa nyeri dada atau sesak napas berat".
_CLAUSE_SPLIT_RE = re.compile(r"[.;!?,\n]+|\b(?:tetapi|namun|tapi|melainkan|padahal|bahkan)\b", re.IGNORECASE)

EMERGENCY_REPLY_MESSAGE = (
    "⚠️ PERINGATAN DARURAT OTOMATIS: Sistem mendeteksi kata kunci gejala kritis pada pesan Anda "
    "(seperti nyeri dada, sesak napas berat, batuk darah, gejala stroke, atau penurunan kesadaran). "
    "Harap segera hentikan aktivitas dan kunjungi Instalasi Gawat Darurat (IGD/UGD) rumah sakit terdekat "
    "atau hubungi layanan darurat medis (119). "
    "Catatan: Peringatan cepat ini berbasis deteksi kata kunci otomatis dan bukan pengganti triase medis klinis."
)


def _is_match_negated(clause: str, match_start: int) -> bool:
    """
    Checks whether a matched emergency phrase inside `clause` is negated by a preceding
    negation word in the same clause (e.g., 'tidak ada nyeri dada', 'tanpa batuk darah').
    """
    prefix = clause[:match_start]
    if _NEGATION_PREFIX_RE.search(prefix):
        return True
    # Also check if a negation word appears within 45 characters before the match in the same clause
    window = prefix[-45:].lower()
    if re.search(r"\b(?:tidak\s+ada|tidak\s+mengalami|tidak\s+merasakan|tanpa|bukan)\b", window):
        return True
    if re.search(r"\btidak\s+(?:pernah\s+)?$", window):
        return True
    return False


def check_emergency(user_message: str) -> bool:
    """
    Cek apakah pesan pengguna mengandung indikasi darurat medis
    (nyeri dada, sesak napas berat, gejala stroke, kehilangan kesadaran, batuk darah)
    dengan memperhatikan negasi agar kalimat penyangkalan (misal 'tidak ada nyeri dada')
    tidak memicu false positive.
    """
    if not user_message or not isinstance(user_message, str):
        return False

    msg = user_message.lower().strip()
    clauses = _CLAUSE_SPLIT_RE.split(msg)

    for clause in clauses:
        clause = clause.strip()
        if not clause:
            continue
        for pattern in EMERGENCY_PATTERNS:
            for m in re.finditer(pattern, clause, flags=re.IGNORECASE):
                if not _is_match_negated(clause, m.start()):
                    return True

    return False


def _new_id() -> str:
    return str(uuid.uuid4()).replace("-", "")[:20]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _find_session_doc(session_id: str):
    """Cari dokumen session berdasarkan sessionID."""
    docs = (
        db.collection(CHATS_COL)
        .where(filter=FieldFilter("sessionID", "==", session_id))
        .limit(1)
        .stream()
    )
    for doc in docs:
        return doc.id, doc.to_dict()
    return None, None


def _find_docs_by_user(user_uid: str):
    """Ambil semua session milik user."""
    docs = (
        db.collection(CHATS_COL)
        .where(filter=FieldFilter("user", "==", user_uid))
        .stream()
    )
    return [(doc.id, doc.to_dict()) for doc in docs]


def _save_message(session_doc_id: str, role: str, response: str):
    """Simpan satu pesan ke subcollection messages."""
    msg_id = _new_id()
    db.collection(CHATS_COL).document(session_doc_id) \
        .collection(MESSAGES_COL).document(msg_id).set({
            "role": role,        # "user" atau "ai"
            "response": response,
            "timestamp": _now(),
        })


def _get_or_create_session_doc(session_id: str, user_uid: str, is_new_session: bool, first_message: str = "") -> str:
    """Return doc_id session. Buat baru jika belum ada."""
    if is_new_session:
        doc_id = _new_id()
        title = _generate_title(first_message) if first_message else "Konsultasi Kesehatan"
        db.collection(CHATS_COL).document(doc_id).set({
            "user": user_uid,
            "sessionID": session_id,
            "title": title,
        })
        return doc_id
    else:
        doc_id, existing = _find_session_doc(session_id)
        if doc_id:
            if existing and existing.get("user") and existing.get("user") != user_uid:
                raise HTTPException(
                    status_code=403,
                    detail="You are not authorized to access this chat session.",
                )
            return doc_id
        doc_id = _new_id()
        title = _generate_title(first_message) if first_message else "Konsultasi Kesehatan"
        db.collection(CHATS_COL).document(doc_id).set({
            "user": user_uid,
            "sessionID": session_id,
            "title": title,
        })
        return doc_id


def _fetch_patient_context(user_uid: str) -> dict:
    """
    Membaca profil user (accounts), riwayat merokok 5 hari terakhir (smokeCount),
    dan aktivitas yang telah diselesaikan (dailyActivities) dari Firestore.
    *Nama pasien disamarkan untuk menjaga privasi data medis.*
    """
    profile_lines = []
    try:
        user_doc = db.collection("accounts").document(user_uid).get()
        if user_doc.exists:
            u_data = user_doc.to_dict() or {}
            gender = u_data.get("gender") or "-"
            birth = u_data.get("birth") or "-"
            blood = u_data.get("bloodType") or "-"
            height = u_data.get("height") or "-"
            weight = u_data.get("weight") or "-"
            history = u_data.get("history") or "-"

            profile_lines.append("- Nama: [DISAMARKAN UNTUK PRIVASI]")
            profile_lines.append(f"- Jenis Kelamin: {gender}")
            profile_lines.append(f"- Tanggal Lahir/Usia: {birth}")
            profile_lines.append(f"- Golongan Darah: {blood}")
            profile_lines.append(f"- Tinggi / Berat: {height} cm / {weight} kg")
            profile_lines.append(f"- Riwayat Penyakit/Medis: {history}")
        else:
            profile_lines.append("- Profil pengguna belum diisi.")
    except Exception as e:
        profile_lines.append(f"- Gagal membaca profil: {e}")

    patient_profile_str = "\n".join(profile_lines)

    smoke_lines = []
    try:
        smoke_docs = (
            db.collection("smokeCount")
            .where(filter=FieldFilter("userID", "==", user_uid))
            .stream()
        )
        records = []
        for doc in smoke_docs:
            d = doc.to_dict() or {}
            ts = d.get("timestamp", "")
            cnt = d.get("count", 0)
            if ts:
                records.append({"timestamp": ts, "count": cnt})

        records.sort(key=lambda x: str(x["timestamp"]), reverse=True)
        recent_smoke = records[:5]

        if recent_smoke:
            for rec in recent_smoke:
                smoke_lines.append(f"- {rec['timestamp']}: {rec['count']} batang")
        else:
            smoke_lines.append("Tidak ada riwayat konsumsi rokok tercatat dalam 5 hari terakhir.")
    except Exception as e:
        smoke_lines.append(f"Gagal mengambil riwayat merokok: {e}")

    smoke_history_str = "\n".join(smoke_lines)

    activity_lines = []
    try:
        act_docs = (
            db.collection("dailyActivities")
            .where(filter=FieldFilter("userID", "==", user_uid))
            .stream()
        )
        act_records = []
        for doc in act_docs:
            d = doc.to_dict() or {}
            ts = d.get("timestamp", "")
            sub_docs = (
                doc.reference.collection("activities")
                .where(filter=FieldFilter("done", "==", True))
                .stream()
            )
            for sub in sub_docs:
                sd = sub.to_dict() or {}
                act_text = sd.get("activity", "")
                if act_text:
                    act_records.append({"timestamp": ts, "activity": act_text})

        act_records.sort(key=lambda x: str(x["timestamp"]), reverse=True)
        recent_acts = act_records[:5]
        if recent_acts:
            for a in recent_acts:
                activity_lines.append(f"- {a['activity']} ({a['timestamp']})")
        else:
            activity_lines.append("Belum ada aktivitas sehat yang diselesaikan.")
    except Exception as e:
        activity_lines.append(f"Gagal mengambil aktivitas selesai: {e}")

    completed_activities_str = "\n".join(activity_lines)

    patient_context_str = (
        f"=== PROFIL PASIEN ===\n{patient_profile_str}\n\n"
        f"=== RIWAYAT MEROKOK (5 HARI TERAKHIR) ===\n{smoke_history_str}\n\n"
        f"=== AKTIVITAS SEHAT YANG SELESAI ===\n{completed_activities_str}"
    )

    return {
        "patient_context": patient_context_str,
        "patient_profile": patient_profile_str,
        "smoke_history": smoke_history_str,
        "completed_activities": completed_activities_str,
    }


async def _get_or_restore_session(runner, user_uid: str, requested_session_id: Optional[str]) -> tuple[str, bool]:
    """
    Mendapatkan session yang aktif di memori runner, atau memulihkannya jika belum ada.
    Return: (session_id, is_new_session)
    """
    patient_state = _fetch_patient_context(user_uid)

    if not requested_session_id:
        session = await runner.session_service.create_session(
            app_name="lungs_assistant_agent",
            user_id=user_uid,
            state=patient_state,
        )
        return session.id, True

    # Verifikasi kepemilikan session di Firestore jika ada
    doc_id, existing_doc = _find_session_doc(requested_session_id)
    if existing_doc and existing_doc.get("user") and existing_doc.get("user") != user_uid:
        raise HTTPException(
            status_code=403,
            detail="You are not authorized to access this chat session.",
        )

    session = await runner.session_service.get_session(
        app_name="lungs_assistant_agent",
        user_id=user_uid,
        session_id=requested_session_id,
    )

    if session is not None:
        app_name = "lungs_assistant_agent"
        if (
            hasattr(runner.session_service, "sessions")
            and app_name in runner.session_service.sessions
            and user_uid in runner.session_service.sessions[app_name]
            and requested_session_id in runner.session_service.sessions[app_name][user_uid]
        ):
            runner.session_service.sessions[app_name][user_uid][requested_session_id].state.update(patient_state)
        return requested_session_id, False

    session = await runner.session_service.create_session(
        app_name="lungs_assistant_agent",
        user_id=user_uid,
        session_id=requested_session_id,
        state=patient_state,
    )

    if doc_id:
        msgs_stream = (
            db.collection(CHATS_COL).document(doc_id)
            .collection(MESSAGES_COL)
            .order_by("timestamp")
            .stream()
        )
        all_msgs = [m.to_dict() for m in msgs_stream]

        N = 20
        recent_msgs = all_msgs[-N:] if len(all_msgs) > N else all_msgs

        for msg in recent_msgs:
            role = msg.get("role")
            text = msg.get("response", "")
            if not text:
                continue
            if role == "user":
                event = Event(
                    author="user",
                    content=types.Content(role="user", parts=[types.Part(text=text)]),
                )
                await runner.session_service.append_event(session, event)
            elif role in ["ai", "assistant", "model"]:
                event = Event(
                    author="healthcare_coordinator",
                    content=types.Content(role="model", parts=[types.Part(text=text)]),
                )
                await runner.session_service.append_event(session, event)

    return requested_session_id, False


# ---------- service functions ----------

async def post_chat(payload: PostChatSchema, runner) -> dict:
    session_id, is_new_session = await _get_or_restore_session(runner, payload.user_uid, payload.session_id)

    # --- DETEKSI PERINGATAN DARURAT CEPAT (Bypass Agen LLM) ---
    if check_emergency(payload.message):
        emergency_reply = EMERGENCY_REPLY_MESSAGE

        doc_id = _get_or_create_session_doc(session_id, payload.user_uid, is_new_session, payload.message)
        _save_message(doc_id, "user", payload.message)
        _save_message(doc_id, "ai", emergency_reply)

        return {
            "session_id": session_id,
            "user_message": payload.message,
            "ai_response": emergency_reply,
            "is_emergency": True,
        }
    # ----------------------------------------------------------

    content = types.Content(
        role="user",
        parts=[types.Part(text=payload.message)],
    )
    final_response = ""

    async def run_agent():
        nonlocal final_response
        async for event in runner.run_async(
            user_id=payload.user_uid,
            session_id=session_id,
            new_message=content,
        ):
            if event.is_final_response():
                if event.content and event.content.parts:
                    for part in event.content.parts:
                        if part.text:
                            final_response += part.text

    try:
        await asyncio.wait_for(run_agent(), timeout=90.0)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Agent timeout. Coba lagi.")

    if not final_response:
        final_response = "Agent tidak memberikan respons."

    doc_id = _get_or_create_session_doc(session_id, payload.user_uid, is_new_session, payload.message)
    _save_message(doc_id, "user", payload.message)
    _save_message(doc_id, "ai", final_response)

    return {
        "session_id": session_id,
        "user_message": payload.message,
        "ai_response": final_response,
        "is_emergency": False,
    }


async def stream_chat(payload: PostChatSchema, runner) -> AsyncGenerator[str, None]:
    session_id, is_new_session = await _get_or_restore_session(runner, payload.user_uid, payload.session_id)

    yield f"data: [SESSION]{session_id}\n\n"

    # --- DETEKSI PERINGATAN DARURAT STREAMING (Bypass Agen LLM) ---
    if check_emergency(payload.message):
        emergency_reply = EMERGENCY_REPLY_MESSAGE

        words = emergency_reply.split(" ")
        for i, word in enumerate(words):
            spaced_word = word + (" " if i < len(words) - 1 else "")
            yield f"data: {json.dumps(spaced_word)}\n\n"
            await asyncio.sleep(0.01)

        doc_id = _get_or_create_session_doc(session_id, payload.user_uid, is_new_session, payload.message)
        _save_message(doc_id, "user", payload.message)
        _save_message(doc_id, "ai", emergency_reply)

        yield "data: [DONE]\n\n"
        return
    # --------------------------------------------------------------

    content = types.Content(
        role="user",
        parts=[types.Part(text=payload.message)],
    )
    full_response = ""

    try:
        async for event in runner.run_async(
            user_id=payload.user_uid,
            session_id=session_id,
            new_message=content,
        ):
            if event.content and event.content.parts:
                for part in event.content.parts:
                    text_chunk = part.text

                    if text_chunk and text_chunk not in full_response:
                        full_response += text_chunk

                        if len(text_chunk) > 30:
                            words = text_chunk.split(" ")
                            for i, word in enumerate(words):
                                spaced_word = word + (" " if i < len(words) - 1 else "")
                                yield f"data: {json.dumps(spaced_word)}\n\n"
                                await asyncio.sleep(0.02)
                        else:
                            yield f"data: {json.dumps(text_chunk)}\n\n"
                            await asyncio.sleep(0.01)

    except Exception as e:
        yield f"data: [ERROR]{str(e)}\n\n"
        return

    if not full_response:
        full_response = "Agent tidak memberikan respons."
        yield f"data: {json.dumps(full_response)}\n\n"

    doc_id = _get_or_create_session_doc(session_id, payload.user_uid, is_new_session, payload.message)
    _save_message(doc_id, "user", payload.message)
    _save_message(doc_id, "ai", full_response)

    yield "data: [DONE]\n\n"


def get_chat_by_session(
    session_id: str,
    current_user: Optional[Dict[str, Any]] = None,
) -> dict:
    """Ambil seluruh pesan dalam satu session dari subcollection."""
    doc_id, doc = _find_session_doc(session_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Session not found")

    owner_uid = doc.get("user")
    if current_user is not None and owner_uid:
        assert_patient_access(current_user, owner_uid)

    msgs = (
        db.collection(CHATS_COL).document(doc_id)
        .collection(MESSAGES_COL)
        .order_by("timestamp")
        .stream()
    )

    messages = []
    for msg in msgs:
        data = msg.to_dict() or {}
        messages.append({
            "role": data.get("role"),
            "response": data.get("response"),
            "timestamp": data.get("timestamp"),
        })

    return {
        "session_id": doc.get("sessionID"),
        "user": doc.get("user"),
        "messages": messages,
    }


def _generate_title(first_message: str) -> str:
    """Generate judul singkat dari pesan pertama user menggunakan Gemini."""
    try:
        genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
        model = genai.GenerativeModel("gemini-2.5-flash")
        response = model.generate_content(
            f"Buatkan judul singkat maksimal 5 kata dalam Bahasa Indonesia "
            f"untuk percakapan yang dimulai dengan pesan berikut. "
            f"Hanya balas dengan judulnya saja, tanpa tanda kutip, tanpa penjelasan.\n\n"
            f"Pesan: {first_message}"
        )
        title = response.text.strip().strip('"').strip("'")
        return title if title else "Konsultasi Kesehatan"
    except Exception:
        return "Konsultasi Kesehatan"


def get_chat_history(user_uid: str) -> list:
    """Ambil semua session milik user, diurutkan dari yang terbaru."""
    results = _find_docs_by_user(user_uid)
    if not results:
        return []

    history = []
    for doc_id, doc in results:
        last_msgs = (
            db.collection(CHATS_COL).document(doc_id)
            .collection(MESSAGES_COL)
            .order_by("timestamp", direction="DESCENDING")
            .limit(1)
            .stream()
        )
        last_timestamp = ""
        for msg in last_msgs:
            last_timestamp = (msg.to_dict() or {}).get("timestamp", "")

        title = (doc or {}).get("title") or "Konsultasi Kesehatan"

        history.append({
            "session_id": (doc or {}).get("sessionID"),
            "title": title,
            "last_timestamp": last_timestamp,
        })

    history.sort(key=lambda x: x.get("last_timestamp", ""), reverse=True)
    return history
