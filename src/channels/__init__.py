"""
Channel abstraction layer for the Swarm Intelligence Negotiator.

Provides a uniform interface for sending/receiving negotiation messages
across different communication backends (simulator, WhatsApp, Gmail).
"""

from src.channels.base import ChannelMessage, MessageChannel
from src.channels.simulator_channel import SimulatorChannel

__all__ = [
    "ChannelMessage",
    "MessageChannel",
    "SimulatorChannel",
]
