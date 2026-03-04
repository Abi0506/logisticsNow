"""
Main Entry Point – initializes data, trains models, runs the full negotiation
pipeline, and outputs results.

Usage:
    python run_negotiation.py                                  # simulator, all lanes
    python run_negotiation.py --lane Lane_A                    # single lane
    python run_negotiation.py --use-claude                     # Claude API for messaging
    python run_negotiation.py --channel whatsapp --config config/lsp_contacts.json
    python run_negotiation.py --channel gmail --config config/lsp_contacts.json
    python run_negotiation.py --channel mixed --config config/lsp_contacts.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from generate_data import (
    BUDGET_PER_LANE,
    LANE_BASE_PRICES,
    LANES,
    LSP_PROFILES,
    generate_all,
)
from src.lsp_simulator import create_simulators_from_profiles
from src.orchestrator import Orchestrator
from src.strategy_brain import StrategyBrain

DATA_DIR = Path(__file__).parent / "data"
LOGS_DIR = Path(__file__).parent / "logs"


def _build_channels_from_config(
    config: dict, lane_id: str
) -> tuple[dict, dict]:
    """Build channel instances and LSP metadata from config for a given lane.

    Returns (channels_dict, lsp_metadata_dict).
    """
    from src.config_loader import get_lsp_metadata

    defaults = config.get("defaults", {})
    contacts = config.get("contacts", [])
    lsp_metadata = get_lsp_metadata(config)

    # Filter to LSPs serving this lane
    lane_contacts = [
        c for c in contacts if lane_id in c.get("lane_ids", [])
    ]

    if not lane_contacts:
        print(f"  No LSPs configured for {lane_id} in config, using all.")
        lane_contacts = contacts

    channels: dict = {}
    wa_contacts: dict[str, str] = {}
    gmail_contacts: dict[str, str] = {}

    for c in lane_contacts:
        pref = c.get("preferred_channel", "simulator")
        lsp_id = c["lsp_id"]
        ch_info = c.get("channels", {})

        if pref == "whatsapp" and "whatsapp" in ch_info:
            wa_contacts[lsp_id] = ch_info["whatsapp"]
        elif pref == "gmail" and "gmail" in ch_info:
            gmail_contacts[lsp_id] = ch_info["gmail"]
        elif "whatsapp" in ch_info:
            wa_contacts[lsp_id] = ch_info["whatsapp"]
        elif "gmail" in ch_info:
            gmail_contacts[lsp_id] = ch_info["gmail"]

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
        for lsp_id in wa_contacts:
            channels[lsp_id] = wa_channel

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

    # Filter metadata to only LSPs with channels
    filtered_meta = {k: v for k, v in lsp_metadata.items() if k in channels}

    return channels, filtered_meta


def main() -> None:
    parser = argparse.ArgumentParser(description="Swarm Intelligence Negotiator – run negotiations")
    parser.add_argument("--lane", type=str, default=None, help="Negotiate a single lane (e.g. Lane_A)")
    parser.add_argument("--use-claude", action="store_true", help="Use Claude API for tactical messaging")
    parser.add_argument("--reliability-weight", type=float, default=1.0, help="Reliability premium weight (0-2)")
    parser.add_argument("--max-lsps", type=int, default=10, help="Max number of LSPs to negotiate with")
    parser.add_argument(
        "--channel",
        choices=["simulator", "whatsapp", "gmail", "mixed"],
        default="simulator",
        help="Communication channel (default: simulator)",
    )
    parser.add_argument("--config", type=str, default="config/lsp_contacts.json", help="Path to LSP contacts config")
    parser.add_argument("--reply-timeout", type=float, default=300, help="Seconds to wait for LSP reply (real channels)")
    parser.add_argument("--persist", action="store_true", help="Enable SQLite session persistence")
    args = parser.parse_args()

    # ── Step 1: Generate data if needed ──
    if not (DATA_DIR / "historical_bids.csv").exists():
        print("[1/4] Generating synthetic data...")
        generate_all()
    else:
        print("[1/4] Synthetic data already exists.")

    # ── Step 2: Train the reservation-price model ──
    print("[2/4] Training reservation-price prediction model...")
    brain = StrategyBrain()
    brain.load_data()
    brain.train()

    # ── Optional: session store ──
    session_store = None
    if args.persist or args.channel != "simulator":
        from src.session_store import SessionStore
        session_store = SessionStore()
        session_store.initialize()
        print("  Session persistence enabled (SQLite)")

    # ── Optional: load config for real channels ──
    config = None
    if args.channel != "simulator":
        from src.config_loader import load_config
        config = load_config(args.config)
        print(f"  Loaded config from {args.config}")
        reply_timeout = config.get("defaults", {}).get(
            "reply_timeout_seconds", args.reply_timeout
        )
    else:
        reply_timeout = args.reply_timeout

    # ── Step 3: Run negotiations ──
    lanes_to_negotiate = [args.lane] if args.lane else LANES
    profiles = LSP_PROFILES[: args.max_lsps]

    all_summaries: list[dict] = []
    total_start = time.time()

    for lane_id in lanes_to_negotiate:
        budget = BUDGET_PER_LANE[lane_id]

        if args.channel == "simulator":
            # Legacy simulator mode
            simulators = create_simulators_from_profiles(profiles, LANE_BASE_PRICES, lane_id)
            print(f"\n[3/4] Negotiating {lane_id} | Budget: ${budget:,.0f} | LSPs: {len(simulators)} | Channel: simulator")
            print("-" * 60)

            orch = Orchestrator(
                strategy_brain=brain,
                simulators=simulators,
                lane_id=lane_id,
                budget=budget,
                reliability_weight=args.reliability_weight,
                use_claude=args.use_claude,
                session_store=session_store,
                reply_timeout=reply_timeout,
            )
        else:
            # Real channel mode
            channels, lsp_metadata = _build_channels_from_config(config, lane_id)
            if not channels:
                print(f"\n  Skipping {lane_id}: no LSPs with configured channels.")
                continue

            print(f"\n[3/4] Negotiating {lane_id} | Budget: ${budget:,.0f} | LSPs: {len(channels)} | Channel: {args.channel}")
            print("-" * 60)

            orch = Orchestrator(
                strategy_brain=brain,
                channels=channels,
                lsp_metadata=lsp_metadata,
                lane_id=lane_id,
                budget=budget,
                reliability_weight=args.reliability_weight,
                use_claude=args.use_claude,
                session_store=session_store,
                reply_timeout=reply_timeout,
            )

        lane_start = time.time()
        sessions = orch.run_sync()
        lane_elapsed = time.time() - lane_start

        summary = orch.get_results_summary()
        summary["lane_id"] = lane_id
        summary["elapsed_seconds"] = round(lane_elapsed, 2)
        all_summaries.append(summary)

        # Print per-LSP results
        for detail in summary["details"]:
            status_icon = {"accepted": "+", "rejected": "x", "timeout": "~"}.get(detail["status"], "?")
            price_str = f"${detail['final_price']:,.2f}" if detail["final_price"] else "N/A"
            ch_label = detail.get("channel_type", "sim")
            print(
                f"  [{status_icon}] {detail['lsp_name']:25s} | "
                f"Quote: ${detail['initial_quote']:,.2f} -> Final: {price_str} | "
                f"Saved: ${detail['savings']:,.2f} | "
                f"Rounds: {detail['rounds']} | OTD: {detail['on_time_pct']:.0f}% | {ch_label}"
            )

        print(f"\n  Lane summary: {summary['accepted_deals']}/{summary['total_lsps']} accepted | "
              f"Savings: ${summary['total_savings']:,.2f} ({summary['avg_savings_pct']:.1f}%) | "
              f"Time: {lane_elapsed:.1f}s")

    total_elapsed = time.time() - total_start

    # ── Step 4: Aggregate results ──
    print(f"\n{'='*60}")
    print("OVERALL RESULTS")
    print(f"{'='*60}")

    grand_savings = sum(s["total_savings"] for s in all_summaries)
    grand_accepted = sum(s["accepted_deals"] for s in all_summaries)
    grand_total = sum(s["total_lsps"] for s in all_summaries)

    print(f"Lanes negotiated: {len(all_summaries)}")
    print(f"Total deals accepted: {grand_accepted}/{grand_total}")
    print(f"Grand total savings: ${grand_savings:,.2f}")
    print(f"Total negotiation time: {total_elapsed:.1f}s")
    if all_summaries:
        print(f"Avg time per lane: {total_elapsed / len(all_summaries):.1f}s")

    # ── Save logs ──
    LOGS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOGS_DIR / f"negotiation_{timestamp}.json"

    log_data = {
        "timestamp": timestamp,
        "config": {
            "lanes": lanes_to_negotiate,
            "channel": args.channel,
            "reliability_weight": args.reliability_weight,
            "max_lsps": args.max_lsps,
            "use_claude": args.use_claude,
        },
        "overall": {
            "grand_savings": grand_savings,
            "accepted": grand_accepted,
            "total": grand_total,
            "elapsed_seconds": round(total_elapsed, 2),
        },
        "lane_summaries": all_summaries,
    }

    with open(log_path, "w") as f:
        json.dump(log_data, f, indent=2, default=str)

    print(f"\nLogs saved to: {log_path}")

    # Cleanup
    if session_store:
        session_store.close()


if __name__ == "__main__":
    main()
