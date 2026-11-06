from schemas.xray_schema import PostXraySchema, AnalyzeXraySchema
from services import xray_service
from fastapi import BackgroundTasks

def save_xray(payload: PostXraySchema, background_tasks: BackgroundTasks):
    result = xray_service.save_xray(payload, background_tasks)
    return {"status": "success", "data": result}

def get_latest_two_xrays(user_id: str):
    return xray_service.get_latest_two_xrays(user_id)

def analyze_xray(payload: AnalyzeXraySchema):
    return xray_service.start_xray_analysis(payload.user_id)