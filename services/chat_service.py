import asyncio
import json
import uuid
import google.generativeai as genai
import os
from datetime import datetime, timezone
from typing import AsyncGenerator, Optional
from fastapi import HTTPException
from google.genai import types
from google.adk.events import Event
from google.cloud.firestore_v1.base_query import FieldFilter

from config.firebase import db
from schemas.chat_schema import PostChatSchema

CHATS_COL = "chats"
MESSAGES_COL = "messages"


# ---------- helpers ----------

def _new_id() -> str:
    return str(uuid.uuid4()).replace("-", "")[:20]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _find_session_doc(session_id: str):
    """Cari dokumen session berdasarkan sessionID."""
    docs = (
        db.collection(CHATS_COL)
        .where("sessionID", "==", session_id)
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
        .where("user", "==", user_uid)
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
            # Jika dokumen sudah ada, pastikan tidak menimpa title yang sudah ada
            return doc_id
        # Session ada di ADK tapi belum ada di Firestore → buat baru
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
    """
    # 1. Profil Pasien
    profile_lines = []
    try:
        user_doc = db.collection("accounts").document(user_uid).get()
        if user_doc.exists:
            u_data = user_doc.to_dict() or {}
            name = u_data.get("name") or "-"
            gender = u_data.get("gender") or "-"
            birth = u_data.get("birth") or "-"
            blood = u_data.get("bloodType") or "-"
            height = u_data.get("height") or "-"
            weight = u_data.get("weight") or "-"
            history = u_data.get("history") or "-"

            profile_lines.append(f"- Nama: {name}")
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

    # 2. Riwayat Merokok 5 Hari Terakhir
    smoke_lines = []
    try:
        smoke_docs = (
            db.collection("smokeCount")
            .where(filter=FieldFilter("userID", "==", user_uid))
            .stream()
        )
        records = []
        for doc in smoke_docs:
            d = doc.to_dict()
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

    # 3. Aktivitas Sehat yang Diselesaikan
    activity_lines = []
    try:
        act_docs = (
            db.collection("dailyActivities")
            .where(filter=FieldFilter("userID", "==", user_uid))
            .stream()
        )
        act_records = []
        for doc in act_docs:
            d = doc.to_dict()
            ts = d.get("timestamp", "")
            sub_docs = (
                doc.reference.collection("activities")
                .where(filter=FieldFilter("done", "==", True))
                .stream()
            )
            for sub in sub_docs:
                sd = sub.to_dict()
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

    # 4. Gabungkan seluruh konteks
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
        # Sesi baru sama sekali
        session = await runner.session_service.create_session(
            app_name="lungs_assistant_agent",
            user_id=user_uid,
            state=patient_state,
        )
        return session.id, True

    # Jika requested_session_id diberikan, cek apakah sudah aktif di memori runner
    session = await runner.session_service.get_session(
        app_name="lungs_assistant_agent",
        user_id=user_uid,
        session_id=requested_session_id,
    )

    if session is not None:
        # Sesi sudah ada di memori runner. Pastikan state di storage runner ter-update dengan konteks pasien terbaru
        app_name = "lungs_assistant_agent"
        if (
            hasattr(runner.session_service, "sessions")
            and app_name in runner.session_service.sessions
            and user_uid in runner.session_service.sessions[app_name]
            and requested_session_id in runner.session_service.sessions[app_name][user_uid]
        ):
            runner.session_service.sessions[app_name][user_uid][requested_session_id].state.update(patient_state)
        return requested_session_id, False

    # Sesi TIDAK ada di memori runner (misalnya setelah server restart atau sesi dibuka kembali)
    session = await runner.session_service.create_session(
        app_name="lungs_assistant_agent",
        user_id=user_uid,
        session_id=requested_session_id,
        state=patient_state,
    )

    # Pulihkan riwayat pesan dari Firestore ke session memory
    doc_id, _ = _find_session_doc(requested_session_id)
    if doc_id:
        msgs_stream = (
            db.collection(CHATS_COL).document(doc_id)
            .collection(MESSAGES_COL)
            .order_by("timestamp")
            .stream()
        )
        all_msgs = [m.to_dict() for m in msgs_stream]

        # Ambil N pesan terakhir (20 pesan / 10 turn percakapan terakhir)
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
                    content=types.Content(role="user", parts=[types.Part(text=text)])
                )
                await runner.session_service.append_event(session, event)
            elif role in ["ai", "assistant", "model"]:
                event = Event(
                    author="healthcare_coordinator",
                    content=types.Content(role="model", parts=[types.Part(text=text)])
                )
                await runner.session_service.append_event(session, event)

    return requested_session_id, False


# ---------- service functions ----------

async def post_chat(payload: PostChatSchema, runner) -> dict:
    # 1. Tentukan session (ambil dari memori atau pulihkan dari Firestore)
    session_id, is_new_session = await _get_or_restore_session(runner, payload.user_uid, payload.session_id)

    # 2. Jalankan agent
    content = types.Content(
        role="user",
        parts=[types.Part(text=payload.message)]
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

    # 3. Simpan ke Firestore (subcollection messages)
    doc_id = _get_or_create_session_doc(session_id, payload.user_uid, is_new_session, payload.message)
    _save_message(doc_id, "user", payload.message)
    _save_message(doc_id, "ai", final_response)

    return {
        "session_id": session_id,
        "user_message": payload.message,
        "ai_response": final_response,
    }


async def stream_chat(payload: PostChatSchema, runner) -> AsyncGenerator[str, None]:
    # 1. Tentukan session (ambil dari memori atau pulihkan dari Firestore)
    session_id, is_new_session = await _get_or_restore_session(runner, payload.user_uid, payload.session_id)

    # 2. Kirim session_id ke client
    yield f"data: [SESSION]{session_id}\n\n"

    # 3. Stream token dari agent
    content = types.Content(
        role="user",
        parts=[types.Part(text=payload.message)]
    )
    full_response = ""

    try:
        async for event in runner.run_async(
            user_id=payload.user_uid,
            session_id=session_id,
            new_message=content,
        ):
            # Ambil bagian teks dari event apa pun (final atau non-final)
            if event.content and event.content.parts:
                for part in event.content.parts:
                    text_chunk = part.text
                    
                    # Pastikan teks tidak kosong dan belum pernah dikirim (mencegah duplikasi di event final)
                    if text_chunk and text_chunk not in full_response:
                        full_response += text_chunk
                        
                        # TOKEN SIMULATOR: 
                        # Jika teks yang diterima sangat besar (ciri khas hasil Tool Call RAG)
                        if len(text_chunk) > 30:
                            # Pecah menjadi kata per kata
                            words = text_chunk.split(" ")
                            for i, word in enumerate(words):
                                # Kembalikan spasi yang hilang
                                spaced_word = word + (" " if i < len(words) - 1 else "")
                                yield f"data: {json.dumps(spaced_word)}\n\n"
                                # Jeda buatan 20 milidetik per kata agar natural
                                await asyncio.sleep(0.02) 
                        else:
                            # Jika LLM memang mengirim token kecil secara natural, langsung yield
                            yield f"data: {json.dumps(text_chunk)}\n\n"
                            await asyncio.sleep(0.01)

    except Exception as e:
        yield f"data: [ERROR]{str(e)}\n\n"
        return

    if not full_response:
        full_response = "Agent tidak memberikan respons."
        yield f"data: {json.dumps(full_response)}\n\n"

    # 4. Simpan ke Firestore (subcollection messages)
    doc_id = _get_or_create_session_doc(session_id, payload.user_uid, is_new_session, payload.message)
    _save_message(doc_id, "user", payload.message)
    _save_message(doc_id, "ai", full_response)

    yield f"data: [DONE]\n\n"


def get_chat_by_session(session_id: str) -> dict:
    """Ambil seluruh pesan dalam satu session dari subcollection."""
    doc_id, doc = _find_session_doc(session_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Session not found")

    # Ambil semua messages, urutkan by timestamp
    msgs = (
        db.collection(CHATS_COL).document(doc_id)
        .collection(MESSAGES_COL)
        .order_by("timestamp")
        .stream()
    )

    messages = []
    for msg in msgs:
        data = msg.to_dict()
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
        model = genai.GenerativeModel("gemini-3.5-flash")
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
        # Ambil timestamp pesan terakhir untuk sorting
        last_msgs = (
            db.collection(CHATS_COL).document(doc_id)
            .collection(MESSAGES_COL)
            .order_by("timestamp", direction="DESCENDING")
            .limit(1)
            .stream()
        )
        last_timestamp = ""
        for msg in last_msgs:
            last_timestamp = msg.to_dict().get("timestamp", "")

        # Ambil judul dari field database (sudah digenerate saat create session)
        title = doc.get("title")
        if not title:
            title = "Konsultasi Kesehatan"

        history.append({
            "session_id": doc.get("sessionID"),
            "title": title,
            "last_timestamp": last_timestamp,
        })

    # Sort desc berdasarkan timestamp pesan terakhir
    history.sort(key=lambda x: x.get("last_timestamp", ""), reverse=True)

    return history