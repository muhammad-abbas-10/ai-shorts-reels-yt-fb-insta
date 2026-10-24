from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from shorts.config import PROJECT_ROOT, Settings

from backend.api import jobs, media, system
from backend.runtime import JobRuntime


def create_app(settings: Settings | None = None, *, start_worker: bool = True) -> FastAPI:
    runtime = JobRuntime(settings or Settings.from_env())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if start_worker:
            runtime.start()
        try:
            yield
        finally:
            runtime.close()

    app = FastAPI(title="Shorts Studio API", version="0.2.0", lifespan=lifespan)
    app.state.runtime = runtime
    app.add_middleware(CORSMiddleware,
                       allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
                       allow_methods=["GET", "POST"], allow_headers=["Content-Type"])
    app.include_router(system.router)
    app.include_router(jobs.router)
    app.include_router(media.router)

    dist = PROJECT_ROOT / "frontend" / "dist"
    if dist.is_dir():
        assets = dist / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="frontend-assets")

    @app.get("/favicon.svg", include_in_schema=False)
    def favicon():
        icon = dist / "favicon.svg"
        if not icon.is_file():
            raise HTTPException(status_code=404, detail="Favicon is not available")
        return FileResponse(icon, media_type="image/svg+xml")

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str):
        if path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
        index = dist / "index.html"
        if not index.is_file():
            raise HTTPException(status_code=404, detail="Build the frontend with npm run build")
        return FileResponse(index)

    return app


app = create_app()
