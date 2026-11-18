"""The backend: the SQLite database, the API for the Web App (/api), the API for the bot (/internal),
and the Web App's files. Run with:  uvicorn backend.main:app
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from backend import db, jobs
from backend.api import internal, me, payments, users
from backend.config import get_settings
from backend.errors import AppError
from backend.services import outbox

log = logging.getLogger(__name__)
WEBAPP_DIR = Path(__file__).parent / "webapp"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.check()
    db.init_engine(settings.database_url)
    await db.create_tables()
    if not settings.bot_token:
        log.warning("BOT_TOKEN is not set: the Web App can't verify who is opening it")
    if not settings.doppi_configured:
        log.warning("DOPPI_* is not set: recording data is not synced")
    if not settings.mail_configured:
        log.warning("SMTP_* is not set: verification codes can't be emailed")
    tasks = jobs.start(settings) if settings.run_jobs else []
    yield
    for task in tasks:
        task.cancel()
    await db.dispose()


def create_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # Request lines carry no personal data, but httpx would log full UseMinute URLs with the login key
    logging.getLogger("httpx").setLevel(logging.WARNING)
    app = FastAPI(title="UseMinute payments backend", version="1.0.0", lifespan=lifespan)

    @app.exception_handler(AppError)
    async def app_error(_: Request, exc: AppError):
        return JSONResponse({"detail": exc.detail()}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, exc: RequestValidationError):
        # Never echo what was sent: it may be a name, a phone number or a code (NFR-3)
        fields = sorted({str(e["loc"][-1]) for e in exc.errors()})
        detail = {"code": "invalid_request", "message": "Check what you entered and try again", "params": {"fields": fields}}
        return JSONResponse({"detail": detail}, status_code=422)

    @app.middleware("http")
    async def after_request(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/webapp"):
            # Revalidate Web App files on every load (cheap: 304 via ETag) so updates reach phones
            response.headers["Cache-Control"] = "no-cache"
        elif request.method not in ("GET", "HEAD") and not request.url.path.startswith("/internal/outbox"):
            outbox.wake()  # a change may have queued messages: let the bot collect them now
        return response

    @app.get("/health", include_in_schema=False)
    async def health():
        return {"status": "ok"}

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse("/webapp/")

    @app.get("/webapp/v/{version}/{path:path}", include_in_schema=False)
    async def versioned_webapp(version: str, path: str = ""):
        """The Web App's files under a per-release path (see Settings.webapp_url)."""
        target = (WEBAPP_DIR / (path or "index.html")).resolve()
        if not target.is_relative_to(WEBAPP_DIR.resolve()) or not target.is_file():
            raise HTTPException(404)
        return FileResponse(target)

    app.include_router(me.router)
    app.include_router(payments.router)
    app.include_router(users.router)
    app.include_router(internal.router)
    if WEBAPP_DIR.is_dir():
        app.mount("/webapp", StaticFiles(directory=WEBAPP_DIR, html=True), name="webapp")
    return app


app = create_app()
