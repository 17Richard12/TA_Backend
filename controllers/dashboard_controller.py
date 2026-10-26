from schemas.dashboard_schema import DashboardResponse
# 1. Tambahkan 'as service_get_monthly_report' agar namanya tidak bentrok
from services.dashboard_service import get_dashboard_report, get_monthly_report as service_get_monthly_report
from datetime import datetime

def get_dashboard(user_id: str, date: str) -> DashboardResponse:
    """
    Mendapatkan report dashboard harian.
    date format: DD/MM/YYYY atau YYYY-MM-DD
    """
    try:
        # if format is DD/MM/YYYY, convert to YYYY-MM-DD
        if "/" in date:
            dt = datetime.strptime(date, "%d/%m/%Y")
            date_db = dt.strftime("%Y-%m-%d")
        else:
            date_db = date
            
        report = get_dashboard_report(user_id, date_db)
        return DashboardResponse(**report)
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise e

def get_monthly_report(user_id: str, year: int, month: int):
    # 2. Panggil fungsi menggunakan nama aliasnya (TANPA tanda titik di tengahnya)
    result = service_get_monthly_report(user_id, year, month)
    return result