"""Re:hear-only application candidate; not yet an operational cutover."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from auth.router import router as auth_router
from odi.router import router as odi_router

app = FastAPI()
app.include_router(auth_router)
app.include_router(odi_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:9000", "http://localhost:5173",
        "https://chobab.app", "https://aura.chobab.app",
        "https://rehear.chobab.app", "https://rehear.kr",
        "https://calender.chobab.app", "https://calendar.chobab.app",
        "https://netaq.chobab.app", "https://ai.chobab.app",
        "https://territories-tickets-donna-twist.trycloudflare.com",
    ],
    allow_origin_regex=r"https://.*\.trycloudflare\.com",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
