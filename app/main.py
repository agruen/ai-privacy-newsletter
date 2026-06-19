"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.auth import NotAuthenticated, bootstrap_admin
from app.config import get_settings
from app.db import init_db
from app.scheduler import start_scheduler, stop_scheduler
from app.web import routes_admin, routes_auth

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()
BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    bootstrap_admin()
    start_scheduler()
    logger.info("%s started (%s)", settings.app_name, settings.environment)
    try:
        yield
    finally:
        stop_scheduler()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key)
app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR / "web" / "static")),
    name="static",
)


@app.exception_handler(NotAuthenticated)
async def _not_authenticated(_request: Request, _exc: NotAuthenticated):
    return RedirectResponse("/login", status_code=303)


@app.get("/healthz")
def healthz() -> JSONResponse:
    return JSONResponse({"status": "ok", "app": settings.app_name})


app.include_router(routes_auth.router)
app.include_router(routes_admin.router)
