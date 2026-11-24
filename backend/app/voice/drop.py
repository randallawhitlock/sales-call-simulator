"""Forces a Gemini drop on a live voice call, for an acceptance run on a real mic.

    uv run python -m app.voice.drop <session_id>

Needs the same voice settings as the API, and a worker started with SIM_VOICE_DEBUG_DROPS=1.
It joins the call's room as a "sim-ops" participant, so the learner's browser is left alone,
asks the customer to cut its Gemini connection, and leaves. The worker and the call screen
ignore a participant that is neither the learner nor the customer.
"""

import asyncio
import json
import sys
from datetime import timedelta

from livekit import api, rtc

from app.voice.config import (
    DEBUG_DROPS_ENV,
    DROP_MODEL_RPC,
    VoiceConfig,
    VoiceConfigError,
    voice_from_env,
)

OPS_IDENTITY = "sim-ops"
TOKEN_TTL = timedelta(minutes=5)
RPC_TIMEOUT_S = 10.0


def ops_token(config: VoiceConfig, session_id: str) -> str:
    """Joins the call's room and can only send data, which is what LiveKit carries an RPC over.
    It can't be hidden: a hidden caller never gets the reply. There is no agent dispatch."""
    return (
        api.AccessToken(config.livekit_key, config.livekit_secret)
        .with_identity(OPS_IDENTITY)
        .with_ttl(TOKEN_TTL)
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=config.room_for(session_id),
                can_publish=False,
                can_subscribe=False,
                can_publish_data=True,
            )
        )
        .to_jwt()
    )


def dropped(reply: str) -> bool:
    """Whether the worker's reply says it cut the connection. It can't while the customer is
    between connections, during a reconnect, say."""
    try:
        data = json.loads(reply)
    except ValueError:
        return False
    return isinstance(data, dict) and data.get("dropped") is True


async def drop(config: VoiceConfig, session_id: str) -> str:
    """The worker's reply. Raises ``RuntimeError`` with what to tell the person running it."""
    room = rtc.Room()
    try:
        await room.connect(config.livekit_url, ops_token(config, session_id))
    except rtc.ConnectError as e:
        raise RuntimeError(f"couldn't reach LiveKit at {config.livekit_url}: {e}") from e
    try:
        customer = next(
            (
                p
                for p in room.remote_participants.values()
                if p.kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT
            ),
            None,
        )
        if customer is None:
            raise RuntimeError(f"no customer in room {config.room_for(session_id)}")
        try:
            reply = await room.local_participant.perform_rpc(
                destination_identity=customer.identity,
                method=DROP_MODEL_RPC,
                payload="",
                response_timeout=RPC_TIMEOUT_S,
            )
        except rtc.RpcError as e:
            if e.code == rtc.RpcError.ErrorCode.UNSUPPORTED_METHOD:
                raise RuntimeError(
                    f"the worker doesn't take drops; start it with {DEBUG_DROPS_ENV}=1"
                ) from e
            raise RuntimeError(f"the drop failed: {e.message} (code {e.code})") from e
        if not dropped(reply):
            raise RuntimeError(
                f"nothing was dropped: the customer had no open model connection ({reply}). "
                "Try again in a few seconds."
            )
        return reply
    finally:
        await room.disconnect()


def main(argv: list[str]) -> int:
    """Runs one drop. Exits 0 when it happened, 1 when it didn't, and 2 on bad usage."""
    if len(argv) != 1:
        print("usage: python -m app.voice.drop <session_id>", file=sys.stderr)
        return 2
    try:
        config = voice_from_env()
    except VoiceConfigError as e:
        print(f"voice settings are incomplete: {e}", file=sys.stderr)
        return 1
    if config is None:
        print("voice is off; set the same voice settings the API uses", file=sys.stderr)
        return 1
    try:
        print(asyncio.run(drop(config, argv[0])))
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
