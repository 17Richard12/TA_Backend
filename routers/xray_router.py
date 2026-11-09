from typing import Any, Dict
from fastapi import APIRouter, BackgroundTasks, Depends, Query
from config.security import assert_patient_access, get_current_user
from schemas.xray_schema import PostXraySchema, AnalyzeXraySchema
from controllers import xray_controller

router = APIRouter(prefix="/xrays", tags=["X-Ray"])


@router.post("/", summary="Simpan gambar X-RAY dengan base64")
def save_xray(
    payload: PostXraySchema,
    background_tasks: BackgroundTasks,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    assert_patient_access(current_user, payload.user_id)
    return xray_controller.save_xray(payload, background_tasks)


@router.post("/analyze", summary="Mulai analisis AI manual untuk 2 X-Ray terbaru")
def analyze_xray(
    payload: AnalyzeXraySchema,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    assert_patient_access(current_user, payload.user_id)
    return xray_controller.analyze_xray(payload)


@router.get("/compare", summary="Ambil 2 gambar X-RAY terbaru milik user")
def get_latest_two_xrays(
    user_id: str = Query(..., description="ID user"),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    assert_patient_access(current_user, user_id)
    return xray_controller.get_latest_two_xrays(user_id)
