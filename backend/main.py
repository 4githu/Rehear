"""Re:hear-only application candidate; not yet an operational cutover."""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from auth.router import router as auth_router
from odi.router import router as odi_router
from odi.db import odidb


def initialize_odi_and_recover():
    # Fail closed: never manufacture an empty database if the data mount is absent.
    db_path = odidb.DB_PATH
    if not db_path.is_file():
        raise RuntimeError('ODI database is missing; refusing startup')
    odidb.ensure_report_schema(db_path)
    odidb.recover_stale_report_jobs(db_path=db_path)
    with odidb.get_conn(db_path) as conn:
        conn.execute("UPDATE practice_attempts SET state='failed', error='분석이 중단되었습니다. 현재 녹음으로 다시 시도해 주세요.' WHERE state IN ('queued','analyzing')")

@asynccontextmanager
async def lifespan(_app: FastAPI):
    initialize_odi_and_recover()
    yield


app = FastAPI(lifespan=lifespan)
from ops_health import install as install_ops_health
install_ops_health(app)
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
