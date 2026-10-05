from fastapi import APIRouter, Query
from schemas.smoke_schema import SmokeCountSchema
from controllers import smoke_controller

router = APIRouter(prefix="/smoke-count", tags=["Smoke Count"])

# Endpoint GET baru menggunakan query parameters
@router.get("/", summary="Get data smoke count berdasarkan userID dan timestamp")
def get_smoke(
    user_id: str = Query(...), 
    timestamp: str = Query(..., description="Format: dd/mm/yyyy")
):
    return smoke_controller.get_smoke(user_id, timestamp)

@router.get("/report", summary="Get 7-day smoke count report untuk kebutuhan grafik")
def get_smoke_report(
    user_id: str = Query(...), 
    timestamp: str = Query(..., description="Tanggal akhir report, Format: dd/mm/yyyy")
):
    return smoke_controller.get_smoke_report(user_id, timestamp)

@router.post("/", summary="Post smoke count (Otomatis update jika data userID & timestamp sudah ada)")
def post_smoke(payload: SmokeCountSchema):
    return smoke_controller.post_smoke(payload)

@router.put("/update", summary="Update data smoke count berdasarkan userID dan timestamp")
def update_smoke(payload: SmokeCountSchema):
    return smoke_controller.update_smoke(payload)