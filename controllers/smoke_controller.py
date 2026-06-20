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