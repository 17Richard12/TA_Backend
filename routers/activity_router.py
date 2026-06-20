from fastapi import APIRouter
from schemas.activity_schema import GenerateActivitySchema
from controllers import activity_controller

router = APIRouter(prefix="/activities", tags=["Activities"])

@router.post("/process", summary="Get data jika tanggal hari ini sudah ada, atau buat baru (POST) lewat AI jika belum ada")
def process_activities(payload: GenerateActivitySchema):
    return activity_controller.process_daily_activities(payload)