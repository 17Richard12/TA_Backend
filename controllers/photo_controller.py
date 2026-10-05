from schemas.photo_schema import PostPhotoSchema
from services import photo_service

def save_photo(payload: PostPhotoSchema):
    result = photo_service.save_photo(payload)
    return {
        "status": "success",
        "data": result
    }

def get_latest_two_photos(user_id: str):
    return photo_service.get_latest_two_photos(user_id)
