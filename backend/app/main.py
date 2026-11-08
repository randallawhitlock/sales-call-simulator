"""FastAPI entry point. Run with ``uv run uvicorn app.main:app --reload``."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app import __version__
from app.api import router
from app.content.loader import load_bundle
from app.engine.session import SessionError, SessionService
from app.engine.store import Store

DB_PATH_ENV = "SIM_DB_PATH"


class Health(BaseModel):
    status: str
    version: str
    ruleset_version: str
    rules: int
    catalog_version: str
    scenarios: int


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Load and cross-check content at startup so a bad file stops the server, not a call.
    content = load_bundle()
    store = Store(os.environ.get(DB_PATH_ENV, "simulator.db"))
    app.state.sessions = SessionService(store, content)
    yield
    store.close()


app = FastAPI(title="Sales Call Simulator", version=__version__, lifespan=lifespan)
app.include_router(router)


@app.exception_handler(SessionError)
async def session_error(request: Request, exc: SessionError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})


@app.get("/api/health")
def health(request: Request) -> Health:
    content = request.app.state.sessions.content
    return Health(
        status="ok",
        version=__version__,
        ruleset_version=content.ruleset.ruleset_version,
        rules=len(content.ruleset.rules),
        catalog_version=content.catalog.catalog_version,
        scenarios=len(content.scenarios),
    )
