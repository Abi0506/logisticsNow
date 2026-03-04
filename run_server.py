"""
Unified Launcher – starts the FastAPI webhook server and the negotiation
orchestrator in a single process.

Usage:
    python run_server.py --lane Lane_A --config config/lsp_contacts.json
    python run_server.py --lane Lane_A --config config/lsp_contacts.json --use-claude
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("SIN")

DATA_DIR = Path(__file__).parent / "data"


def start_webhook_server(host: str = "0.0.0.0", port: int = 8000) -> None:
    """Run the FastAPI webhook server (blocking, for use in a thread)."""
    import uvicorn
    uvicorn.run("src.webhook_server:app", host=host, port=port, log_level="info")


async def run_negotiation(args: argparse.Namespace) -> None:
    """Initialize channels, session store, and run the orchestrator."""
    from generate_data import BUDGET_PER_LANE, generate_all
    from src.config_loader import get_lsp_metadata, load_config
    from src.orchestrator import Orchestrator
    from src.session_store import SessionStore
    from src.strategy_brain import StrategyBrain
    from src.webhook_server import set_session_store, set_whatsapp_channel

    # Generate data if needed
    if not (DATA_DIR / "historical_bids.csv").exists():
        generate_all()

    # Train model
    logger.info("Training reservation-price model...")
    brain = StrategyBrain()
    brain.load_data()
    brain.train()

    # Load config
    config = load_config(args.config)
    defaults = config.get("defaults", {})
    lsp_metadata = get_lsp_metadata(config)

    # Session store
    store = SessionStore()
    store.initialize()
    set_session_store(store)
    logger.info("Session store initialized")

    # Build channels
    channels: dict = {}
    wa_contacts: dict[str, str] = {}
    gmail_contacts: dict[str, str] = {}

    for contact in config.get("contacts", []):
        lsp_id = contact["lsp_id"]
        if args.lane and args.lane not in contact.get("lane_ids", []):
            continue
        ch_info = contact.get("channels", {})
        pref = contact.get("preferred_channel", "gmail")

        if pref == "whatsapp" and "whatsapp" in ch_info:
            wa_contacts[lsp_id] = ch_info["whatsapp"]
        elif "gmail" in ch_info:
            gmail_contacts[lsp_id] = ch_info["gmail"]
        elif "whatsapp" in ch_info:
            wa_contacts[lsp_id] = ch_info["whatsapp"]

    # Create WhatsApp channel
    if wa_contacts:
        from src.channels.whatsapp_channel import WhatsAppChannel
        wa_cfg = defaults.get("whatsapp", {})
        wa_channel = WhatsAppChannel(
            account_sid=wa_cfg.get("account_sid", ""),
            auth_token=wa_cfg.get("auth_token", ""),
            from_number=wa_cfg.get("from_number", ""),
            lsp_contacts=wa_contacts,
        )
        set_whatsapp_channel(wa_channel)
        for lsp_id in wa_contacts:
            channels[lsp_id] = wa_channel
        logger.info(f"WhatsApp channel: {len(wa_contacts)} LSPs")

    # Create Gmail channel
    if gmail_contacts:
        from src.channels.gmail_channel import GmailChannel
        gm_cfg = defaults.get("gmail", {})
        gm_channel = GmailChannel(
            smtp_server=gm_cfg.get("smtp_server", "smtp.gmail.com"),
            smtp_port=gm_cfg.get("smtp_port", 587),
            imap_server=gm_cfg.get("imap_server", "imap.gmail.com"),
            imap_port=gm_cfg.get("imap_port", 993),
            email_address=gm_cfg.get("email_address", ""),
            app_password=gm_cfg.get("app_password", ""),
            lsp_contacts=gmail_contacts,
            poll_interval=defaults.get("imap_poll_interval_seconds", 30),
        )
        for lsp_id in gmail_contacts:
            channels[lsp_id] = gm_channel
        logger.info(f"Gmail channel: {len(gmail_contacts)} LSPs")

    if not channels:
        logger.error("No channels configured. Check your config and --lane filter.")
        return

    # Filter metadata
    filtered_meta = {k: v for k, v in lsp_metadata.items() if k in channels}

    # Determine lane and budget
    lane_id = args.lane or "Lane_A"
    budget = BUDGET_PER_LANE.get(lane_id, 1200)
    reply_timeout = defaults.get("reply_timeout_seconds", 86400)

    logger.info(f"Starting negotiation: lane={lane_id}, budget={budget}, LSPs={len(channels)}")

    orch = Orchestrator(
        strategy_brain=brain,
        channels=channels,
        lsp_metadata=filtered_meta,
        lane_id=lane_id,
        budget=budget,
        reliability_weight=args.reliability_weight,
        use_claude=args.use_claude,
        session_store=store,
        reply_timeout=reply_timeout,
    )

    try:
        sessions = await orch.run()
        summary = orch.get_results_summary()

        logger.info(f"Negotiation complete: {summary['accepted_deals']}/{summary['total_lsps']} accepted")
        logger.info(f"Total savings: ${summary['total_savings']:,.2f} ({summary['avg_savings_pct']:.1f}%)")

        for detail in summary["details"]:
            status_icon = {"accepted": "+", "rejected": "x", "timeout": "~"}.get(detail["status"], "?")
            price_str = f"${detail['final_price']:,.2f}" if detail["final_price"] else "N/A"
            logger.info(
                f"  [{status_icon}] {detail['lsp_name']} | "
                f"${detail['initial_quote']:,.2f} -> {price_str} | "
                f"{detail.get('channel_type', '?')}"
            )
    finally:
        await orch.shutdown()
        store.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="SIN – Unified Server + Negotiator")
    parser.add_argument("--lane", type=str, default=None, help="Lane to negotiate")
    parser.add_argument("--config", type=str, default="config/lsp_contacts.json", help="Config path")
    parser.add_argument("--use-claude", action="store_true", help="Use Claude API")
    parser.add_argument("--reliability-weight", type=float, default=1.0, help="Reliability weight")
    parser.add_argument("--webhook-port", type=int, default=8000, help="Webhook server port")
    parser.add_argument("--no-webhook", action="store_true", help="Skip webhook server (Gmail-only mode)")
    args = parser.parse_args()

    # Start webhook server in background thread (for WhatsApp)
    if not args.no_webhook:
        logger.info(f"Starting webhook server on port {args.webhook_port}...")
        server_thread = threading.Thread(
            target=start_webhook_server,
            kwargs={"port": args.webhook_port},
            daemon=True,
        )
        server_thread.start()
        logger.info("Webhook server started. Configure Twilio to POST to:")
        logger.info(f"  https://<your-domain>:{args.webhook_port}/webhooks/whatsapp")

    # Run the negotiation
    asyncio.run(run_negotiation(args))


if __name__ == "__main__":
    main()
