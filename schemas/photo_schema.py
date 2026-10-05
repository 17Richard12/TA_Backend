from pydantic import BaseModel
from typing import Optional

class PostPhotoSchema(BaseModel):
    user_id: str
    foto_base64: str
