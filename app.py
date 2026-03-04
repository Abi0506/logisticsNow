"""
Streamlit Dashboard – Live negotiation monitoring and results visualization.

Run with:  streamlit run app.py
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

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

# ──────────────────────────────────────────────
# Page config
# ──────────────────────────────────────────────
st.set_page_config(
    page_title="SIN – Swarm Intelligence Negotiator",
    page_icon="🤝",
    layout="wide",
)

st.title("Swarm Intelligence Negotiator")
st.caption("AI-powered parallel logistics rate negotiation")


# ──────────────────────────────────────────────
# Sidebar controls
# ──────────────────────────────────────────────
st.sidebar.header("Configuration")

selected_lane = st.sidebar.selectbox("Select Lane", LANES, index=0)

default_budget = BUDGET_PER_LANE.get(selected_lane, 1200)
budget = st.sidebar.slider(
    "Manufacturer Budget",
    min_value=int(default_budget * 0.5),
    max_value=int(default_budget * 2.0),
    value=int(default_budget),
    step=50,
)

reliability_weight = st.sidebar.slider(
    "Reliability Weight",
    min_value=0.0,
    max_value=2.0,
    value=1.0,
    step=0.1,
    help="Higher = willing to pay more premium for on-time delivery",
)

market_demand = st.sidebar.slider(
    "Market Demand Factor",
    min_value=0.5,
    max_value=1.5,
    value=1.0,
    step=0.05,
    help="Higher demand = less aggressive pricing",
)

max_lsps = st.sidebar.slider("Number of LSPs", min_value=3, max_value=10, value=5)

run_button = st.sidebar.button("Run Negotiation", type="primary", use_container_width=True)


# ──────────────────────────────────────────────
# Data generation & model training (cached)
# ──────────────────────────────────────────────
@st.cache_resource
def load_brain() -> StrategyBrain:
    """Load data, train model, return brain (cached across reruns)."""
    if not (DATA_DIR / "historical_bids.csv").exists():
        generate_all()
    brain = StrategyBrain()
    brain.load_data()
    brain.train()
    return brain


brain = load_brain()


# ──────────────────────────────────────────────
# Run negotiation
# ──────────────────────────────────────────────
def run_negotiation(
    lane_id: str, budget_val: float, rel_weight: float, n_lsps: int
) -> tuple[list, dict]:
    """Execute the negotiation and return (sessions, summary)."""
    profiles = LSP_PROFILES[:n_lsps]
    simulators = create_simulators_from_profiles(profiles, LANE_BASE_PRICES, lane_id)

    orch = Orchestrator(
        strategy_brain=brain,
        simulators=simulators,
        lane_id=lane_id,
        budget=budget_val,
        reliability_weight=rel_weight,
        use_claude=False,  # use offline mode for fast demo
    )
    sessions = orch.run_sync()
    summary = orch.get_results_summary()
    return sessions, summary


if run_button:
    with st.spinner("Negotiating with LSPs in parallel..."):
        start = time.time()
        sessions, summary = run_negotiation(
            selected_lane, budget, reliability_weight, max_lsps
        )
        elapsed = time.time() - start

    st.success(f"Negotiation complete in {elapsed:.1f}s")

    # Store in session state for persistence
    st.session_state["sessions"] = sessions
    st.session_state["summary"] = summary
    st.session_state["budget"] = budget
    st.session_state["lane"] = selected_lane


# ──────────────────────────────────────────────
# Display results (if available)
# ──────────────────────────────────────────────
if "summary" in st.session_state:
    summary = st.session_state["summary"]
    sessions = st.session_state["sessions"]
    budget_val = st.session_state["budget"]

    # ── KPI metrics row ──
    st.markdown("---")
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Deals Accepted", f"{summary['accepted_deals']}/{summary['total_lsps']}")
    col2.metric("Total Savings", f"${summary['total_savings']:,.0f}")
    col3.metric("Avg Savings %", f"{summary['avg_savings_pct']:.1f}%")
    col4.metric("Rejected", summary["rejected_deals"])
    col5.metric("Timed Out", summary["timeout_deals"])

    if summary["best_deal"]:
        bd = summary["best_deal"]
        st.info(
            f"**Best Deal:** {bd['lsp_name']} at **${bd['final_price']:,.2f}** "
            f"(saved ${bd['savings']:,.2f}, OTD {bd['on_time_pct']:.0f}%)"
        )

    # ── Live negotiation split-screen ──
    st.markdown("### Live Negotiation View")
    display_sessions = sessions[:3]  # show up to 3 side-by-side
    cols = st.columns(len(display_sessions))

    for idx, session in enumerate(display_sessions):
        with cols[idx]:
            status_emoji = {"accepted": "✅", "rejected": "❌", "timeout": "⏰"}.get(
                session.status, "🔄"
            )
            st.markdown(f"**{session.lsp_name}** {status_emoji}")
            st.caption(f"Persona: {session.persona}")

            if session.history:
                offers = [h["our_offer"] for h in session.history]
                lsp_prices = [h["lsp_price"] for h in session.history]
                rounds = list(range(1, len(offers) + 1))

                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=rounds, y=offers,
                    mode="lines+markers", name="Our Offer",
                    line=dict(color="#2196F3"),
                ))
                fig.add_trace(go.Scatter(
                    x=rounds, y=lsp_prices,
                    mode="lines+markers", name="LSP Price",
                    line=dict(color="#FF5722"),
                ))
                fig.add_hline(
                    y=session.target_price, line_dash="dash",
                    line_color="green", annotation_text="Target",
                )
                fig.add_hline(
                    y=budget_val, line_dash="dot",
                    line_color="orange", annotation_text="Budget",
                )
                fig.update_layout(
                    height=280, margin=dict(l=20, r=20, t=30, b=20),
                    xaxis_title="Round", yaxis_title="Price ($)",
                    legend=dict(orientation="h", y=-0.25),
                )
                st.plotly_chart(fig, use_container_width=True)

            if session.final_price:
                st.metric("Final Price", f"${session.final_price:,.2f}")
            st.caption(f"Rounds: {session.round_num}")

    # ── Results table ──
    st.markdown("### All LSP Results")
    results_df = pd.DataFrame(summary["details"])
    results_df = results_df.rename(columns={
        "lsp_id": "LSP ID",
        "lsp_name": "LSP Name",
        "persona": "Persona",
        "initial_quote": "Initial Quote",
        "final_price": "Final Price",
        "savings": "Savings",
        "status": "Status",
        "rounds": "Rounds",
        "on_time_pct": "OTD %",
    })
    st.dataframe(results_df, use_container_width=True, hide_index=True)

    # ── ZOPA Visualization ──
    st.markdown("### ZOPA Analysis")
    selected_lsp = st.selectbox(
        "Select LSP for ZOPA view",
        [s.lsp_id for s in sessions],
        format_func=lambda x: next(
            (s.lsp_name for s in sessions if s.lsp_id == x), x
        ),
    )

    sel_session = next(s for s in sessions if s.lsp_id == selected_lsp)

    zopa_fig = go.Figure()

    # ZOPA band
    zopa_fig.add_shape(
        type="rect",
        x0=0, x1=1,
        y0=sel_session.zopa_low, y1=sel_session.zopa_high,
        fillcolor="rgba(76, 175, 80, 0.2)",
        line=dict(color="green", width=1),
    )

    # Price markers
    prices = {
        "Initial Quote": sel_session.initial_quote,
        "Target Price": sel_session.target_price,
        "ZOPA Low (Reservation)": sel_session.zopa_low,
        "ZOPA High (Budget)": sel_session.zopa_high,
    }
    if sel_session.final_price:
        prices["Final Price"] = sel_session.final_price

    colors = {
        "Initial Quote": "#FF5722",
        "Target Price": "#2196F3",
        "ZOPA Low (Reservation)": "#4CAF50",
        "ZOPA High (Budget)": "#FF9800",
        "Final Price": "#9C27B0",
    }

    for label, price in prices.items():
        zopa_fig.add_hline(
            y=price, line_dash="solid" if "Final" in label else "dash",
            line_color=colors.get(label, "gray"),
            annotation_text=f"{label}: ${price:,.2f}",
            annotation_position="top left",
        )

    zopa_fig.update_layout(
        height=350,
        xaxis=dict(visible=False),
        yaxis_title="Price ($)",
        title=f"ZOPA for {sel_session.lsp_name}",
        margin=dict(l=20, r=20, t=50, b=20),
    )
    st.plotly_chart(zopa_fig, use_container_width=True)

    # ── Offer history chart for selected LSP ──
    if sel_session.history:
        st.markdown(f"### Offer History – {sel_session.lsp_name}")
        hist_df = pd.DataFrame(sel_session.history)

        fig2 = go.Figure()
        fig2.add_trace(go.Scatter(
            x=hist_df["round"], y=hist_df["our_offer"],
            mode="lines+markers", name="Our Offer",
            line=dict(color="#2196F3", width=2),
        ))
        fig2.add_trace(go.Scatter(
            x=hist_df["round"], y=hist_df["lsp_price"],
            mode="lines+markers", name="LSP Price",
            line=dict(color="#FF5722", width=2),
        ))
        fig2.add_hline(y=sel_session.target_price, line_dash="dash", line_color="green")
        fig2.add_hline(y=sel_session.zopa_low, line_dash="dot", line_color="gray")
        fig2.update_layout(
            height=350, xaxis_title="Round", yaxis_title="Price ($)",
            margin=dict(l=20, r=20, t=30, b=20),
        )
        st.plotly_chart(fig2, use_container_width=True)

    # ── Savings breakdown chart ──
    st.markdown("### Savings Breakdown")
    accepted_details = [d for d in summary["details"] if d["status"] == "accepted"]
    if accepted_details:
        savings_df = pd.DataFrame(accepted_details)
        fig3 = px.bar(
            savings_df,
            x="lsp_name",
            y="savings",
            color="on_time_pct",
            color_continuous_scale="Greens",
            labels={"lsp_name": "LSP", "savings": "Savings ($)", "on_time_pct": "OTD %"},
            title="Savings per LSP (color = reliability)",
        )
        fig3.update_layout(height=350)
        st.plotly_chart(fig3, use_container_width=True)
    else:
        st.warning("No accepted deals to visualize.")

    # ── Reliability-weighted score ──
    st.markdown("### Reliability-Weighted Score")
    if accepted_details:
        for d in accepted_details:
            max_ok = ClosureGuard.max_acceptable_price(
                budget_val, d["on_time_pct"], reliability_weight
            )
            d["max_acceptable"] = max_ok
            d["score"] = round(
                (1 - d["final_price"] / max_ok) * 100 + d["on_time_pct"] * 0.3,
                1,
            )
        score_df = pd.DataFrame(accepted_details).sort_values("score", ascending=False)
        st.dataframe(
            score_df[["lsp_name", "final_price", "on_time_pct", "max_acceptable", "score"]].rename(
                columns={
                    "lsp_name": "LSP",
                    "final_price": "Price",
                    "on_time_pct": "OTD %",
                    "max_acceptable": "Max Acceptable",
                    "score": "Score",
                }
            ),
            use_container_width=True,
            hide_index=True,
        )

else:
    st.info("Configure parameters in the sidebar and click **Run Negotiation** to begin.")


# ── Footer ──
st.sidebar.markdown("---")
st.sidebar.caption("Built with Swarm Intelligence | Claude API + Streamlit")
