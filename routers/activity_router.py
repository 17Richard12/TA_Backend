from fastapi import APIRouter
from schemas.activity_schema import GenerateActivitySchema, UpdateChecklistSchema
from controllers import activity_controller
from fastapi import APIRouter, Query

router = APIRouter(prefix="/activities", tags=["Activities"])

@router.get("/report", summary="Get 7-day completed activity report")
def get_activity_report(
    user_id: str = Query(...), 
    timestamp: str = Query(..., description="Format: dd/mm/yyyy")
):
    return activity_controller.get_activity_report(user_id, timestamp)

@router.get("/streak", summary="Mendapatkan jumlah hari berturut-turut mengerjakan aktivitas")
def get_activity_streak(
    user_id: str = Query(...), 
    timestamp: str = Query(..., description="Format: dd/mm/yyyy")
):
    return activity_controller.get_streak_count(user_id, timestamp)

@router.post("/process", summary="Get data jika tanggal hari ini sudah ada, atau buat baru (POST) lewat AI jika belum ada")
def process_activities(payload: GenerateActivitySchema):
    return activity_controller.process_daily_activities(payload)

@router.patch("/{daily_activity_id}/items/{activity_id}/check", summary="Mengubah status checklist (done) pada aktivitas tertentu")
def update_activity_checklist(daily_activity_id: str, activity_id: str, payload: UpdateChecklistSchema):
    return activity_controller.update_checklist_status(daily_activity_id, activity_id, payload)