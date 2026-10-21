from schemas.photo_schema import PostPhotoSchema
from fastapi import BackgroundTasks
from services import photo_service

def save_photo(payload: PostPhotoSchema, background_tasks: BackgroundTasks):
    result = photo_service.save_photo(payload, background_tasks)
    return {
        "status": "success",
        "data": result
    }

def get_latest_two_photos(user_id: str):
    return photo_service.get_latest_two_photos(user_id)
