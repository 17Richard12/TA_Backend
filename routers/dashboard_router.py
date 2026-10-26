from fastapi import APIRouter, Query
from controllers import dashboard_controller

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])

@router.get("/", summary="Dashboard report harian")
def get_dashboard(user_id: str = Query(...), date: str = Query(...)):
    return dashboard_controller.get_dashboard(user_id, date)

@router.get("/monthly", summary="Ambil rekap laporan bulanan pasien")
def get_monthly_report(
    user_id: str = Query(..., description="ID user / pasien"),
    year: int = Query(..., description="Tahun, contoh: 2026"),
    month: int = Query(..., description="Bulan dalam angka, contoh: 10 untuk Oktober")
):
    return dashboard_controller.get_monthly_report(user_id, year, month)