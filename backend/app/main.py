"""FastAPI application: the JSON API under /api, plus the built React app at /."""
import os

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app import config
from app.api.routes import router
from app.db import engine
from app.errors import AppError

app = FastAPI(title="Newtonite Ops", version="1.0.0")
app.include_router(router, prefix="/api")


@app.exception_handler(AppError)
def handle_app_error(_request: Request, err: AppError):
    body = {"code": err.code, "detail": err.detail}
    if err.current is not None:
        body["current"] = err.current
    return JSONResponse(body, status_code=err.status_code)


@app.get("/health", include_in_schema=False)
def health():
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ok"}


# Serve the React build (only when it exists, i.e. inside the Docker image).
if config.FRONTEND_DIST and os.path.isdir(config.FRONTEND_DIST):
    app.mount("/assets", StaticFiles(directory=os.path.join(config.FRONTEND_DIST, "assets")), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            return JSONResponse({"code": "not_found", "detail": "Not found"}, status_code=404)
        # Client-side routing: every page is index.html; React decides what to show.
        return FileResponse(os.path.join(config.FRONTEND_DIST, "index.html"))
