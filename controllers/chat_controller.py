from typing import Any, Dict, Optional
from schemas.chat_schema import PostChatSchema
from services import chat_service


async def post_chat(payload: PostChatSchema, runner):
    result = await chat_service.post_chat(payload, runner)
    return {
        "status": "success",
        "data": result,
    }


def get_chat_by_session(
    session_id: str,
    current_user: Optional[Dict[str, Any]] = None,
):
    result = chat_service.get_chat_by_session(session_id, current_user=current_user)
    return {
        "status": "success",
        "data": result,
    }


def get_chat_history(user_uid: str):
    result = chat_service.get_chat_history(user_uid)
    return {
        "status": "success",
        "data": result,
    }
