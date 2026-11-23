"""Join tokens for the learner's browser.

The token lets the browser into its call's room only, and asks LiveKit to dispatch the customer
worker when the room is created. The browser never gets a model key or the customer's prompt.
"""

import json
from datetime import timedelta

from livekit import api

from app.voice.config import AGENT_NAME, LEARNER_IDENTITY, VoiceConfig

TOKEN_TTL = timedelta(hours=2)
EMPTY_TIMEOUT_S = 300
"""A room nobody has joined closes after this long."""
DEPARTURE_TIMEOUT_S = 120
"""How long the room outlives the learner leaving, so a tab reload rejoins the same room and the
same customer session instead of a new one."""


def learner_token(config: VoiceConfig, session_id: str) -> str:
    room = config.room_for(session_id)
    return (
        api.AccessToken(config.livekit_key, config.livekit_secret)
        .with_identity(LEARNER_IDENTITY)
        .with_name("Agent")
        .with_ttl(TOKEN_TTL)
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            )
        )
        .with_room_config(
            api.RoomConfiguration(
                empty_timeout=EMPTY_TIMEOUT_S,
                departure_timeout=DEPARTURE_TIMEOUT_S,
                agents=[
                    api.RoomAgentDispatch(
                        agent_name=AGENT_NAME, metadata=json.dumps({"session_id": session_id})
                    )
                ],
            )
        )
        .to_jwt()
    )
