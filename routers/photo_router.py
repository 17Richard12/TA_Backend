from fastapi import APIRouter, Query, BackgroundTasks
from schemas.photo_schema import PostPhotoSchema, AnalyzePhotoSchema
from controllers import photo_controller

router = APIRouter(prefix="/photos", tags=["Photos"])

@router.post("/", summary="Simpan gambar MRI dengan base64")
def save_photo(payload: PostPhotoSchema, background_tasks: BackgroundTasks): # <-- Tambahkan di sini
    return photo_controller.save_photo(payload, background_tasks)

@router.post("/analyze", summary="Mulai analisis AI manual untuk 2 CT Scan/MRI terbaru")
def analyze_photo(payload: AnalyzePhotoSchema):
    return photo_controller.analyze_photo(payload)

@router.get("/compare", summary="Ambil 2 gambar MRI terbaru milik user")
def get_latest_two_photos(user_id: str = Query(..., description="ID user")):
    return photo_controller.get_latest_two_photos(user_id)
