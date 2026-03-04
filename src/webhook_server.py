"""
Webhook Server – FastAPI application that:
  1. Receives Twilio WhatsApp webhooks at POST /webhooks/whatsapp
  2. Exposes a REST API for the Streamlit dashboard to poll session state
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import PlainTextResponse

logger = logging.getLogger(__name__)

app = FastAPI(title="SIN – Swarm Intelligence Negotiator API")

# Global references – set during startup by the launcher
_whatsapp_channel: Any = None
_session_store: Any = None


def set_whatsapp_channel(channel: Any) -> None:
    """Register the WhatsApp channel instance for webhook routing."""
    global _whatsapp_channel
    _whatsapp_channel = channel


def set_session_store(store: Any) -> None:
    """Register the session store for dashboard API."""
    global _session_store
    _session_store = store


# ── Twilio WhatsApp webhook ──

@app.post("/webhooks/whatsapp")
async def whatsapp_webhook(request: Request) -> PlainTextResponse:
    """Receive incoming WhatsApp messages from Twilio.

    Twilio sends form-encoded POST with fields including:
      - From: whatsapp:+919876543210
      - Body: message text
      - MessageSid, AccountSid, etc.
    """
    form_data = await request.form()
    raw = dict(form_data)

    from_phone = raw.get("From", "")
    body = raw.get("Body", "")

    logger.info(f"WhatsApp webhook: From={from_phone}, Body={body[:100]}")

    if _whatsapp_channel:
        routed = _whatsapp_channel.handle_webhook(
            from_phone=from_phone,
            body=body,
            raw=raw,
        )
        if not routed:
            logger.warning(f"Message from {from_phone} not routed to any LSP")

    # Twilio expects a TwiML response; empty Response means no auto-reply
    return PlainTextResponse(
        "<Response></Response>",
        media_type="application/xml",
    )


# ── Dashboard API ──

@app.get("/api/health")
async def health_check() -> dict:
    return {
        "status": "ok",
        "whatsapp_channel": _whatsapp_channel is not None,
        "session_store": _session_store is not None,
    }


@app.get("/api/sessions")
async def get_sessions() -> list[dict]:
    """Return all sessions for the dashboard."""
    if _session_store:
        return _session_store.get_all_sessions_summary()
    return []


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str) -> dict:
    """Return a single session with full detail."""
    if _session_store:
        result = _session_store.load_session(session_id)
        if result:
            result["rounds"] = _session_store.get_rounds(session_id)
            result["messages"] = _session_store.get_messages(session_id)
            return result
    return {"error": "not found"}


@app.get("/api/sessions/{session_id}/rounds")
async def get_session_rounds(session_id: str) -> list[dict]:
    """Return round history for a session."""
    if _session_store:
        return _session_store.get_rounds(session_id)
    return []


@app.get("/api/sessions/{session_id}/messages")
async def get_session_messages(session_id: str) -> list[dict]:
    """Return raw message audit log for a session."""
    if _session_store:
        return _session_store.get_messages(session_id)
    return []
