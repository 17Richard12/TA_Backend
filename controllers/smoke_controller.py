from schemas.smoke_schema import SmokeCountSchema
from services import smoke_service

def post_smoke(payload: SmokeCountSchema):
    result = smoke_service.post_smoke_count(payload)
    return {
        "status": "success",
        "data": result
    }

def update_smoke(payload: SmokeCountSchema):
    result = smoke_service.update_smoke_count(payload)
    return {
        "status": "success",
        "data": result
    }

def get_smoke(user_id: str, timestamp: str):
    result = smoke_service.get_smoke_count(user_id, timestamp)
    return {
        "status": "success",
        "data": result
    }

def get_smoke_report(user_id: str, timestamp: str):
    try:
        result = smoke_service.get_weekly_report(user_id, timestamp)
        return {
            "status": "success",
            "data": result
        }
    except ValueError as e:
        # Menangani error jika format timestamp salah
        return {
            "status": "error",
            "message": str(e)
        }
    except Exception as e:
        return {
            "status": "error",
            "message": "Terjadi kesalahan pada server",
            "detail": str(e)
        }