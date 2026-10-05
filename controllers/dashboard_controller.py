from schemas.dashboard_schema import DashboardResponse
from services.dashboard_service import get_dashboard_report
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
