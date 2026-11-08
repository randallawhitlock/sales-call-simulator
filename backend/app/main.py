"""FastAPI entry point. Run with ``uv run uvicorn app.main:app --reload``."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel

from app import __version__
from app.content.loader import load_call_frame, load_ruleset


class Health(BaseModel):
    status: str
    version: str
    ruleset_version: str
    rules: int


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Load content at startup so a bad content file stops the server instead of a call.
    app.state.ruleset = load_ruleset()
    app.state.call_frame = load_call_frame()
    yield


app = FastAPI(title="Sales Call Simulator", version=__version__, lifespan=lifespan)


@app.get("/api/health")
def health() -> Health:
    ruleset = app.state.ruleset
    return Health(
        status="ok",
        version=__version__,
        ruleset_version=ruleset.ruleset_version,
        rules=len(ruleset.rules),
    )
