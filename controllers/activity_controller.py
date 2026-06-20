from schemas.activity_schema import GenerateActivitySchema
from services import activity_service

def process_daily_activities(payload: GenerateActivitySchema):
    result = activity_service.get_or_create_daily_activities(payload)
    return {
        "status": "success",
        "data": result
    }