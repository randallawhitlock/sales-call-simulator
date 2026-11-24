"""The drop command's token. The room and RPC need a live LiveKit, so they aren't run here."""

import pytest
from livekit import api

from app.voice.config import LEARNER_IDENTITY, voice_from_env
from app.voice.drop import OPS_IDENTITY, main, ops_token
from tests.conftest import VOICE_ENV


def test_the_ops_token_joins_one_room_to_send_data_with_no_dispatch() -> None:
    config = voice_from_env(VOICE_ENV)
    assert config is not None
    token = ops_token(config, "abc123")
    claims = api.TokenVerifier(config.livekit_key, config.livekit_secret).verify(token)
    assert claims.identity == OPS_IDENTITY != LEARNER_IDENTITY
    grants = claims.video
    assert grants is not None
    assert (grants.room, grants.room_join) == ("call-abc123", True)
    assert not grants.hidden, "a hidden caller never gets the RPC reply"
    assert not (grants.can_publish or grants.can_subscribe or grants.room_admin)
    assert grants.can_publish_data
    assert claims.room_config is None or not claims.room_config.agents


def test_the_command_refuses_without_voice_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in VOICE_ENV:
        monkeypatch.delenv(name, raising=False)
    assert main(["abc123"]) == 1
    assert main([]) == 2
