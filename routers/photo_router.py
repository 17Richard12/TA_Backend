from typing import Any, Dict
from fastapi import APIRouter, BackgroundTasks, Depends, Query
from config.security import assert_patient_access, get_current_user
from schemas.photo_schema import PostPhotoSchema, AnalyzePhotoSchema
from controllers import photo_controller

router = APIRouter(prefix="/photos", tags=["Photos"])


@router.post("/", summary="Simpan gambar CT Scan/MRI dengan base64")
def save_photo(
    payload: PostPhotoSchema,
    background_tasks: BackgroundTasks,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    assert_patient_access(current_user, payload.user_id)
    return photo_controller.save_photo(payload, background_tasks)


@router.post("/analyze", summary="Mulai analisis AI manual untuk 2 CT Scan/MRI terbaru")
def analyze_photo(
    payload: AnalyzePhotoSchema,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    assert_patient_access(current_user, payload.user_id)
    return photo_controller.analyze_photo(payload)


@router.get("/compare", summary="Ambil 2 gambar CT Scan/MRI terbaru milik user")
def get_latest_two_photos(
    user_id: str = Query(..., description="ID user"),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    assert_patient_access(current_user, user_id)
    return photo_controller.get_latest_two_photos(user_id)
