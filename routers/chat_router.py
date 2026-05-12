from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from schemas.chat_schema import PostChatSchema
from controllers import chat_controller
from services import chat_service

router = APIRouter(prefix="/chats", tags=["Chats"])


@router.post("/", summary="Post chat - kirim pesan ke AI dan simpan ke Firestore")
async def post_chat(payload: PostChatSchema, request: Request):
    runner = request.app.state.runner
    return await chat_controller.post_chat(payload, runner)



@router.post("/stream", summary="Stream chat response token per token (SSE)")
async def stream_chat(payload: PostChatSchema, request: Request):
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
def get_chat_by_session(session_id: str):
    return chat_controller.get_chat_by_session(session_id)


@router.get("/history/{user_uid}", summary="Get semua history chat milik user")
def get_chat_history(user_uid: str):
    return chat_controller.get_chat_history(user_uid)