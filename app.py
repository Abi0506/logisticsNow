"""
Streamlit Dashboard -- Lively, animated negotiation monitoring with
WhatsApp / Gmail / Simulator channel support.

Run with:  streamlit run app.py
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent))

from generate_data import (
    BUDGET_PER_LANE,
    LANE_BASE_PRICES,
    LANES,
    LSP_PROFILES,
    generate_all,
)
from src.lsp_simulator import LSPSimulator, create_simulators_from_profiles
from src.orchestrator import ClosureGuard, Orchestrator
from src.strategy_brain import StrategyBrain

DATA_DIR = Path(__file__).parent / "data"

# =====================================================================
# Page config & custom CSS for lively look
# =====================================================================
st.set_page_config(
    page_title="SIN -- Swarm Intelligence Negotiator",
    page_icon="\U0001f91d",
    layout="wide",
)

st.markdown("""
<style>
/* Pulsing dot for active negotiations */
@keyframes pulse { 0%{opacity:1} 50%{opacity:.3} 100%{opacity:1} }
.pulse-dot {
    display:inline-block; width:10px; height:10px; border-radius:50%;
    background:#4CAF50; animation:pulse 1s infinite; margin-right:6px;
}
.pulse-dot-orange {
    display:inline-block; width:10px; height:10px; border-radius:50%;
    background:#FF9800; animation:pulse 0.8s infinite; margin-right:6px;
}

/* Chat bubbles */
.chat-container { max-height:360px; overflow-y:auto; padding:4px 0; }
.chat-bubble {
    padding:10px 14px; border-radius:16px; margin:6px 0;
    max-width:88%; font-size:0.88rem; line-height:1.4;
    word-wrap:break-word;
}
.chat-ours {
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    color:#fff; margin-left:auto; margin-right:4px;
    border-bottom-right-radius:4px; text-align:right;
}
.chat-lsp {
    background:#f0f2f6; color:#1a1a2e; margin-right:auto; margin-left:4px;
    border-bottom-left-radius:4px;
}
.chat-label {
    font-size:0.7rem; color:#888; margin:0 6px;
}
.chat-label-right { text-align:right; }

/* Status badges */
.badge { display:inline-block; padding:2px 10px; border-radius:12px;
         font-size:0.78rem; font-weight:600; }
