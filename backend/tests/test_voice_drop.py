"""The drop command, with LiveKit's room stood in for: the real RPC needs a live server."""

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from livekit import api, rtc

from app.voice.config import LEARNER_IDENTITY, VoiceConfig, voice_from_env
from app.voice.drop import OPS_IDENTITY, drop, dropped, main, ops_token
from tests.conftest import VOICE_ENV


def config() -> VoiceConfig:
    out = voice_from_env(VOICE_ENV)
    assert out is not None
    return out


def test_the_ops_token_joins_one_room_to_send_data_with_no_dispatch() -> None:
    cfg = config()
    token = ops_token(cfg, "abc123")
    claims = api.TokenVerifier(cfg.livekit_key, cfg.livekit_secret).verify(token)
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


def test_only_a_reply_that_says_so_counts_as_a_drop() -> None:
    assert dropped('{"dropped": true}')
    for reply in ('{"dropped": false}', '{"dropped": "yes"}', "[]", "", "ok"):
        assert not dropped(reply), reply


class FakeRoom:
    def __init__(self, reply: str = "", *, refuse: bool = False) -> None:
        self.refuse = refuse
        self.disconnected = False
        customer = SimpleNamespace(
            identity="agent-1", kind=rtc.ParticipantKind.PARTICIPANT_KIND_AGENT
        )
        self.remote_participants = {customer.identity: customer}

        async def perform_rpc(**kwargs: Any) -> str:
            return reply

        self.local_participant = SimpleNamespace(perform_rpc=perform_rpc)

    async def connect(self, url: str, token: str) -> None:
        if self.refuse:
            raise rtc.ConnectError("connection refused")

    async def disconnect(self) -> None:
        self.disconnected = True


def test_a_drop_that_did_nothing_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mid-reconnect the customer has no socket to cut. Saying so beats a silent success the
    person only finds out about from the report an hour later."""
    room = FakeRoom('{"dropped": false}')
    monkeypatch.setattr(rtc, "Room", lambda: room)
    with pytest.raises(RuntimeError, match="nothing was dropped"):
        asyncio.run(drop(config(), "abc123"))
    assert room.disconnected

    room = FakeRoom('{"dropped": true}')
    assert asyncio.run(drop(config(), "abc123")) == '{"dropped": true}'


def test_an_unreachable_livekit_is_a_plain_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rtc, "Room", lambda: FakeRoom(refuse=True))
    with pytest.raises(RuntimeError, match="couldn't reach LiveKit at ws://127.0.0.1:7880"):
        asyncio.run(drop(config(), "abc123"))
