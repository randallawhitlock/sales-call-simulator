"""Gets the customer back on a call the worker has left.

The join token dispatches the customer worker only when it creates the room. If the worker
crashes or gives up while the room stays open (the learner is still in it, or left less than
``DEPARTURE_TIMEOUT_S`` ago), rejoining with a new token finds the room and no customer. So the
join endpoint asks LiveKit who's in the room, and sends the worker again when nobody plays the
customer and no dispatch is on its way. The worker picks the call up from the transcript.
"""

import asyncio
import json
import time

from livekit import api

from app.voice.config import AGENT_NAME, VoiceConfig

TIMEOUT_S = 3.0
"""Joining doesn't wait longer than this on LiveKit; the learner can still join without it."""
_LIVE = {api.JobStatus.JS_PENDING, api.JobStatus.JS_RUNNING}
WAITING_S = 10.0
"""A dispatch this young that lists no job may still be on its way: the worker can take a few
seconds to join, and ``livekit-server`` doesn't always list a dispatch's jobs. Past this it
counts as lost, since LiveKit never hands a dispatch to a worker that registers after it."""


def _on_its_way(dispatch: api.AgentDispatch, now_ns: int) -> bool:
    state = dispatch.state
    if any(job.state.status in _LIVE for job in state.jobs):
        return True
    return not state.jobs and now_ns - state.created_at < WAITING_S * 1e9


async def ensure_customer(config: VoiceConfig, session_id: str) -> bool:
    """Dispatches the customer worker to an open call room that has no customer and none on the
    way. Returns whether it did. A room that doesn't exist yet is left to the join token."""
    room = config.room_for(session_id)
    lk = api.LiveKitAPI(config.livekit_url, config.livekit_key, config.livekit_secret)
    try:
        async with asyncio.timeout(TIMEOUT_S):
            try:
                listed = await lk.room.list_participants(api.ListParticipantsRequest(room=room))
            except api.TwirpError as e:
                if e.code == api.TwirpErrorCode.NOT_FOUND:
                    return False
                raise
            if any(p.kind == api.ParticipantInfo.Kind.AGENT for p in listed.participants):
                return False
            dispatches = await lk.agent_dispatch.list_dispatch(room)
            now_ns = time.time_ns()
            if any(_on_its_way(d, now_ns) for d in dispatches if d.agent_name == AGENT_NAME):
                return False
            await lk.agent_dispatch.create_dispatch(
                api.CreateAgentDispatchRequest(
                    agent_name=AGENT_NAME,
                    room=room,
                    metadata=json.dumps({"session_id": session_id}),
                )
            )
            return True
    finally:
        await lk.aclose()
