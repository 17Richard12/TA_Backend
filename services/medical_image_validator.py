"""
Validasi pre-processing untuk gambar medis (X-Ray / CT Scan).

Alur:
1. Validasi struktural (base64 valid, format JPEG/PNG/WEBP, ukuran wajar).
2. Klasifikasi modality dengan Gemini (JSON terstruktur):
   xray | ct_scan | mri | other_medical | non_medical
3. Tolak jika bukan gambar medis, atau modality tidak sesuai fitur.
"""
import os
import json
import base64
import binascii
from typing import Iterable

import google.generativeai as genai
from fastapi import HTTPException

genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))

VALIDATOR_MODEL = os.getenv("MEDICAL_VALIDATOR_MODEL", "gemini-3.7-flash")
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB
MIN_IMAGE_BYTES = 1024             # < 1 KB hampir pasti bukan gambar valid
MIN_CONFIDENCE = 0.6

MODALITY_LABELS = {
    "xray": "X-Ray",
    "ct_scan": "CT Scan",
    "mri": "MRI",
    "other_medical": "gambar medis lain",
    "non_medical": "gambar non-medis",
}

_CLASSIFIER_PROMPT = """Anda adalah sistem verifikasi citra medis.
Klasifikasikan gambar ini ke SATU kategori:
- "xray": radiografi sinar-X (misal X-Ray dada/thorax)
- "ct_scan": potongan CT Scan (computed tomography)
- "mri": potongan MRI
- "other_medical": citra medis lain (USG, foto luka klinis, dll.)
- "non_medical": selain citra medis (selfie, pemandangan, dokumen, screenshot, makanan, dll.)

Jawab HANYA dalam JSON dengan format:
{"modality": "<kategori>", "confidence": <angka 0-1>, "reason": "<alasan singkat bahasa Indonesia>"}"""


def _decode_base64_image(foto_base64: str) -> bytes:
    raw = foto_base64.split(",", 1)[-1] if "," in foto_base64 else foto_base64
    try:
        return base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=422, detail="File gambar rusak atau bukan format base64 yang valid.")


def detect_mime_type(image_bytes: bytes) -> str | None:
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    return None


def _classify_with_gemini(image_bytes: bytes, mime_type: str) -> dict:
    model = genai.GenerativeModel(VALIDATOR_MODEL)
    response = model.generate_content(
        [_CLASSIFIER_PROMPT, {"mime_type": mime_type, "data": image_bytes}],
        generation_config={"response_mime_type": "application/json", "temperature": 0},
    )
    result = json.loads(response.text)
    modality = str(result.get("modality", "non_medical")).strip().lower()
    if modality not in MODALITY_LABELS:
        modality = "non_medical"
    try:
        confidence = float(result.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0.0
    return {
        "modality": modality,
        "confidence": confidence,
        "reason": str(result.get("reason", "")),
    }


def validate_medical_image(foto_base64: str, accepted_modalities: Iterable[str], feature_label: str) -> dict:
    """
    Raise HTTPException(422) jika gambar ditolak.
    Return hasil klasifikasi jika lolos.
    """
    accepted = set(accepted_modalities)
    image_bytes = _decode_base64_image(foto_base64)

    # 1. Validasi struktural
    if len(image_bytes) < MIN_IMAGE_BYTES:
        raise HTTPException(status_code=422, detail="File gambar terlalu kecil atau rusak.")
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=422, detail="Ukuran gambar melebihi 5 MB.")

    mime_type = detect_mime_type(image_bytes)
    if mime_type is None:
        raise HTTPException(status_code=422, detail="Format file tidak didukung. Gunakan JPG, PNG, atau WEBP.")

    # 2. Klasifikasi AI (fail-closed: jika AI gagal, upload ditolak agar data tetap bersih)
    try:
        result = _classify_with_gemini(image_bytes, mime_type)
    except Exception as e:
        print(f"[MedicalValidator] Gagal klasifikasi: {e}")
        raise HTTPException(
            status_code=503,
            detail="Sistem validasi gambar sedang tidak tersedia. Silakan coba beberapa saat lagi.",
        )

    modality = result["modality"]
    print(f"[MedicalValidator] {feature_label}: {result}")

    # 3. Keputusan
    if modality == "non_medical":
        raise HTTPException(
            status_code=422,
            detail=f"Gambar ditolak: file yang diunggah bukan gambar {feature_label}. {result['reason']}".strip(),
        )

    if modality not in accepted:
        detected = MODALITY_LABELS.get(modality, modality)
        raise HTTPException(
            status_code=422,
            detail=f"Gambar terdeteksi sebagai {detected}, bukan {feature_label}. Silakan unggah gambar {feature_label} yang sesuai.",
        )

    if result["confidence"] < MIN_CONFIDENCE:
        raise HTTPException(
            status_code=422,
            detail=f"Sistem tidak yakin gambar ini adalah {feature_label}. Pastikan gambar jelas dan tidak terpotong.",
        )

    return {**result, "mime_type": mime_type}
