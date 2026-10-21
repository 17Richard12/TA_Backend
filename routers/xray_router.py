from fastapi import APIRouter, Query, BackgroundTasks
from schemas.xray_schema import PostXraySchema
from controllers import xray_controller

router = APIRouter(prefix="/xrays", tags=["X-Ray"])

@router.post("/", summary="Simpan gambar X-RAY dengan base64")
def save_xray(payload: PostXraySchema, background_tasks: BackgroundTasks):
    return xray_controller.save_xray(payload, background_tasks)

@router.get("/compare", summary="Ambil 2 gambar X-RAY terbaru milik user")
def get_latest_two_xrays(user_id: str = Query(..., description="ID user")):
    return xray_controller.get_latest_two_xrays(user_id)