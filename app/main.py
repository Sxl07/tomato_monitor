from __future__ import annotations

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.routes.ui import router as ui_router
from app.routes.pipeline import router as pipeline_router
from app.routes.sessions import router as sessions_router


app = FastAPI(
    title="Tomato Monitor",
    description="Mini app de pruebas para visión por computador en tomate cherry",
    version="0.1.0",
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(ui_router)
app.include_router(pipeline_router, prefix="/pipeline", tags=["pipeline"])
app.include_router(sessions_router, prefix="/sessions", tags=["sessions"])