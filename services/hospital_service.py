import os
from typing import Any, Dict, List
import requests
from dotenv import load_dotenv
from fastapi import HTTPException, status

load_dotenv()

PLACES_NEARBY_URL = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"


def _get_maps_api_key() -> str:
    key = (
        os.getenv("GOOGLE_MAPS_API_KEY")
        or os.getenv("GOOGLE_PLACES_API_KEY")
        or os.getenv("GOOGLE_API_KEY")
        or ""
    ).strip()
    if not key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google Maps API key is not configured on the server.",
        )
    return key


def filter_general_hospitals(raw_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Filters raw Google Places nearby search results to retain general hospitals
    and exclude maternity clinics (RSIA/bersalin), primary clinics/puskesmas,
    and dental/eye/beauty specialist clinics.
    Never fabricates doctor schedules.
    """
    general_hospitals: List[Dict[str, Any]] = []

    for place in raw_results or []:
        if not isinstance(place, dict):
            continue
        name = str(place.get("name") or "").strip()
        lower_name = name.lower()

        is_maternity = (
            "ibu dan anak" in lower_name
            or "rsia" in lower_name
            or "bersalin" in lower_name
        )
        is_clinic = "klinik" in lower_name or "puskesmas" in lower_name
        is_specialist = (
            "gigi" in lower_name
            or "mata" in lower_name
            or "kecantikan" in lower_name
        )

        if is_maternity or is_clinic or is_specialist:
            continue

        geometry = place.get("geometry") or {}
        location = geometry.get("location") or {}
        lat = location.get("lat")
        lng = location.get("lng")
        if lat is None or lng is None:
            continue

        opening_hours = place.get("opening_hours") or {}
        open_now = opening_hours.get("open_now") if isinstance(opening_hours, dict) else None

        general_hospitals.append({
            "name": name,
            "place_id": place.get("place_id", ""),
            "lat": float(lat),
            "lng": float(lng),
            "vicinity": place.get("vicinity") or "",
            "rating": place.get("rating"),
            "open_now": open_now,
            "doctor_schedules": [],
        })

    return general_hospitals


def get_nearby_hospitals(
    lat: float,
    lng: float,
    radius: int = 3000,
) -> Dict[str, Any]:
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lng <= 180.0):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Koordinat lokasi (lat/lng) tidak valid.",
        )

    clamped_radius = max(100, min(int(radius), 50000))
    api_key = _get_maps_api_key()

    params = {
        "location": f"{lat},{lng}",
        "radius": clamped_radius,
        "type": "hospital",
        "keyword": "rumah sakit umum",
        "key": api_key,
    }

    try:
        response = requests.get(PLACES_NEARBY_URL, params=params, timeout=15)
    except requests.RequestException:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Gagal menghubungi layanan Google Places API.",
        )

    if response.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Layanan Google Places mengembalikan status {response.status_code}.",
        )

    payload = response.json() if response.content else {}
    places_status = payload.get("status", "OK")
    if places_status not in ("OK", "ZERO_RESULTS"):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Google Places API status: {places_status}",
        )

    hospitals = filter_general_hospitals(payload.get("results") or [])
    return {
        "status": "success",
        "data": hospitals,
    }
