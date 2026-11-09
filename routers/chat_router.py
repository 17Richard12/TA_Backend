from typing import Any, Dict
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from config.security import assert_patient_access, get_current_user
from schemas.chat_schema import PostChatSchema
from controllers import chat_controller
from services import chat_service

router = APIRouter(prefix="/chats", tags=["Chats"])


@router.post("/", summary="Post chat - kirim pesan ke AI dan simpan ke Firestore")
async def post_chat(
    payload: PostChatSchema,
    request: Request,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    assert_patient_access(current_user, payload.user_uid)
    runner = request.app.state.runner
    return await chat_controller.post_chat(payload, runner)


@router.post("/stream", summary="Stream chat response token per token (SSE)")
async def stream_chat(
    payload: PostChatSchema,
    request: Request,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    assert_patient_access(current_user, payload.user_uid)
    runner = request.app.state.runner
    return StreamingResponse(
        chat_service.stream_chat(payload, runner),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/session/{session_id}", summary="Get semua chat dalam satu session")
def get_chat_by_session(
    session_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    return chat_controller.get_chat_by_session(session_id, current_user=current_user)


@router.get("/history/{user_uid}", summary="Get semua history chat milik user")
def get_chat_history(
    user_uid: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    assert_patient_access(current_user, user_uid)
    return chat_controller.get_chat_history(user_uid)
