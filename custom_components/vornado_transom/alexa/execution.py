"""Send planned writes without letting an older poll replace them."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from .commands import FeatureWrite, Intent, expected_states, plan
from .endpoints import apply_fan_state
from .models import AlexaApiError, FanDevice

type ReadDevice = Callable[[], FanDevice]
type PublishDevice = Callable[[FanDevice], None]
type SendWrite = Callable[[FeatureWrite], Awaitable[None]]


class CommandGate:
    """One lock for every fan command and for publishing a poll."""

    def __init__(self) -> None:
        """Initialize the gate."""
        self._lock = asyncio.Lock()
        self._epochs: dict[str, int] = {}

    def epoch(self, appliance_key: str) -> int:
        """Return how many times this fan has published a command."""
        return self._epochs.get(appliance_key, 0)

    async def run(
        self,
        appliance_key: str,
        read: ReadDevice,
        intent: Intent,
        send: SendWrite,
        publish: PublishDevice,
        failures: tuple[type[BaseException], ...] = (AlexaApiError,),
    ) -> None:
        """Plan, send, and publish one fan. The lock is held across every write."""
        async with self._lock:
            writes = plan(read(), intent)
            if not writes:
                return
            succeeded: list[FeatureWrite] = []
            for write in writes:
                try:
                    await send(write)
                except failures:
                    if succeeded:
                        publish(apply_fan_state(read(), expected_states(succeeded)))
                        self._bump(appliance_key)
                    raise
                succeeded.append(write)
            publish(apply_fan_state(read(), expected_states(succeeded)))
            self._bump(appliance_key)

    async def merge_poll(
        self,
        started: dict[str, int],
        proposed: dict[str, FanDevice],
        current: Callable[[str], FanDevice | None],
    ) -> dict[str, FanDevice]:
        """Keep a command that published after its poll snapshot was started."""
        async with self._lock:
            merged: dict[str, FanDevice] = {}
            for key, device in proposed.items():
                if self.epoch(key) != started.get(key, 0):
                    kept = current(key)
                    merged[key] = device if kept is None else kept
                else:
                    merged[key] = device
            return merged

    def _bump(self, appliance_key: str) -> None:
        self._epochs[appliance_key] = self.epoch(appliance_key) + 1
