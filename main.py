from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers.user_router import router as user_router
from routers.chat_router import router as chat_router
from google.adk.runners import InMemoryRunner
from myAgent.agent import root_agent

app = FastAPI(
    title="Flutter Agent Doctor API",
    version="1.0.0",
    description="Backend API for Flutter Agent Doctor app",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Inisialisasi runner dan simpan ke app.state
runner = InMemoryRunner(agent=root_agent, app_name="agent_doctor")
app.state.runner = runner

app.include_router(user_router, prefix="/api")
app.include_router(chat_router, prefix="/api")


@app.get("/", tags=["Health"])
def health_check():
    return {"status": "ok", "message": "Server is running"}