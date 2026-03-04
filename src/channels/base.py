"""
Base channel abstraction – the ABC that all communication channels implement.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChannelMessage:
    """Normalized message flowing through any channel."""

    lsp_id: str
    direction: str  # "outbound" | "inbound"
    text: str
    raw_payload: dict[str, Any] | None = None
    timestamp: float = field(default_factory=time.time)
    channel_type: str = ""  # "simulator" | "whatsapp" | "gmail"


class MessageChannel(ABC):
    """Abstract base class for all communication channels.

    Implementations must handle:
      - Sending a message to an LSP
      - Waiting (potentially for hours) for an inbound reply
      - Lifecycle management (start/stop)
    """

    @abstractmethod
    async def send(
        self,
        lsp_id: str,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Send a message to the LSP.

        Args:
            lsp_id: Target LSP identifier.
            message: The text of the message to send.
            metadata: Channel-specific extras (e.g. offer_price for simulator,
                      subject line for email).
        """

    @abstractmethod
    async def receive(
        self,
        lsp_id: str,
        timeout: float | None = None,
    ) -> ChannelMessage:
        """Wait for the next inbound message from this LSP.

        Blocks until a message arrives or *timeout* seconds elapse.
        Raises ``asyncio.TimeoutError`` if the timeout is hit.
        """

    @abstractmethod
    async def start(self) -> None:
        """Initialize the channel (connect, start pollers, etc.)."""

    @abstractmethod
    async def stop(self) -> None:
        """Graceful shutdown (close connections, cancel tasks)."""
