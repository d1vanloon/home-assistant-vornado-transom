"""Send one fan's planned writes without interleaving another command."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from .commands import FeatureWrite, Intent, expected_states, plan
from .endpoints import apply_fan_state
from .models import AlexaApiError, FanDevice


class FanCommandLane:
    """Plan, send, and publish for one fan, one command at a time."""

    def __init__(self) -> None:
        """Initialize the lane."""
        self._lock = asyncio.Lock()

    async def run(
        self,
        read: Callable[[], FanDevice],
        intent: Intent,
        send: Callable[[FeatureWrite], Awaitable[None]],
        publish: Callable[[FanDevice], None],
    ) -> None:
        """Run the intent against the device read inside this lane's lock."""
        async with self._lock:
            device = read()
            writes = plan(device, intent)
            if not writes:
                return
            succeeded: list[FeatureWrite] = []
            for write in writes:
                try:
                    await send(write)
                except AlexaApiError:
                    if succeeded:
                        publish(apply_fan_state(device, expected_states(succeeded)))
                    raise
                succeeded.append(write)
            publish(apply_fan_state(device, expected_states(succeeded)))
