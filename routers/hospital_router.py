from typing import Any, Dict
from fastapi import APIRouter, Depends, Query
from config.security import get_current_user
from services.hospital_service import get_nearby_hospitals

router = APIRouter(prefix="/hospitals", tags=["Hospitals"])


@router.get("/nearby")
def fetch_nearby_hospitals_endpoint(
    lat: float = Query(..., ge=-90.0, le=90.0, description="Latitude pengguna"),
    lng: float = Query(..., ge=-180.0, le=180.0, description="Longitude pengguna"),
    radius: int = Query(3000, ge=100, le=50000, description="Radius pencarian dalam meter"),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    return get_nearby_hospitals(lat=lat, lng=lng, radius=radius)
