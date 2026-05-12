import asyncio
import json
import uuid
import google.generativeai as genai
import os
from datetime import datetime, timezone
from typing import AsyncGenerator
from fastapi import HTTPException
from google.genai import types

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


def _get_or_create_session_doc(session_id: str, user_uid: str, is_new_session: bool) -> str:
    """Return doc_id session. Buat baru jika belum ada."""
    if is_new_session:
        doc_id = _new_id()
        db.collection(CHATS_COL).document(doc_id).set({
            "user": user_uid,
            "sessionID": session_id,
        })
        return doc_id
    else:
        doc_id, existing = _find_session_doc(session_id)
        if doc_id:
            return doc_id
        # Session ada di ADK tapi belum ada di Firestore → buat baru
        doc_id = _new_id()
        db.collection(CHATS_COL).document(doc_id).set({
            "user": user_uid,
            "sessionID": session_id,
        })
        return doc_id


# ---------- service functions ----------

async def post_chat(payload: PostChatSchema, runner) -> dict:
    # 1. Tentukan session
    if payload.session_id is None:
        session = await runner.session_service.create_session(
            app_name="agent_doctor",
            user_id=payload.user_uid,
        )
        session_id = session.id
        is_new_session = True
    else:
        session_id = payload.session_id
        is_new_session = False

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
    doc_id = _get_or_create_session_doc(session_id, payload.user_uid, is_new_session)
    _save_message(doc_id, "user", payload.message)
    _save_message(doc_id, "ai", final_response)

    return {
        "session_id": session_id,
        "user_message": payload.message,
        "ai_response": final_response,
    }


async def stream_chat(payload: PostChatSchema, runner) -> AsyncGenerator[str, None]:
    # 1. Tentukan session
    if payload.session_id is None:
        session = await runner.session_service.create_session(
            app_name="agent_doctor",
            user_id=payload.user_uid,
        )
        session_id = session.id
        is_new_session = True
    else:
        session_id = payload.session_id
        is_new_session = False

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
            if (
                not event.is_final_response()
                and event.content
                and event.content.parts
            ):
                for part in event.content.parts:
                    if part.text:
                        full_response += part.text
                        # Encode sebagai JSON agar \n dalam teks tidak merusak format SSE
                        yield f"data: {json.dumps(part.text)}\n\n"

            elif event.is_final_response():
                if event.content and event.content.parts:
                    for part in event.content.parts:
                        if part.text and part.text not in full_response:
                            full_response += part.text
                            yield f"data: {json.dumps(part.text)}\n\n"

    except Exception as e:
        yield f"data: [ERROR]{str(e)}\n\n"
        return

    if not full_response:
        full_response = "Agent tidak memberikan respons."

    # 4. Simpan ke Firestore (subcollection messages)
    doc_id = _get_or_create_session_doc(session_id, payload.user_uid, is_new_session)
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
        model = genai.GenerativeModel("gemini-2.0-flash")
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
        # Ambil pesan pertama sebagai title source (selalu dari user)
        first_msgs = (
            db.collection(CHATS_COL).document(doc_id)
            .collection(MESSAGES_COL)
            .order_by("timestamp")
            .limit(1)
            .stream()
        )
        first_message = ""
        for msg in first_msgs:
            first_message = msg.to_dict().get("response", "")

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

        # Generate judul dari pesan pertama
        title = _generate_title(first_message) if first_message else "Konsultasi Kesehatan"

        history.append({
            "session_id": doc.get("sessionID"),
            "title": title,
            "last_timestamp": last_timestamp,
        })

    # Sort desc berdasarkan timestamp pesan terakhir
    history.sort(key=lambda x: x.get("last_timestamp", ""), reverse=True)

    return history