from pydantic import BaseModel
from typing import Optional


class PostChatSchema(BaseModel):
    user_uid: str
    session_id: Optional[str] = None   # null = buat session baru
    message: str


class ChatResponse(BaseModel):
    session_id: str
    user_message: str
    ai_response: str