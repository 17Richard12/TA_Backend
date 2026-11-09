from typing import Any, Dict
from fastapi import APIRouter, Depends, Query
from config.security import assert_patient_access, get_current_user
from schemas.smoke_schema import SmokeCountSchema
from controllers import smoke_controller

router = APIRouter(prefix="/smoke-count", tags=["Smoke Count"])


@router.get("/", summary="Get data smoke count berdasarkan userID dan timestamp")
def get_smoke(
    user_id: str = Query(...),
    timestamp: str = Query(..., description="Format: dd/mm/yyyy atau YYYY-MM-DD"),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    assert_patient_access(current_user, user_id)
    return smoke_controller.get_smoke(user_id, timestamp)


@router.get("/report", summary="Get 7-day smoke count report untuk kebutuhan grafik")
def get_smoke_report(
    user_id: str = Query(...),
    timestamp: str = Query(..., description="Tanggal akhir report, Format: dd/mm/yyyy"),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    assert_patient_access(current_user, user_id)
    return smoke_controller.get_smoke_report(user_id, timestamp)


@router.post("/", summary="Post smoke count (Otomatis update jika data userID & timestamp sudah ada)")
def post_smoke(
    payload: SmokeCountSchema,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    assert_patient_access(current_user, payload.user_id)
    return smoke_controller.post_smoke(payload)


@router.put("/update", summary="Update data smoke count berdasarkan userID dan timestamp")
def update_smoke(
    payload: SmokeCountSchema,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    assert_patient_access(current_user, payload.user_id)
    return smoke_controller.update_smoke(payload)
