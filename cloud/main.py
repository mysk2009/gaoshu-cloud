from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from cloud.config import Settings
from cloud.db import Database
from cloud.export_xlsx import router as export_router
from cloud.routes_admin import router as admin_router
from cloud.routes_board import router as board_router
from cloud.routes_sync import router as sync_router
from cloud.routes_teacher import router as teacher_router
from cloud.schema import ensure_schema


STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or Settings.from_env()
    database = Database(resolved_settings.db_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            ensure_schema(database)
            app.state.schema_ready = True
        except Exception:
            # Keep the process reachable so /api/health can report db="down".
            app.state.schema_ready = False
        yield

    app = FastAPI(title="Gaoshu Cloud M2", lifespan=lifespan)
    # 学生端跑在本地（http://127.0.0.1:8020 或局域网 IP），上报要打云端域名 => 必须放行跨源。
    # 鉴权走请求头（X-Class-Secret / Authorization），不用 cookie，所以 allow_origins=["*"] 是安全的。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.settings = resolved_settings
    app.state.database = database
    app.include_router(admin_router)
    app.include_router(sync_router)
    app.include_router(board_router)
    app.include_router(teacher_router)
    app.include_router(export_router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


app = create_app()
