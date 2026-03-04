"""
Orchestrator -- manages multiple concurrent negotiation sessions using asyncio.

Supports three modes via the channel abstraction:
  - Simulator (default) -- instant local negotiations
  - WhatsApp (Twilio) -- real-time messaging with webhook-based replies
  - Gmail (SMTP/IMAP) -- email-based negotiations with polling

Parallel negotiation flow:
  1. For each LSP, compute strategy (target price + persona).
  2. Generate a counter-offer (via tactical agent or offline fallback).
  3. Send the offer via the channel (simulator, WhatsApp, or Gmail).
  4. Wait for the LSP response via the channel's async queue.
  5. Extract price and sentiment from the reply.
  6. Repeat until convergence or max rounds.
  7. Apply ClosureGuard before finalising any deal.

Live dashboard support:
  - ``on_round_callback(session, event_type)`` fires after every round and
    key lifecycle events so the Streamlit UI can update in real time.
  - ``sim_round_delay`` adds deliberate pacing for the simulator channel to
    make the demo feel lively (default 1.2 s).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

from src.channels.base import MessageChannel
from src.lsp_simulator import LSPSimulator
from src.strategy_brain import StrategyBrain
from src.tactical_agent import (
    analyze_sentiment_offline,
    generate_counter_offer_offline,
)

logger = logging.getLogger(__name__)

# Attempt to import Claude-powered functions; fall back to offline if no API key
_USE_CLAUDE = bool(os.environ.get("ANTHROPIC_API_KEY"))
if _USE_CLAUDE:
    from src.tactical_agent import analyze_sentiment, generate_counter_offer


MAX_ROUNDS = 8
RELIABILITY_PREMIUM_FACTOR = 0.05  # 5% premium per 10% above 85% OTD


# ---------------------------------------------------------------------------
# Callback event types emitted during negotiation
# ---------------------------------------------------------------------------
EVENT_SESSION_START = "session_start"      # session initialised
EVENT_OFFER_SENT = "offer_sent"            # our counter-offer sent
EVENT_REPLY_RECEIVED = "reply_received"    # LSP replied
EVENT_ROUND_COMPLETE = "round_complete"    # round fully processed
EVENT_SESSION_END = "session_end"          # session concluded


# Type alias for the optional callback
RoundCallback = Callable[["NegotiationSession", str], Coroutine[Any, Any, None]]


@dataclass
class NegotiationSession:
    """Tracks the state of a single LSP negotiation."""

    lsp_id: str
    lsp_name: str
    lane_id: str
    persona: str
    target_price: float
    budget: float
    zopa_low: float
    zopa_high: float
    initial_quote: float
    current_offer: float = 0.0
    lsp_current_price: float = 0.0
    status: str = "active"  # active | waiting_for_reply | accepted | rejected | timeout
    round_num: int = 0
    history: list[dict[str, Any]] = field(default_factory=list)
    final_price: float | None = None
    savings: float = 0.0
    sentiment_log: list[dict[str, Any]] = field(default_factory=list)
    channel_type: str = "simulator"
    # Live state for dashboard rendering
    last_our_message: str = ""
    last_lsp_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialise session state for persistence."""
        return {
            "session_id": f"{self.lane_id}_{self.lsp_id}",
            "lsp_id": self.lsp_id,
            "lsp_name": self.lsp_name,
            "lane_id": self.lane_id,
            "persona": self.persona,
            "target_price": self.target_price,
            "budget": self.budget,
            "zopa_low": self.zopa_low,
            "zopa_high": self.zopa_high,
            "initial_quote": self.initial_quote,
            "current_offer": self.current_offer,
            "lsp_current_price": self.lsp_current_price,
            "status": self.status,
            "round_num": self.round_num,
            "final_price": self.final_price,
            "savings": self.savings,
        }


@dataclass
class SharedMemory:
    """Cross-agent shared memory for learning LSP flexibility in real time."""

    lsp_flexibility: dict[str, float] = field(default_factory=dict)
    lsp_concession_history: dict[str, list[float]] = field(default_factory=dict)

    def update(self, lsp_id: str, price_before: float, price_after: float) -> None:
        if price_before == 0:
            return
        concession_pct = (price_before - price_after) / price_before
        self.lsp_concession_history.setdefault(lsp_id, []).append(concession_pct)
        avg = sum(self.lsp_concession_history[lsp_id]) / len(self.lsp_concession_history[lsp_id])
        self.lsp_flexibility[lsp_id] = round(avg, 4)


