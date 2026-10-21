from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers.xray_router import router as xray_router
from routers.user_router import router as user_router
from routers.chat_router import router as chat_router
from routers.activity_router import router as activity_router
from routers.smoke_router import router as smoke_router
from routers.photo_router import router as photo_router
from routers.dashboard_router import router as dashboard_router
from google.adk.runners import InMemoryRunner
from myAgent.agent import root_agent

app = FastAPI(
    title="Flutter Lungs Assistant Agent API",
    version="1.0.0",
    description="Backend API for Flutter Lungs Assistant Agent app",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Inisialisasi runner dan simpan ke app.state
runner = InMemoryRunner(agent=root_agent, app_name="lungs_assistant_agent")
app.state.runner = runner

app.include_router(user_router, prefix="/api")
app.include_router(chat_router, prefix="/api")
app.include_router(activity_router, prefix="/api")
app.include_router(smoke_router, prefix="/api")
app.include_router(photo_router, prefix="/api")
app.include_router(dashboard_router, prefix="/api")
app.include_router(xray_router, prefix="/api")
@app.get("/", tags=["Health"])
def health_check():
    return {"status": "ok", "message": "Server is running"}