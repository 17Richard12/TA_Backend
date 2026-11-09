import os
import uuid
import datetime
import base64
from fastapi import BackgroundTasks
import google.generativeai as genai
from google.cloud.firestore_v1.base_query import FieldFilter
from config.firebase import db
from schemas.xray_schema import PostXraySchema
from services.medical_image_validator import (
    _decode_base64_image,
    detect_mime_type,
    validate_medical_image,
)

XRAYS_COL = "xrays"
MEDICAL_ANALYSIS_MODEL = os.getenv("MEDICAL_ANALYSIS_MODEL", "gemini-2.5-flash")
MEDICAL_DISCLAIMER = "*Catatan: Ini bukan diagnosis medis. Harap konsultasikan dengan dokter.*"

genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))


def _prepare_image_part(b64_str: str) -> dict:
    image_bytes = _decode_base64_image(b64_str)
    mime_type = detect_mime_type(image_bytes) or "image/jpeg"
    return {"mime_type": mime_type, "data": image_bytes}


def _ensure_disclaimer(text: str) -> str:
    cleaned = (text or "").strip()
    if "bukan diagnosis medis" not in cleaned.lower():
        cleaned = f"{cleaned}\n\n{MEDICAL_DISCLAIMER}"
    return cleaned


def background_analyze(doc_id: str, old_base64: str, new_base64: str):
    analysis_text = analyze_xray_with_gemini(old_base64, new_base64)
    db.collection(XRAYS_COL).document(doc_id).update({
        "analysis_result": analysis_text,
    })


def analyze_xray_with_gemini(old_base64: str, new_base64: str) -> str:
    try:
        model = genai.GenerativeModel(MEDICAL_ANALYSIS_MODEL)
        old_part = _prepare_image_part(old_base64)
        new_part = _prepare_image_part(new_base64)

        prompt = (
            "Anda adalah asisten medis AI. Bandingkan dua gambar X-Ray ini "
            "(Gambar 1: Lama, Gambar 2: Terbaru). "
            "Berikan analisis maksimal 2 kalimat yang HANYA FOKUS pada perubahan kondisi fisik "
            "paru-paru (misal: bercak, konsolidasi, atau kebersihan area paru). "
            "Abaikan perbedaan resolusi, kecerahan, ukuran, atau penanda digital pada gambar. "
            f"Tutup dengan: '{MEDICAL_DISCLAIMER}'"
        )

        response = model.generate_content([prompt, old_part, new_part])
        return _ensure_disclaimer(response.text)
    except Exception as e:
        return f"Gagal menghasilkan analisis: {str(e)}"


def _new_id() -> str:
    return str(uuid.uuid4()).replace("-", "")[:20]


def save_xray(payload: PostXraySchema, background_tasks: BackgroundTasks = None) -> dict:
    # 0. Validasi pre-processing: tolak jika bukan gambar X-Ray (raise HTTP 422)
    validation = validate_medical_image(
        payload.foto_base64,
        accepted_modalities={"xray"},
        feature_label="X-Ray",
    )

    raw_b64 = payload.foto_base64.split(",", 1)[-1] if "," in payload.foto_base64 else payload.foto_base64
    mime_type = validation.get("mime_type", "image/jpeg")
    normalized_foto = f"data:{mime_type};base64,{raw_b64}"

    doc_id = _new_id()

    # Simpan data X-Ray TANPA auto-analysis (analisis dijalankan manual via tombol "Start Analysis")
    data = {
        "userID": payload.user_id,
        "foto": normalized_foto,
        "timestamp": datetime.datetime.now(datetime.timezone.utc),
        "analysis_result": "",
    }
    db.collection(XRAYS_COL).document(doc_id).set(data)

    return {
        "id": doc_id,
        "action": "inserted",
        "message": "Foto X-RAY berhasil diupload. Tekan tombol 'Start Analysis' untuk memulai analisis AI.",
    }


def start_xray_analysis(user_id: str) -> dict:
    """Menjalankan analisis AI secara manual berdasarkan 2 gambar X-Ray terbaru milik user."""
    try:
        docs = (
            db.collection(XRAYS_COL)
            .where(filter=FieldFilter("userID", "==", user_id))
            .stream()
        )
        xrays_raw = []
        for doc in docs:
            d = doc.to_dict() or {}
            xrays_raw.append({
                "id": doc.id,
                "foto": d.get("foto", ""),
                "timestamp": d.get("timestamp"),
            })
        xrays_raw.sort(
            key=lambda x: x["timestamp"] if x["timestamp"] else datetime.datetime.min.replace(tzinfo=datetime.timezone.utc),
            reverse=True,
        )

        if len(xrays_raw) < 2:
            return {
                "status": "error",
                "message": "Dibutuhkan minimal 2 gambar X-Ray (Sebelumnya & Terbaru) untuk memulai analisis perbandingan.",
            }

        newest = xrays_raw[0]
        older = xrays_raw[1]
        analysis_text = analyze_xray_with_gemini(older["foto"], newest["foto"])

        if analysis_text.startswith("Gagal menghasilkan analisis"):
            return {
                "status": "error",
                "message": analysis_text,
            }

        db.collection(XRAYS_COL).document(newest["id"]).update({
            "analysis_result": analysis_text,
        })

        return {
            "status": "success",
            "data": {
                "id": newest["id"],
                "analysis": analysis_text,
            },
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
        }


def get_latest_two_xrays(user_id: str) -> dict:
    try:
        docs = (
            db.collection(XRAYS_COL)
            .where(filter=FieldFilter("userID", "==", user_id))
            .stream()
        )

        xrays_raw = []
        for doc in docs:
            data = doc.to_dict() or {}
            xrays_raw.append({
                "id": doc.id,
                "foto": data.get("foto", ""),
                "timestamp": data.get("timestamp"),
                "analysis_result": data.get("analysis_result", ""),
            })

        xrays_raw.sort(
            key=lambda x: x["timestamp"] if x["timestamp"] else datetime.datetime.min.replace(tzinfo=datetime.timezone.utc),
            reverse=True,
        )

        xrays = []
        for p in xrays_raw[:2]:
            ts = p["timestamp"]
            if isinstance(ts, datetime.datetime):
                ts = ts.isoformat()

            xrays.append({
                "id": p["id"],
                "foto": p["foto"],
                "timestamp": ts,
            })

        analysis_text = ""
        if len(xrays_raw) > 0:
            analysis_text = xrays_raw[0].get(
                "analysis_result",
                "Upload satu gambar X-Ray lagi untuk melihat perbandingan dan analisis AI.",
            )
        else:
            analysis_text = "Belum ada data X-Ray yang diupload."

        return {
            "status": "success",
            "data": {
                "user_id": user_id,
                "xrays": xrays,
                "analysis": analysis_text,
            },
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
        }