class ClosureGuard:
    """Validates that a final deal meets budget + reliability premium constraints."""

    @staticmethod
    def is_acceptable(
        final_price: float,
        budget: float,
        on_time_pct: float,
        reliability_weight: float = 1.0,
    ) -> bool:
        otd_above_baseline = max(0, (on_time_pct - 85) / 10)
        premium = budget * RELIABILITY_PREMIUM_FACTOR * otd_above_baseline * reliability_weight
        max_acceptable = budget + premium
        return final_price <= max_acceptable

    @staticmethod
    def max_acceptable_price(
        budget: float,
        on_time_pct: float,
        reliability_weight: float = 1.0,
    ) -> float:
        otd_above_baseline = max(0, (on_time_pct - 85) / 10)
        premium = budget * RELIABILITY_PREMIUM_FACTOR * otd_above_baseline * reliability_weight
        return round(budget + premium, 2)


class Orchestrator:
    """Manages parallel negotiation sessions across multiple LSPs.

    Supports two initialisation modes:
      1. Legacy (simulator): pass ``simulators`` list -- channels are auto-created.
      2. Channel-based: pass ``channels`` dict -- for WhatsApp/Gmail/mixed.
    """

    def __init__(
        self,
        strategy_brain: StrategyBrain,
        simulators: list[LSPSimulator] | None = None,
        channels: dict[str, MessageChannel] | None = None,
        lsp_metadata: dict[str, dict[str, Any]] | None = None,
        lane_id: str = "",
        budget: float = 0.0,
        reliability_weight: float = 1.0,
        use_claude: bool = False,
        on_round_callback: RoundCallback | None = None,
        session_store: Any = None,
        reply_timeout: float = 300.0,
        sim_round_delay: float = 1.2,
    ) -> None:
        self.brain = strategy_brain
        self.lane_id = lane_id
        self.budget = budget
        self.reliability_weight = reliability_weight
        self.use_claude = use_claude and _USE_CLAUDE
        self.shared_memory = SharedMemory()
        self.sessions: dict[str, NegotiationSession] = {}
        self.closure_guard = ClosureGuard()
        self.on_round_callback = on_round_callback
        self.session_store = session_store
        self.reply_timeout = reply_timeout
        self.sim_round_delay = sim_round_delay
        self._results: list[dict[str, Any]] = []

        # Legacy simulator mode: auto-wrap in SimulatorChannel
        self._simulators: dict[str, LSPSimulator] = {}
        self._channels: dict[str, MessageChannel] = {}
        self._lsp_metadata: dict[str, dict[str, Any]] = lsp_metadata or {}
        self._channels_started = False

        if simulators is not None:
            from src.channels.simulator_channel import SimulatorChannel
            self._simulators = {s.lsp_id: s for s in simulators}
            sim_channel = SimulatorChannel(self._simulators)
            self._channels = {s.lsp_id: sim_channel for s in simulators}
            # Auto-populate metadata from simulators
            for s in simulators:
                self._lsp_metadata.setdefault(s.lsp_id, {
                    "name": s.name,
                    "initial_quote": s.initial_price,
                    "on_time_pct": s.on_time_pct,
                })
        elif channels is not None:
            self._channels = channels

    # ------------------------------------------------------------------
    # Helper: fire callback safely
    # ------------------------------------------------------------------

    async def _emit(self, session: NegotiationSession, event: str) -> None:
        """Fire the on_round_callback if set, swallowing exceptions."""
        if self.on_round_callback:
            try:
                await self.on_round_callback(session, event)
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"Callback error ({event}): {exc}")

    # ------------------------------------------------------------------
    # Session initialisation
    # ------------------------------------------------------------------

    def _init_sessions(self) -> None:
        """Create a NegotiationSession for each LSP."""
        for lsp_id in self._channels:
            meta = self._lsp_metadata.get(lsp_id, {})
            initial_quote = meta.get("initial_quote", self.budget * 1.2)
            on_time_pct = meta.get("on_time_pct", 90.0)
            name = meta.get("name", lsp_id)

            strategy = self.brain.get_strategy(
                lsp_id=lsp_id,
                lane_id=self.lane_id,
                budget=self.budget,
                quoted_price=initial_quote,
            )

            channel = self._channels[lsp_id]
            channel_type = channel.__class__.__name__.replace("Channel", "").lower()

            session = NegotiationSession(
                lsp_id=lsp_id,
                lsp_name=name,
                lane_id=self.lane_id,
                persona=strategy.persona,
                target_price=strategy.target_price,
                budget=self.budget,
                zopa_low=strategy.zopa_low,
                zopa_high=strategy.zopa_high,
                initial_quote=initial_quote,
                lsp_current_price=initial_quote,
                channel_type=channel_type,
            )
            self.sessions[lsp_id] = session

    # ------------------------------------------------------------------
    # Core negotiation loop for a single LSP
    # ------------------------------------------------------------------

    async def _negotiate_single(self, lsp_id: str) -> NegotiationSession:
        """Run the negotiation loop for a single LSP."""
        session = self.sessions[lsp_id]
        channel = self._channels[lsp_id]
        meta = self._lsp_metadata.get(lsp_id, {})
        on_time_pct = meta.get("on_time_pct", 90.0)

        # Starting offer: aggressive to force multi-round negotiation
        session.current_offer = round(
            session.zopa_low * 0.75 + session.target_price * 0.25, 2
        )

        # Notify dashboard that this session has started
        await self._emit(session, EVENT_SESSION_START)

        for round_num in range(1, MAX_ROUNDS + 1):
            session.round_num = round_num

            # Generate counter-offer message text
            last_lsp_msg = ""
            if session.history:
                last_lsp_msg = session.history[-1].get("lsp_message", "")

            if self.use_claude:
                msg = generate_counter_offer(
                    lsp_message=last_lsp_msg or str(session.initial_quote),
                    current_offer=session.lsp_current_price,
                    target_price=session.target_price,
                    persona=session.persona,
                    history=[{"role": "system", "text": str(h)} for h in session.history[-5:]],
                )
            else:
                msg = generate_counter_offer_offline(
                    lsp_message=last_lsp_msg,
                    current_offer=session.lsp_current_price,
                    target_price=session.target_price,
                    persona=session.persona,
                )

            session.last_our_message = msg

            # Persist state before sending (for real channels, we may wait hours)
            session.status = "waiting_for_reply"
            if self.session_store:
                self.session_store.save_session(session.to_dict(), session.channel_type)
                self.session_store.log_message(
                    session_id=f"{session.lane_id}_{session.lsp_id}",
                    direction="outbound",
                    channel_type=session.channel_type,
                    body=msg,
                )

            # Notify: our offer is being sent
            await self._emit(session, EVENT_OFFER_SENT)

            # Send offer via channel
            lsp_prev_price = session.lsp_current_price
            await channel.send(
                lsp_id=lsp_id,
                message=msg,
                metadata={"offer_price": session.current_offer, "round": round_num},
            )

            # Pacing delay for simulator mode (makes the demo visually lively)
            if session.channel_type == "simulator" and self.sim_round_delay > 0:
                await asyncio.sleep(self.sim_round_delay)

            # Wait for LSP response
            try:
                reply = await channel.receive(
                    lsp_id=lsp_id,
                    timeout=self.reply_timeout,
                )
            except asyncio.TimeoutError:
                session.status = "timeout"
                logger.warning(f"{lsp_id}: timed out waiting for reply (round {round_num})")
                await self._emit(session, EVENT_SESSION_END)
                break

            session.status = "active"
            session.last_lsp_message = reply.text

            # Notify: reply received
            await self._emit(session, EVENT_REPLY_RECEIVED)

            # Log inbound message
            if self.session_store:
                self.session_store.log_message(
                    session_id=f"{session.lane_id}_{session.lsp_id}",
                    direction="inbound",
                    channel_type=session.channel_type,
                    body=reply.text,
                    raw_payload=reply.raw_payload,
                )

            # Extract price and acceptance from reply
            if reply.raw_payload and "new_price" in reply.raw_payload:
                # Simulator channel includes structured data
                new_price = reply.raw_payload["new_price"]
                accepted = reply.raw_payload.get("accepted", False)
            else:
                # Real channel: extract from text
                from src.price_extractor import detect_acceptance, extract_price
                extracted = extract_price(reply.text, use_claude=self.use_claude)
                new_price = extracted if extracted is not None else session.lsp_current_price
                accepted = detect_acceptance(reply.text)

            # Update shared memory
            self.shared_memory.update(lsp_id, lsp_prev_price, new_price)

            # Sentiment analysis
            if self.use_claude:
                sentiment = analyze_sentiment(reply.text)
            else:
                sentiment = analyze_sentiment_offline(reply.text)
            session.sentiment_log.append(sentiment)

            # Record history
            round_record = {
                "round": round_num,
                "our_offer": session.current_offer,
                "lsp_price": new_price,
                "lsp_message": reply.text,
                "our_message": msg,
                "sentiment": sentiment,
                "accepted": accepted,
            }
            session.history.append(round_record)

            # Persist round
            if self.session_store:
                self.session_store.save_round(
                    f"{session.lane_id}_{session.lsp_id}", round_record
                )

            # Fire round-complete callback so dashboard can refresh
            await self._emit(session, EVENT_ROUND_COMPLETE)

            # Check acceptance
            if accepted:
                acceptable = self.closure_guard.is_acceptable(
                    final_price=new_price,
                    budget=self.budget,
                    on_time_pct=on_time_pct,
                    reliability_weight=self.reliability_weight,
                )
                if acceptable:
                    session.status = "accepted"
                    session.final_price = new_price
                    session.savings = round(session.initial_quote - new_price, 2)
                else:
                    session.status = "rejected"
                    session.final_price = new_price

                if self.session_store:
                    self.session_store.update_session_status(
                        f"{session.lane_id}_{session.lsp_id}",
                        session.status,
                        session.final_price,
                        session.savings,
                    )
                await self._emit(session, EVENT_SESSION_END)
                break

            # Adjust our next offer
            session.lsp_current_price = new_price
            gap = new_price - session.current_offer
            flex = self.shared_memory.lsp_flexibility.get(lsp_id, 0.15)
            step_fraction = 0.20 + flex * 0.15
            session.current_offer = round(
                session.current_offer + gap * step_fraction, 2
            )
            max_price = self.closure_guard.max_acceptable_price(
                self.budget, on_time_pct, self.reliability_weight
            )
            session.current_offer = min(session.current_offer, max_price)

        if session.status in ("active", "waiting_for_reply"):
            session.status = "timeout"
            if self.session_store:
                self.session_store.update_session_status(
                    f"{session.lane_id}_{session.lsp_id}", "timeout"
                )
            await self._emit(session, EVENT_SESSION_END)

        return session

    # ------------------------------------------------------------------
    # Top-level runners
    # ------------------------------------------------------------------

    async def run(self) -> list[NegotiationSession]:
        """Run all negotiations in parallel and return completed sessions."""
        # Start channels if not already started
        if not self._channels_started:
            unique_channels = set(self._channels.values())
            for ch in unique_channels:
                await ch.start()
            self._channels_started = True

        self._init_sessions()

        tasks = [self._negotiate_single(lsp_id) for lsp_id in self.sessions]
        results = await asyncio.gather(*tasks)

        self._results = []
        for session in results:
            meta = self._lsp_metadata.get(session.lsp_id, {})
            self._results.append({
                "lsp_id": session.lsp_id,
                "lsp_name": session.lsp_name,
                "lane_id": session.lane_id,
                "persona": session.persona,
                "initial_quote": session.initial_quote,
                "final_price": session.final_price,
                "savings": session.savings,
                "status": session.status,
                "rounds": session.round_num,
                "on_time_pct": meta.get("on_time_pct", 90.0),
                "channel_type": session.channel_type,
            })

        return list(results)

    async def shutdown(self) -> None:
        """Stop all channels gracefully."""
        if self._channels_started:
            unique_channels = set(self._channels.values())
            for ch in unique_channels:
                await ch.stop()
            self._channels_started = False

    def run_sync(self) -> list[NegotiationSession]:
        """Synchronous wrapper for run()."""
        return asyncio.run(self.run())

    def get_results_summary(self) -> dict[str, Any]:
        """Return aggregate negotiation results."""
        accepted = [r for r in self._results if r["status"] == "accepted"]
        total_savings = sum(r["savings"] for r in accepted)
        avg_savings_pct = 0.0
        if accepted:
            avg_savings_pct = sum(
                r["savings"] / r["initial_quote"] * 100 for r in accepted
            ) / len(accepted)

        return {
            "total_lsps": len(self._results),
            "accepted_deals": len(accepted),
            "rejected_deals": sum(1 for r in self._results if r["status"] == "rejected"),
            "timeout_deals": sum(1 for r in self._results if r["status"] == "timeout"),
            "total_savings": round(total_savings, 2),
            "avg_savings_pct": round(avg_savings_pct, 1),
            "best_deal": min(accepted, key=lambda x: x["final_price"]) if accepted else None,
            "details": self._results,
        }


if __name__ == "__main__":
    from generate_data import LSP_PROFILES, LANE_BASE_PRICES, BUDGET_PER_LANE
    from src.lsp_simulator import create_simulators_from_profiles

    brain = StrategyBrain()
    brain.load_data()
    brain.train()

    lane = "Lane_A"
    sims = create_simulators_from_profiles(LSP_PROFILES, LANE_BASE_PRICES, lane)

    orch = Orchestrator(
        strategy_brain=brain,
        simulators=sims,
        lane_id=lane,
        budget=BUDGET_PER_LANE[lane],
        sim_round_delay=0,  # no delay for CLI
    )
    sessions = orch.run_sync()
    summary = orch.get_results_summary()

    print(f"\n{'='*60}")
    print(f"NEGOTIATION SUMMARY -- {lane}")
    print(f"{'='*60}")
    print(f"Accepted: {summary['accepted_deals']}/{summary['total_lsps']}")
    print(f"Total Savings: {summary['total_savings']:.2f}")
    print(f"Avg Savings: {summary['avg_savings_pct']:.1f}%")
    if summary["best_deal"]:
        bd = summary["best_deal"]
        print(f"Best Deal: {bd['lsp_name']} @ {bd['final_price']:.2f}")
