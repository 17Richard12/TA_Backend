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