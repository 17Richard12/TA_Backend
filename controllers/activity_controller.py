from fastapi import HTTPException

from schemas.activity_schema import GenerateActivitySchema, UpdateChecklistSchema
from services import activity_service

def process_daily_activities(payload: GenerateActivitySchema):
    result = activity_service.get_or_create_daily_activities(payload)
    return {
        "status": "success",
        "data": result
    }

def update_checklist_status(daily_activity_id: str, activity_id: str, payload: UpdateChecklistSchema):
    result = activity_service.update_activity_status(daily_activity_id, activity_id, payload.done)
    
    if not result:
        raise HTTPException(status_code=404, detail="Activity document not found")
        
    return {
        "status": "success",
        "message": "Checklist status updated successfully",
        "data": result
    }

def get_activity_report(user_id: str, timestamp: str):
    try:
        result = activity_service.get_weekly_activity_report(user_id, timestamp)
        return {
            "status": "success",
            "data": result
        }
    except ValueError as e:
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

def get_streak_count(user_id: str, timestamp: str):
    try:
        streak = activity_service.get_activity_streak(user_id, timestamp)
        return {
            "status": "success",
            "data": {
                "streak": streak
            }
        }
    except ValueError as e:
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