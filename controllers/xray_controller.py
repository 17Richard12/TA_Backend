from schemas.xray_schema import PostXraySchema
from services import xray_service
from fastapi import BackgroundTasks

def save_xray(payload: PostXraySchema, background_tasks: BackgroundTasks):
    result = xray_service.save_xray(payload, background_tasks)
    return {"status": "success", "data": result}

def get_latest_two_xrays(user_id: str):
    return xray_service.get_latest_two_xrays(user_id)