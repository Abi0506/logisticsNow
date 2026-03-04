"""
Simulator Channel – wraps the existing LSPSimulator behind the MessageChannel
interface for full backwards compatibility.

send() calls sim.respond() immediately and queues the result.
receive() pops from the queue (instant in simulator mode).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from src.channels.base import ChannelMessage, MessageChannel
from src.lsp_simulator import LSPSimulator


class SimulatorChannel(MessageChannel):
    """Channel implementation backed by local LSPSimulators."""

    def __init__(self, simulators: dict[str, LSPSimulator]) -> None:
        self._simulators = simulators
        self._queues: dict[str, asyncio.Queue[ChannelMessage]] = {}

    async def start(self) -> None:
        for lsp_id in self._simulators:
            self._queues[lsp_id] = asyncio.Queue()

    async def send(
        self,
        lsp_id: str,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        sim = self._simulators[lsp_id]
        offer_price = (metadata or {}).get("offer_price", 0.0)

        result = sim.respond(offer=offer_price)

        reply = ChannelMessage(
            lsp_id=lsp_id,
            direction="inbound",
            text=result["message"],
            raw_payload=result,
            timestamp=time.time(),
            channel_type="simulator",
        )
        await self._queues[lsp_id].put(reply)

    async def receive(
        self,
        lsp_id: str,
        timeout: float | None = None,
    ) -> ChannelMessage:
        return await asyncio.wait_for(
            self._queues[lsp_id].get(),
            timeout=timeout,
        )

    async def stop(self) -> None:
        pass
