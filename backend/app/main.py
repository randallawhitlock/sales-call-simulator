"""FastAPI entry point. Run with ``uv run uvicorn app.main:app --reload``."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app import __version__
from app.actor.actors import actor_from_env, actor_timeout_from_env
from app.actor.prompt import PROMPT_VERSION, check_all_prompts
from app.api import router
from app.content.loader import ContentError, load_bundle
from app.engine.session import SessionError, SessionService
from app.engine.store import Store
from app.judge.judges import judge_from_env
from app.judge.prompt import JUDGE_PROMPT_VERSION

DB_PATH_ENV = "SIM_DB_PATH"


class Health(BaseModel):
    status: str
    version: str
    ruleset_version: str
    rules: int
    catalog_version: str
    scenarios: int
    actor: str
    """The model playing the customer. ``scripted`` is the offline stand-in."""
    prompt_version: str
    judge: str | None
    """The model that grades conversation. None: only the rule checks grade."""
    judge_prompt_version: str


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Load and cross-check content at startup so a bad file stops the server, not a call.
    content = load_bundle()
    # The customer prompt's leak guard runs for every case and mood before any call can start.
    problems = check_all_prompts(content)
    if problems:
        raise ContentError(problems)
    actor = actor_from_env()
    judge = judge_from_env()
    store = Store(os.environ.get(DB_PATH_ENV, "simulator.db"))
    app.state.sessions = SessionService(
        store, content, actor=actor, actor_deadline_s=actor_timeout_from_env(), judge=judge
    )
    yield
    store.close()


app = FastAPI(title="Sales Call Simulator", version=__version__, lifespan=lifespan)
app.include_router(router)


@app.exception_handler(SessionError)
async def session_error(request: Request, exc: SessionError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})


@app.get("/api/health")
def health(request: Request) -> Health:
    service: SessionService = request.app.state.sessions
    content = service.content
    return Health(
        status="ok",
        version=__version__,
        ruleset_version=content.ruleset.ruleset_version,
        rules=len(content.ruleset.rules),
        catalog_version=content.catalog.catalog_version,
        scenarios=len(content.scenarios),
        actor=service.actor.name,
        prompt_version=PROMPT_VERSION,
        judge=service.judge.name if service.judge else None,
        judge_prompt_version=JUDGE_PROMPT_VERSION,
    )
