from fastapi import APIRouter, Query, BackgroundTasks
from schemas.photo_schema import PostPhotoSchema
from controllers import photo_controller

router = APIRouter(prefix="/photos", tags=["Photos"])

@router.post("/", summary="Simpan gambar MRI dengan base64")
def save_photo(payload: PostPhotoSchema, background_tasks: BackgroundTasks): # <-- Tambahkan di sini
    return photo_controller.save_photo(payload, background_tasks)

@router.get("/compare", summary="Ambil 2 gambar MRI terbaru milik user")
def get_latest_two_photos(user_id: str = Query(..., description="ID user")):
    return photo_controller.get_latest_two_photos(user_id)
