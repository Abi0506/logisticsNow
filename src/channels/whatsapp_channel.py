"""
WhatsApp Channel – sends messages via Twilio REST API and receives replies
via a webhook handler that routes incoming messages to per-LSP asyncio queues.

The webhook endpoint itself is served by the FastAPI server in webhook_server.py.
This module provides the channel logic and the handle_webhook() entry point.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from src.channels.base import ChannelMessage, MessageChannel

logger = logging.getLogger(__name__)

try:
    from twilio.rest import Client as TwilioClient
    _TWILIO_AVAILABLE = True
except ImportError:
    _TWILIO_AVAILABLE = False


class WhatsAppChannel(MessageChannel):
    """Channel implementation for WhatsApp via Twilio."""

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        from_number: str,
        lsp_contacts: dict[str, str],
    ) -> None:
        """
        Args:
            account_sid: Twilio Account SID.
            auth_token: Twilio Auth Token.
            from_number: Twilio WhatsApp sender number (e.g. "+14155238886").
            lsp_contacts: Mapping of lsp_id -> phone number (e.g. "+919876543210").
        """
        if not _TWILIO_AVAILABLE:
            raise ImportError("twilio package is not installed. Run: pip install twilio")

        self._client = TwilioClient(account_sid, auth_token)
        self._from = f"whatsapp:{from_number}"
        self._contacts = lsp_contacts
        self._phone_to_lsp: dict[str, str] = {}
        self._queues: dict[str, asyncio.Queue[ChannelMessage]] = {}

    async def start(self) -> None:
        for lsp_id, phone in self._contacts.items():
            self._queues[lsp_id] = asyncio.Queue()
            # Store both raw and whatsapp: prefixed for flexible lookup
            clean = phone.replace("whatsapp:", "").strip()
            self._phone_to_lsp[clean] = lsp_id
            self._phone_to_lsp[f"whatsapp:{clean}"] = lsp_id

        logger.info(
            f"WhatsAppChannel started: {len(self._contacts)} LSP contacts registered"
        )

    async def send(
        self,
        lsp_id: str,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        phone = self._contacts[lsp_id]
        to_whatsapp = f"whatsapp:{phone}" if not phone.startswith("whatsapp:") else phone

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, self._send_sync, to_whatsapp, message
        )
        logger.info(f"WhatsApp sent to {phone} ({lsp_id})")

    def _send_sync(self, to_number: str, body: str) -> None:
        self._client.messages.create(
            body=body,
            from_=self._from,
            to=to_number,
        )

    async def receive(
        self,
        lsp_id: str,
        timeout: float | None = None,
    ) -> ChannelMessage:
        return await asyncio.wait_for(
            self._queues[lsp_id].get(),
            timeout=timeout,
        )

    def handle_webhook(self, from_phone: str, body: str, raw: dict[str, Any]) -> bool:
        """Route an incoming Twilio webhook message to the correct LSP queue.

        Called synchronously from the FastAPI endpoint.

        Args:
            from_phone: The "From" field from Twilio (e.g. "whatsapp:+919876543210").
            body: The message body text.
            raw: Full form data from the webhook.

        Returns:
            True if the message was routed to a known LSP, False otherwise.
        """
        clean = from_phone.replace("whatsapp:", "").strip()
        lsp_id = self._phone_to_lsp.get(clean) or self._phone_to_lsp.get(from_phone)

        if not lsp_id or lsp_id not in self._queues:
            logger.warning(f"Unrecognized WhatsApp sender: {from_phone}")
            return False

        msg = ChannelMessage(
            lsp_id=lsp_id,
            direction="inbound",
            text=body,
            raw_payload=raw,
            timestamp=time.time(),
            channel_type="whatsapp",
        )
        self._queues[lsp_id].put_nowait(msg)
        logger.info(f"WhatsApp received from {from_phone} -> {lsp_id}")
        return True

    async def stop(self) -> None:
        logger.info("WhatsAppChannel stopped")
