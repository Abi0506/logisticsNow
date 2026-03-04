"""
Gmail Channel – sends emails via SMTP and receives replies via IMAP polling.

Uses app password authentication (no OAuth required).
Background IMAP poller runs every N seconds checking for new emails from LSPs.
"""

from __future__ import annotations

import asyncio
import email
import email.utils
import imaplib
import logging
import smtplib
import time
from email.mime.text import MIMEText
from typing import Any

from src.channels.base import ChannelMessage, MessageChannel

logger = logging.getLogger(__name__)


class GmailChannel(MessageChannel):
    """Channel implementation for email-based negotiation via SMTP/IMAP."""

    def __init__(
        self,
        smtp_server: str,
        smtp_port: int,
        imap_server: str,
        imap_port: int,
        email_address: str,
        app_password: str,
        lsp_contacts: dict[str, str],
        poll_interval: float = 30.0,
    ) -> None:
        """
        Args:
            lsp_contacts: Mapping of lsp_id -> email address.
            poll_interval: Seconds between IMAP inbox checks.
        """
        self._smtp_server = smtp_server
        self._smtp_port = smtp_port
        self._imap_server = imap_server
        self._imap_port = imap_port
        self._email = email_address
        self._password = app_password
        self._contacts = lsp_contacts
        self._email_to_lsp: dict[str, str] = {}
        self._queues: dict[str, asyncio.Queue[ChannelMessage]] = {}
        self._poll_interval = poll_interval
        self._poller_task: asyncio.Task | None = None
        self._last_seen_uid: int = 0
        self._running = False

    async def start(self) -> None:
        for lsp_id, addr in self._contacts.items():
            self._queues[lsp_id] = asyncio.Queue()
            self._email_to_lsp[addr.lower().strip()] = lsp_id

        # Snapshot current max UID so we skip existing emails
        loop = asyncio.get_event_loop()
        self._last_seen_uid = await loop.run_in_executor(None, self._get_max_uid)

        self._running = True
        self._poller_task = asyncio.create_task(self._poll_loop())
        logger.info(
            f"GmailChannel started: monitoring {len(self._contacts)} LSP addresses, "
            f"poll every {self._poll_interval}s"
        )

    async def send(
        self,
        lsp_id: str,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        to_email = self._contacts[lsp_id]
        subject = (metadata or {}).get(
            "subject", f"[SIN] Logistics Rate Negotiation – {lsp_id}"
        )
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, self._send_email_sync, to_email, subject, message
        )
        logger.info(f"Email sent to {to_email} ({lsp_id})")

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
        self._running = False
        if self._poller_task:
            self._poller_task.cancel()
            try:
                await self._poller_task
            except asyncio.CancelledError:
                pass
        logger.info("GmailChannel stopped")

    # ── synchronous helpers (run in executor) ──

    def _send_email_sync(self, to_email: str, subject: str, body: str) -> None:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = self._email
        msg["To"] = to_email

        with smtplib.SMTP(self._smtp_server, self._smtp_port) as server:
            server.starttls()
            server.login(self._email, self._password)
            server.send_message(msg)

    def _get_max_uid(self) -> int:
        """Return the highest UID in the inbox (used for watermarking)."""
        try:
            conn = imaplib.IMAP4_SSL(self._imap_server, self._imap_port)
            conn.login(self._email, self._password)
            conn.select("INBOX")
            status, data = conn.uid("search", None, "ALL")
            conn.logout()
            if status == "OK" and data[0]:
                uids = data[0].split()
                return int(uids[-1]) if uids else 0
        except Exception as exc:
            logger.warning(f"Could not get max UID: {exc}")
        return 0

    def _fetch_new_emails_sync(self) -> list[tuple[str, str, str, int]]:
        """Fetch emails with UID > last_seen_uid.

        Returns list of (sender_email, subject, body_text, uid).
        """
        results: list[tuple[str, str, str, int]] = []
        try:
            conn = imaplib.IMAP4_SSL(self._imap_server, self._imap_port)
            conn.login(self._email, self._password)
            conn.select("INBOX")

            search_criteria = f"(UID {self._last_seen_uid + 1}:*)"
            status, data = conn.uid("search", None, search_criteria)

            if status == "OK" and data[0]:
                uid_list = data[0].split()
                for uid_bytes in uid_list:
                    uid = int(uid_bytes)
                    if uid <= self._last_seen_uid:
                        continue

                    status2, msg_data = conn.uid("fetch", uid_bytes, "(RFC822)")
                    if status2 != "OK" or not msg_data[0]:
                        continue

                    raw_email = msg_data[0][1]
                    parsed = email.message_from_bytes(raw_email)
                    sender = email.utils.parseaddr(parsed.get("From", ""))[1]
                    subject = parsed.get("Subject", "")

                    body = ""
                    if parsed.is_multipart():
                        for part in parsed.walk():
                            if part.get_content_type() == "text/plain":
                                payload = part.get_payload(decode=True)
                                if payload:
                                    body = payload.decode("utf-8", errors="replace")
                                break
                    else:
                        payload = parsed.get_payload(decode=True)
                        if payload:
                            body = payload.decode("utf-8", errors="replace")

                    results.append((sender, subject, body, uid))

            conn.logout()
        except Exception as exc:
            logger.error(f"IMAP fetch error: {exc}")

        return results

    # ── background poller ──

    async def _poll_loop(self) -> None:
        """Background coroutine: poll IMAP for new emails and route to queues."""
        while self._running:
            try:
                loop = asyncio.get_event_loop()
                new_emails = await loop.run_in_executor(
                    None, self._fetch_new_emails_sync
                )

                for sender, subject, body, uid in new_emails:
                    sender_clean = sender.lower().strip()
                    lsp_id = self._email_to_lsp.get(sender_clean)

                    if lsp_id and lsp_id in self._queues:
                        msg = ChannelMessage(
                            lsp_id=lsp_id,
                            direction="inbound",
                            text=body,
                            raw_payload={
                                "sender": sender,
                                "subject": subject,
                                "uid": uid,
                            },
                            timestamp=time.time(),
                            channel_type="gmail",
                        )
                        self._queues[lsp_id].put_nowait(msg)
                        logger.info(f"Email received from {sender} -> {lsp_id}")

                    if uid > self._last_seen_uid:
                        self._last_seen_uid = uid

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"IMAP poll error: {exc}")

            await asyncio.sleep(self._poll_interval)