.badge-active { background:#E8F5E9; color:#2E7D32; }
.badge-accepted { background:#E8F5E9; color:#1B5E20; }
.badge-rejected { background:#FFEBEE; color:#C62828; }
.badge-timeout { background:#FFF3E0; color:#E65100; }
.badge-waiting { background:#E3F2FD; color:#1565C0; }

/* Channel indicator */
.channel-chip {
    display:inline-block; padding:2px 8px; border-radius:8px;
    font-size:0.72rem; font-weight:500; margin-left:6px;
}
.chip-simulator { background:#E8EAF6; color:#283593; }
.chip-whatsapp  { background:#E8F5E9; color:#1B5E20; }
.chip-gmail     { background:#FCE4EC; color:#880E4F; }

/* KPI cards */
.kpi-card {
    background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
    border-radius:12px; padding:16px; text-align:center;
}
.kpi-value { font-size:1.8rem; font-weight:700; color:#1a1a2e; }
.kpi-label { font-size:0.78rem; color:#666; margin-top:2px; }

/* Section separators */
.section-header {
    border-left:4px solid #667eea; padding-left:12px;
    margin:20px 0 10px 0; font-size:1.15rem; font-weight:600;
}
</style>
""", unsafe_allow_html=True)

# =====================================================================
# Header
# =====================================================================
col_h1, col_h2 = st.columns([3, 1])
with col_h1:
    st.title("Swarm Intelligence Negotiator")
    st.caption("AI-powered parallel logistics rate negotiation  |  Simulator  &bull;  WhatsApp  &bull;  Gmail")
with col_h2:
    st.markdown("")  # spacer


# =====================================================================
# Sidebar -- config & channel selection
# =====================================================================
st.sidebar.header("Configuration")

# Channel selection
channel_mode = st.sidebar.selectbox(
    "Communication Channel",
    ["Simulator (Demo)", "WhatsApp (Twilio)", "Gmail (SMTP/IMAP)"],
    index=0,
    help="Simulator runs locally for demo. WhatsApp/Gmail send real messages.",
)

# WhatsApp config (shown only when selected)
wa_creds: dict[str, str] = {}
if channel_mode == "WhatsApp (Twilio)":
    st.sidebar.markdown("---")
    st.sidebar.subheader("WhatsApp / Twilio Config")
    wa_creds["account_sid"] = st.sidebar.text_input("Twilio Account SID", type="password")
    wa_creds["auth_token"] = st.sidebar.text_input("Twilio Auth Token", type="password")
    wa_creds["from_number"] = st.sidebar.text_input("Twilio WhatsApp From#", placeholder="+14155238886")
    st.sidebar.caption("LSP phone numbers are loaded from `config/lsp_contacts.json`.")

# Gmail config (shown only when selected)
gmail_creds: dict[str, str] = {}
if channel_mode == "Gmail (SMTP/IMAP)":
    st.sidebar.markdown("---")
    st.sidebar.subheader("Gmail Config")
    gmail_creds["email_address"] = st.sidebar.text_input("Gmail Address", placeholder="you@gmail.com")
    gmail_creds["app_password"] = st.sidebar.text_input("Gmail App Password", type="password")
    st.sidebar.caption("Use a Google App Password (not your regular password).")

st.sidebar.markdown("---")

selected_lane = st.sidebar.selectbox("Select Lane", LANES, index=0)

default_budget = BUDGET_PER_LANE.get(selected_lane, 1200)
budget = st.sidebar.slider(
    "Manufacturer Budget ($)",
    min_value=int(default_budget * 0.5),
    max_value=int(default_budget * 2.0),
    value=int(default_budget),
    step=50,
)

reliability_weight = st.sidebar.slider(
    "Reliability Weight",
    min_value=0.0, max_value=2.0, value=1.0, step=0.1,
    help="Higher = willing to pay more premium for on-time delivery",
)

market_demand = st.sidebar.slider(
    "Market Demand Factor",
    min_value=0.5, max_value=1.5, value=1.0, step=0.05,
    help="Higher demand = less aggressive pricing",
)

max_lsps = st.sidebar.slider("Number of LSPs", min_value=3, max_value=10, value=5)

run_button = st.sidebar.button(
    "Run Negotiation", type="primary", use_container_width=True
)

st.sidebar.markdown("---")
st.sidebar.caption("Built with Swarm Intelligence | Claude API + Streamlit")


# =====================================================================
# Data loading & model training (cached)
# =====================================================================
@st.cache_resource
def load_brain() -> StrategyBrain:
    if not (DATA_DIR / "historical_bids.csv").exists():
        generate_all()
    brain = StrategyBrain()
    brain.load_data()
    brain.train()
    return brain


brain = load_brain()


# =====================================================================
# Helpers -- channel badge, status badge, chat HTML
# =====================================================================
def _channel_badge(ch: str) -> str:
    cls = {"simulator": "chip-simulator", "whatsapp": "chip-whatsapp", "gmail": "chip-gmail"}.get(ch, "chip-simulator")
    label = {"simulator": "SIM", "whatsapp": "WA", "gmail": "GMAIL"}.get(ch, ch.upper())
    return f'<span class="channel-chip {cls}">{label}</span>'


def _status_badge(status: str) -> str:
    cls = {
        "active": "badge-active", "waiting_for_reply": "badge-waiting",
        "accepted": "badge-accepted", "rejected": "badge-rejected",
        "timeout": "badge-timeout",
    }.get(status, "badge-active")
    icons = {
        "active": '<span class="pulse-dot"></span>',
        "waiting_for_reply": '<span class="pulse-dot-orange"></span>',
        "accepted": "&#10004;", "rejected": "&#10008;", "timeout": "&#9200;",
    }
    icon = icons.get(status, "")
    return f'<span class="badge {cls}">{icon} {status.replace("_"," ").title()}</span>'


def _render_chat(history: list[dict], lsp_name: str) -> str:
    """Build HTML for a chat bubble view of the negotiation."""
    html = '<div class="chat-container">'
    for h in history:
        rnd = h.get("round", "?")
        our_msg = h.get("our_message", "")
        lsp_msg = h.get("lsp_message", "")
        our_price = h.get("our_offer", 0)
        lsp_price = h.get("lsp_price", 0)
        accepted = h.get("accepted", False)

        # Our message (right-aligned)
        html += f'<div class="chat-label chat-label-right">You &bull; Round {rnd} &bull; ${our_price:,.2f}</div>'
        html += f'<div class="chat-bubble chat-ours">{our_msg}</div>'

        # LSP message (left-aligned)
        acc_tag = ' &#10004; DEAL' if accepted else ''
        html += f'<div class="chat-label">{lsp_name} &bull; ${lsp_price:,.2f}{acc_tag}</div>'
        html += f'<div class="chat-bubble chat-lsp">{lsp_msg}</div>'

    html += '</div>'
    return html


def _kpi_card(label: str, value: str) -> str:
    return f'<div class="kpi-card"><div class="kpi-value">{value}</div><div class="kpi-label">{label}</div></div>'


# =====================================================================
# Build channel objects for real WhatsApp / Gmail
# =====================================================================
def _build_real_channels(
    mode: str, lane_id: str, wa_creds: dict, gmail_creds: dict
) -> tuple[dict, dict] | None:
    """Build channel dict + lsp_metadata for WhatsApp or Gmail mode.

    Returns None if credentials are missing.
    """
    from src.config_loader import get_lsp_metadata, load_config

    config_path = Path(__file__).parent / "config" / "lsp_contacts.json"
    if not config_path.exists():
        st.error("Missing `config/lsp_contacts.json`. Cannot use real channels.")
        return None

    config = load_config(str(config_path))
    contacts = config.get("contacts", [])
    lsp_metadata = get_lsp_metadata(config)

    # Filter to lane
    lane_contacts = [c for c in contacts if lane_id in c.get("lane_ids", [])]
    if not lane_contacts:
        lane_contacts = contacts

    channels: dict = {}

    if "WhatsApp" in mode:
        if not wa_creds.get("account_sid") or not wa_creds.get("auth_token"):
            st.error("Please fill in all WhatsApp / Twilio credentials in the sidebar.")
            return None
        from src.channels.whatsapp_channel import WhatsAppChannel
        wa_lsp: dict[str, str] = {}
        for c in lane_contacts:
            ch_info = c.get("channels", {})
            if "whatsapp" in ch_info:
                wa_lsp[c["lsp_id"]] = ch_info["whatsapp"]
        if not wa_lsp:
            st.error("No LSP WhatsApp contacts found in config.")
            return None
        wa_channel = WhatsAppChannel(
            account_sid=wa_creds["account_sid"],
            auth_token=wa_creds["auth_token"],
            from_number=wa_creds["from_number"],
            lsp_contacts=wa_lsp,
        )
        for lsp_id in wa_lsp:
            channels[lsp_id] = wa_channel

    elif "Gmail" in mode:
        if not gmail_creds.get("email_address") or not gmail_creds.get("app_password"):
            st.error("Please fill in Gmail credentials in the sidebar.")
            return None
        from src.channels.gmail_channel import GmailChannel
        gm_lsp: dict[str, str] = {}
        for c in lane_contacts:
            ch_info = c.get("channels", {})
            if "gmail" in ch_info:
                gm_lsp[c["lsp_id"]] = ch_info["gmail"]
        if not gm_lsp:
            st.error("No LSP Gmail contacts found in config.")
            return None
        gm_channel = GmailChannel(
            smtp_server="smtp.gmail.com", smtp_port=587,
            imap_server="imap.gmail.com", imap_port=993,
            email_address=gmail_creds["email_address"],
            app_password=gmail_creds["app_password"],
            lsp_contacts=gm_lsp,
            poll_interval=30,
        )
        for lsp_id in gm_lsp:
            channels[lsp_id] = gm_channel

    filtered_meta = {k: v for k, v in lsp_metadata.items() if k in channels}
    return channels, filtered_meta


# =====================================================================
# Run negotiation with live UI updates
# =====================================================================
def run_negotiation_live(
    lane_id: str,
    budget_val: float,
    rel_weight: float,
    n_lsps: int,
    channel_mode_str: str,
) -> tuple[list, dict]:
    """Execute the negotiation showing live progress in Streamlit."""

    is_simulator = "Simulator" in channel_mode_str

    if is_simulator:
        profiles = LSP_PROFILES[:n_lsps]
        simulators = create_simulators_from_profiles(profiles, LANE_BASE_PRICES, lane_id)
    else:
        result = _build_real_channels(channel_mode_str, lane_id, wa_creds, gmail_creds)
        if result is None:
            return [], {}
        channels_dict, lsp_meta = result
        simulators = None

    # ----- Status header -----
    status_placeholder = st.empty()
    timer_placeholder = st.empty()

    # ----- Live negotiation grid (up to 3 columns) -----
    st.markdown('<div class="section-header">Live Negotiations</div>', unsafe_allow_html=True)
    n_display = min(n_lsps if is_simulator else 5, 6)
    live_cols = st.columns(min(n_display, 3))
    # We create placeholders for each visible LSP
    chat_placeholders: list = []
    chart_placeholders: list = []
    info_placeholders: list = []
    for i in range(min(n_display, 3)):
        with live_cols[i]:
            info_placeholders.append(st.empty())
            chart_placeholders.append(st.empty())
            chat_placeholders.append(st.empty())

    # Shared mutable state updated by the async callback
    live_state: dict[str, Any] = {
        "sessions": {},
        "order": [],
        "start_time": time.time(),
    }
    lock = threading.Lock()

    async def on_event(session: Any, event: str) -> None:
        """Callback invoked by the orchestrator on every event."""
        with lock:
            live_state["sessions"][session.lsp_id] = session
            if session.lsp_id not in live_state["order"]:
                live_state["order"].append(session.lsp_id)

    # Build orchestrator
    if is_simulator:
        orch = Orchestrator(
            strategy_brain=brain,
            simulators=simulators,
            lane_id=lane_id,
            budget=budget_val,
            reliability_weight=rel_weight,
            use_claude=False,
            on_round_callback=on_event,
            sim_round_delay=1.3,
        )
    else:
        orch = Orchestrator(
            strategy_brain=brain,
            channels=channels_dict,
            lsp_metadata=lsp_meta,
            lane_id=lane_id,
            budget=budget_val,
            reliability_weight=rel_weight,
            use_claude=False,
            on_round_callback=on_event,
            reply_timeout=86400,
            sim_round_delay=0,
        )

    # Run orchestrator in a background thread
    result_holder: dict[str, Any] = {"sessions": [], "summary": {}, "done": False}

    def _run_orch():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        sessions = loop.run_until_complete(orch.run())
        summary = orch.get_results_summary()
        result_holder["sessions"] = sessions
        result_holder["summary"] = summary
        result_holder["done"] = True
        loop.close()

    bg_thread = threading.Thread(target=_run_orch, daemon=True)
    bg_thread.start()

    # ----- Live polling loop: update UI while negotiation runs -----
    while not result_holder["done"]:
        elapsed = time.time() - live_state["start_time"]
        minutes, secs = divmod(int(elapsed), 60)

        with lock:
            active_count = sum(
                1 for s in live_state["sessions"].values()
                if s.status in ("active", "waiting_for_reply")
            )
            done_count = sum(
                1 for s in live_state["sessions"].values()
                if s.status in ("accepted", "rejected", "timeout")
            )
            total = len(live_state["sessions"])

        status_placeholder.markdown(
            f'<span class="pulse-dot"></span> **Negotiating...** &nbsp; '
            f'{done_count}/{total if total else n_lsps} complete &nbsp; | &nbsp; '
            f'Time: {minutes}:{secs:02d}',
            unsafe_allow_html=True,
        )

        # Update the visible LSP cards
        with lock:
            visible_ids = live_state["order"][:3]
            for i, lsp_id in enumerate(visible_ids):
                s = live_state["sessions"].get(lsp_id)
                if s is None:
                    continue

                ch_badge = _channel_badge(s.channel_type)
                st_badge = _status_badge(s.status)

                # Info line
                info_placeholders[i].markdown(
                    f'**{s.lsp_name}** {ch_badge} {st_badge}<br>'
                    f'<span style="font-size:0.8rem;color:#666;">'
                    f'Persona: {s.persona} &bull; Round {s.round_num}/8'
                    f'</span>',
                    unsafe_allow_html=True,
                )

                # Mini chart
                if s.history:
                    offers = [h["our_offer"] for h in s.history]
                    lsp_prices = [h["lsp_price"] for h in s.history]
                    rounds = list(range(1, len(offers) + 1))

                    fig = go.Figure()
                    fig.add_trace(go.Scatter(
                        x=rounds, y=offers, mode="lines+markers",
                        name="Our Offer", line=dict(color="#667eea", width=2),
                        marker=dict(size=7),
                    ))
                    fig.add_trace(go.Scatter(
                        x=rounds, y=lsp_prices, mode="lines+markers",
                        name="LSP Price", line=dict(color="#FF5722", width=2),
                        marker=dict(size=7),
                    ))
                    fig.add_hline(y=s.target_price, line_dash="dash", line_color="#4CAF50",
                                  annotation_text="Target")
                    fig.add_hline(y=budget_val, line_dash="dot", line_color="#FF9800",
                                  annotation_text="Budget")
                    fig.update_layout(
                        height=220, margin=dict(l=10, r=10, t=25, b=10),
                        xaxis_title="Round", yaxis_title="$",
                        legend=dict(orientation="h", y=-0.35, font=dict(size=10)),
                        plot_bgcolor="rgba(0,0,0,0)",
                    )
                    chart_placeholders[i].plotly_chart(fig, use_container_width=True, key=f"live_{lsp_id}_{s.round_num}")

                # Chat bubbles
                if s.history:
                    chat_html = _render_chat(s.history, s.lsp_name)
                    chat_placeholders[i].markdown(chat_html, unsafe_allow_html=True)

        time.sleep(0.6)

    # Final update
    elapsed = time.time() - live_state["start_time"]
    minutes, secs = divmod(int(elapsed), 60)
    status_placeholder.markdown(
        f'&#10004; **Negotiation complete** in {minutes}:{secs:02d}',
        unsafe_allow_html=True,
    )
    timer_placeholder.empty()

    bg_thread.join(timeout=5)
    return result_holder["sessions"], result_holder["summary"]


# =====================================================================
# MAIN: Run button handler
# =====================================================================
if run_button:
    sessions, summary = run_negotiation_live(
        selected_lane, budget, reliability_weight, max_lsps, channel_mode,
    )
    if sessions and summary:
        st.session_state["sessions"] = sessions
        st.session_state["summary"] = summary
        st.session_state["budget"] = budget
        st.session_state["lane"] = selected_lane
        st.session_state["channel_mode"] = channel_mode


# =====================================================================
# Display persisted results (if available)
# =====================================================================
if "summary" in st.session_state:
    summary = st.session_state["summary"]
    sessions = st.session_state["sessions"]
    budget_val = st.session_state["budget"]
    ch_mode = st.session_state.get("channel_mode", "Simulator (Demo)")

    # ----- KPI Row -----
    st.markdown('<div class="section-header">Key Metrics</div>', unsafe_allow_html=True)
    k1, k2, k3, k4, k5 = st.columns(5)
    with k1:
        st.markdown(_kpi_card("Deals Accepted", f"{summary['accepted_deals']}/{summary['total_lsps']}"), unsafe_allow_html=True)
    with k2:
        st.markdown(_kpi_card("Total Savings", f"${summary['total_savings']:,.0f}"), unsafe_allow_html=True)
    with k3:
        st.markdown(_kpi_card("Avg Savings %", f"{summary['avg_savings_pct']:.1f}%"), unsafe_allow_html=True)
    with k4:
        st.markdown(_kpi_card("Rejected", str(summary['rejected_deals'])), unsafe_allow_html=True)
    with k5:
        st.markdown(_kpi_card("Timed Out", str(summary['timeout_deals'])), unsafe_allow_html=True)

    if summary["best_deal"]:
        bd = summary["best_deal"]
        st.success(
            f"**Best Deal:** {bd['lsp_name']} at **${bd['final_price']:,.2f}** "
            f"(saved ${bd['savings']:,.2f}, OTD {bd['on_time_pct']:.0f}%)"
        )

    # ----- Detailed chat view for each LSP -----
    st.markdown('<div class="section-header">Negotiation Conversations</div>', unsafe_allow_html=True)

    # Show in expandable cards, 2 per row
    for row_start in range(0, len(sessions), 2):
        row_sessions = sessions[row_start:row_start + 2]
        cols = st.columns(len(row_sessions))
        for idx, session in enumerate(row_sessions):
            with cols[idx]:
                ch_badge = _channel_badge(session.channel_type)
                st_badge = _status_badge(session.status)
                savings_str = f"${session.savings:,.2f}" if session.savings else "--"
                final_str = f"${session.final_price:,.2f}" if session.final_price else "--"

                with st.expander(
                    f"{session.lsp_name}  |  {session.status.upper()}  |  "
                    f"Final: {final_str}  |  Saved: {savings_str}  |  "
                    f"Rounds: {session.round_num}",
                    expanded=(idx < 2 and row_start == 0),
                ):
                    st.markdown(
                        f'{ch_badge} {st_badge} &nbsp; Persona: **{session.persona}**',
                        unsafe_allow_html=True,
                    )

                    if session.history:
                        # Offer trajectory chart
                        offers = [h["our_offer"] for h in session.history]
                        lsp_prices = [h["lsp_price"] for h in session.history]
                        rounds_list = list(range(1, len(offers) + 1))

                        fig = go.Figure()
                        fig.add_trace(go.Scatter(
                            x=rounds_list, y=offers, mode="lines+markers",
                            name="Our Offer", line=dict(color="#667eea", width=2.5),
                            marker=dict(size=8),
                        ))
                        fig.add_trace(go.Scatter(
                            x=rounds_list, y=lsp_prices, mode="lines+markers",
                            name="LSP Price", line=dict(color="#FF5722", width=2.5),
                            marker=dict(size=8),
                        ))
                        fig.add_hline(y=session.target_price, line_dash="dash",
                                      line_color="#4CAF50", annotation_text="Target")
                        fig.add_hline(y=budget_val, line_dash="dot",
                                      line_color="#FF9800", annotation_text="Budget")
                        if session.final_price:
                            fig.add_hline(y=session.final_price, line_dash="solid",
                                          line_color="#9C27B0", annotation_text="Final",
                                          line_width=2)
                        fig.update_layout(
                            height=260, margin=dict(l=10, r=10, t=30, b=10),
                            xaxis_title="Round", yaxis_title="Price ($)",
                            legend=dict(orientation="h", y=-0.28, font=dict(size=10)),
                            plot_bgcolor="rgba(0,0,0,0)",
                        )
                        st.plotly_chart(fig, use_container_width=True)

                        # Chat bubbles
                        st.markdown("**Conversation:**")
                        chat_html = _render_chat(session.history, session.lsp_name)
                        st.markdown(chat_html, unsafe_allow_html=True)

                        # Sentiment trail
                        if session.sentiment_log:
                            sent_labels = [s.get("sentiment", "?") for s in session.sentiment_log]
                            sent_conf = [s.get("confidence", 0) for s in session.sentiment_log]
                            flexibility_trail = [s.get("flexibility_signal", "?") for s in session.sentiment_log]
                            st.markdown("**Sentiment Trail:**")
                            sent_cols = st.columns(len(sent_labels))
                            for si, sc in enumerate(sent_cols):
                                col_map = {"positive": "#4CAF50", "negative": "#F44336",
                                           "neutral": "#FF9800", "frustrated": "#D32F2F"}
                                clr = col_map.get(sent_labels[si], "#999")
                                sc.markdown(
                                    f'<div style="text-align:center;">'
                                    f'<div style="font-size:0.7rem;color:#888;">R{si+1}</div>'
                                    f'<div style="color:{clr};font-weight:600;font-size:0.85rem;">'
                                    f'{sent_labels[si]}</div>'
                                    f'<div style="font-size:0.65rem;color:#aaa;">'
                                    f'{flexibility_trail[si]}</div></div>',
                                    unsafe_allow_html=True,
                                )

    # ----- Results Table -----
    st.markdown('<div class="section-header">All LSP Results</div>', unsafe_allow_html=True)
    results_df = pd.DataFrame(summary["details"])
    display_cols = {
        "lsp_id": "LSP ID", "lsp_name": "LSP Name", "persona": "Persona",
        "initial_quote": "Initial Quote", "final_price": "Final Price",
        "savings": "Savings", "status": "Status", "rounds": "Rounds",
        "on_time_pct": "OTD %", "channel_type": "Channel",
    }
    results_df = results_df.rename(columns=display_cols)
    st.dataframe(results_df, use_container_width=True, hide_index=True)

    # ----- ZOPA Analysis -----
    st.markdown('<div class="section-header">ZOPA Analysis</div>', unsafe_allow_html=True)

    selected_lsp = st.selectbox(
        "Select LSP for ZOPA view",
        [s.lsp_id for s in sessions],
        format_func=lambda x: next(
            (s.lsp_name for s in sessions if s.lsp_id == x), x
        ),
    )

    sel_session = next(s for s in sessions if s.lsp_id == selected_lsp)

    zopa_col1, zopa_col2 = st.columns([2, 1])

    with zopa_col1:
        zopa_fig = go.Figure()
        zopa_fig.add_shape(
            type="rect", x0=0, x1=1,
            y0=sel_session.zopa_low, y1=sel_session.zopa_high,
            fillcolor="rgba(76, 175, 80, 0.15)",
            line=dict(color="green", width=1),
        )
        prices = {
            "Initial Quote": (sel_session.initial_quote, "#FF5722", "dash"),
            "Target Price": (sel_session.target_price, "#2196F3", "dash"),
            "ZOPA Low": (sel_session.zopa_low, "#4CAF50", "dot"),
            "ZOPA High": (sel_session.zopa_high, "#FF9800", "dot"),
        }
        if sel_session.final_price:
            prices["Final Price"] = (sel_session.final_price, "#9C27B0", "solid")

        for label, (price, color, dash) in prices.items():
            zopa_fig.add_hline(
                y=price, line_dash=dash, line_color=color, line_width=2,
                annotation_text=f"{label}: ${price:,.2f}",
                annotation_position="top left",
            )
        zopa_fig.update_layout(
            height=350, xaxis=dict(visible=False), yaxis_title="Price ($)",
            title=f"ZOPA -- {sel_session.lsp_name}",
            margin=dict(l=10, r=10, t=50, b=10), plot_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(zopa_fig, use_container_width=True)

    with zopa_col2:
        st.markdown("**Zone of Possible Agreement**")
        zopa_range = sel_session.zopa_high - sel_session.zopa_low
        st.markdown(f"- **ZOPA Range:** ${zopa_range:,.2f}")
        st.markdown(f"- **Reservation (Low):** ${sel_session.zopa_low:,.2f}")
        st.markdown(f"- **Budget (High):** ${sel_session.zopa_high:,.2f}")
        st.markdown(f"- **Target:** ${sel_session.target_price:,.2f}")
        if sel_session.final_price:
            position = ((sel_session.final_price - sel_session.zopa_low) / zopa_range * 100) if zopa_range > 0 else 0
            st.markdown(f"- **Final Price:** ${sel_session.final_price:,.2f}")
            st.markdown(f"- **Position in ZOPA:** {position:.0f}%")

    # ----- Offer history for selected LSP -----
    if sel_session.history:
        st.markdown(f'<div class="section-header">Offer History -- {sel_session.lsp_name}</div>', unsafe_allow_html=True)
        hist_df = pd.DataFrame(sel_session.history)
        fig2 = go.Figure()
        fig2.add_trace(go.Scatter(
            x=hist_df["round"], y=hist_df["our_offer"],
            mode="lines+markers", name="Our Offer",
            line=dict(color="#667eea", width=2.5), marker=dict(size=8),
        ))
        fig2.add_trace(go.Scatter(
            x=hist_df["round"], y=hist_df["lsp_price"],
            mode="lines+markers", name="LSP Price",
            line=dict(color="#FF5722", width=2.5), marker=dict(size=8),
        ))
        fig2.add_hline(y=sel_session.target_price, line_dash="dash", line_color="#4CAF50")
        fig2.add_hline(y=sel_session.zopa_low, line_dash="dot", line_color="#999")
        fig2.update_layout(
            height=350, xaxis_title="Round", yaxis_title="Price ($)",
            margin=dict(l=10, r=10, t=30, b=10), plot_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig2, use_container_width=True)

    # ----- Savings breakdown -----
    st.markdown('<div class="section-header">Savings Breakdown</div>', unsafe_allow_html=True)
    accepted_details = [d for d in summary["details"] if d["status"] == "accepted"]
    if accepted_details:
        savings_df = pd.DataFrame(accepted_details)
        fig3 = px.bar(
            savings_df, x="lsp_name", y="savings",
            color="on_time_pct", color_continuous_scale="Greens",
            labels={"lsp_name": "LSP", "savings": "Savings ($)", "on_time_pct": "OTD %"},
        )
        fig3.update_layout(height=350, plot_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig3, use_container_width=True)
    else:
        st.warning("No accepted deals to visualise.")

    # ----- Reliability-weighted scoring -----
    st.markdown('<div class="section-header">Reliability-Weighted Scoring</div>', unsafe_allow_html=True)
    if accepted_details:
        score_rows = []
        for d in accepted_details:
            max_ok = ClosureGuard.max_acceptable_price(
                budget_val, d["on_time_pct"], reliability_weight
            )
            score = round(
                (1 - d["final_price"] / max_ok) * 100 + d["on_time_pct"] * 0.3, 1,
            )
            score_rows.append({
                "LSP": d["lsp_name"],
                "Price": f"${d['final_price']:,.2f}",
                "OTD %": f"{d['on_time_pct']:.0f}%",
                "Max Acceptable": f"${max_ok:,.2f}",
                "Score": score,
            })
        score_df = pd.DataFrame(score_rows).sort_values("Score", ascending=False)
        st.dataframe(score_df, use_container_width=True, hide_index=True)

else:
    # No results yet -- show welcome
    st.markdown("---")
    col_w1, col_w2 = st.columns([2, 1])
    with col_w1:
        st.markdown("""
        ### Getting Started

        1. **Choose a channel** in the sidebar (Simulator for demo, or WhatsApp/Gmail for real negotiations)
        2. **Adjust parameters** -- budget, reliability weight, number of LSPs
        3. **Click "Run Negotiation"** to watch the AI negotiate in real time

        The system will negotiate with multiple LSPs **simultaneously**, adapting its
        strategy per-LSP based on their flexibility and behaviour.

        **Channels:**
        - **Simulator** -- instant local simulation, great for demos
        - **WhatsApp** -- sends real messages via Twilio
        - **Gmail** -- sends real emails via SMTP, polls responses via IMAP
        """)
    with col_w2:
        st.markdown("""
        ### Architecture

        ```
        Orchestrator (asyncio)
            |
            +-- Strategy Brain
            |     ZOPA Engine
            |     Persona Selector
            |
            +-- Tactical Agent
            |     Counter-offers
            |     Sentiment Analysis
            |
            +-- Channels
                  Simulator
                  WhatsApp (Twilio)
                  Gmail (SMTP/IMAP)
        ```
        """)
