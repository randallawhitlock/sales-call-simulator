"""Load and validate content files from ``content/``."""

import os
from pathlib import Path

from pydantic import BaseModel

from app.content.rules import RuleSet
from app.content.scenario import CallFrame

CONTENT_DIR_ENV = "SIM_CONTENT_DIR"
MVP_RULESET = Path("rules/mvp-2027.json")
MVP_FRAME = Path("scenarios/frames/aep-2027.json")


def content_root() -> Path:
    override = os.environ.get(CONTENT_DIR_ENV)
    return Path(override) if override else Path(__file__).resolve().parents[3] / "content"


def load_model[T: BaseModel](path: Path, model: type[T]) -> T:
    return model.model_validate_json(path.read_bytes())


def load_ruleset(root: Path | None = None) -> RuleSet:
    return load_model((root or content_root()) / MVP_RULESET, RuleSet)


def load_call_frame(root: Path | None = None) -> CallFrame:
    return load_model((root or content_root()) / MVP_FRAME, CallFrame)
