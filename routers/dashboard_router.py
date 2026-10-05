from fastapi import APIRouter, Query
from controllers import dashboard_controller

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])

@router.get("/", summary="Dashboard report harian")
def get_dashboard(user_id: str = Query(...), date: str = Query(...)):
    return dashboard_controller.get_dashboard(user_id, date)
