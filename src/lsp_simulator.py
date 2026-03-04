"""
LSP Simulator – mimics Logistics Service Provider negotiation behaviour.

Each simulator has a personality (aggressive, moderate, soft) that determines
how it responds to counter-offers. No real APIs – everything runs locally.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any


@dataclass
class LSPSimulator:
    """Simulates an LSP's negotiation behaviour.

    Attributes:
        lsp_id: Unique identifier.
        name: Human-readable name.
        personality: One of 'aggressive', 'moderate', 'soft'.
        initial_price: The LSP's opening quoted price.
        reservation_price: The lowest price the LSP will accept (hidden floor).
        concession_rate: Fraction of the gap the LSP concedes per round.
        on_time_pct: Historical on-time delivery percentage (0-100).
        round_count: Number of negotiation rounds completed.
        current_price: The LSP's latest price (starts at initial_price).
        history: List of (round, our_offer, lsp_response_price) tuples.
    """

    lsp_id: str
    name: str
    personality: str  # 'aggressive' | 'moderate' | 'soft'
    initial_price: float
    reservation_price: float
    on_time_pct: float = 90.0
    concession_rate: float | None = None

    # Mutable state
    round_count: int = field(default=0, init=False)
    current_price: float = field(default=0.0, init=False)
    history: list[dict[str, Any]] = field(default_factory=list, init=False)
    _accepted: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        self.current_price = self.initial_price
        if self.concession_rate is None:
            defaults = {"aggressive": 0.08, "moderate": 0.18, "soft": 0.30}
            self.concession_rate = defaults.get(self.personality, 0.15)

    @property
    def is_done(self) -> bool:
        return self._accepted or self.round_count >= 10

    def respond(self, offer: float, current_price: float | None = None) -> dict[str, Any]:
        """Process the manufacturer's offer and return a response.

        Args:
            offer: The manufacturer's proposed price.
            current_price: Ignored (kept for API compatibility); uses internal state.

        Returns:
            Dict with keys: new_price (float), message (str), accepted (bool).
        """
        self.round_count += 1

        # If offer meets or exceeds our reservation price → accept
        if offer >= self.reservation_price:
            self._accepted = True
            self.current_price = offer
            self.history.append({
                "round": self.round_count,
                "manufacturer_offer": offer,
                "lsp_response_price": offer,
                "accepted": True,
            })
            return {
                "new_price": offer,
                "message": self._accept_message(offer),
                "accepted": True,
            }

        # Calculate gap and new price
        gap = self.current_price - self.reservation_price
        concession = gap * self.concession_rate

        # Add personality-specific noise
        noise_factor = {
            "aggressive": random.uniform(-0.02, 0.02),
            "moderate": random.uniform(-0.01, 0.03),
            "soft": random.uniform(0.0, 0.04),
        }.get(self.personality, 0.0)
        concession *= (1 + noise_factor)

        new_price = max(self.current_price - concession, self.reservation_price)
        new_price = round(new_price, 2)

        # If the offer is very close (within 2%), accept
        if abs(new_price - offer) / new_price < 0.02:
            self._accepted = True
            final_price = round((new_price + offer) / 2, 2)
            self.current_price = final_price
            self.history.append({
                "round": self.round_count,
                "manufacturer_offer": offer,
                "lsp_response_price": final_price,
                "accepted": True,
            })
            return {
                "new_price": final_price,
                "message": self._accept_message(final_price),
                "accepted": True,
            }

        self.current_price = new_price
        self.history.append({
            "round": self.round_count,
            "manufacturer_offer": offer,
            "lsp_response_price": new_price,
            "accepted": False,
        })

        return {
            "new_price": new_price,
            "message": self._counter_message(offer, new_price),
            "accepted": False,
        }

    # ---------- message templates ----------

    def _accept_message(self, price: float) -> str:
        templates = {
            "aggressive": f"Fine. We'll accept {price:.2f}, but this is our absolute floor.",
            "moderate": f"We're happy to agree on {price:.2f}. Looking forward to working together.",
            "soft": f"That works for us! We confirm {price:.2f}. Excited about this partnership!",
        }
        return templates.get(self.personality, f"Agreed at {price:.2f}.")

    def _counter_message(self, their_offer: float, our_new_price: float) -> str:
        gap_pct = ((our_new_price - their_offer) / our_new_price) * 100

        if self.personality == "aggressive":
            if gap_pct > 15:
                return (
                    f"Your offer of {their_offer:.2f} is well below our operating costs. "
                    f"The best we can do right now is {our_new_price:.2f}. "
                    f"We have other clients willing to pay more."
                )
            return (
                f"We've reviewed your offer of {their_offer:.2f}. "
                f"Our revised price is {our_new_price:.2f}. This is very competitive."
            )
        elif self.personality == "soft":
            return (
                f"Thank you for your offer of {their_offer:.2f}. "
                f"We'd like to suggest {our_new_price:.2f} – we're flexible "
                f"and happy to discuss further."
            )
        else:  # moderate
            return (
                f"We appreciate the offer of {their_offer:.2f}. "
                f"After reviewing costs, we can come down to {our_new_price:.2f}. "
                f"Let us know your thoughts."
            )

    def reset(self) -> None:
        """Reset the simulator for a fresh negotiation."""
        self.round_count = 0
        self.current_price = self.initial_price
        self.history.clear()
        self._accepted = False


def create_simulators_from_profiles(
    profiles: list[dict[str, Any]],
    lane_base_prices: dict[str, float],
    lane_id: str,
) -> list[LSPSimulator]:
    """Factory: create one LSPSimulator per profile for a given lane.

    The simulator's personality is assigned based on flexibility:
      flexibility < 0.4  → aggressive
      0.4 ≤ flex < 0.7   → moderate
      flexibility ≥ 0.7  → soft
    """
    base = lane_base_prices.get(lane_id, 1200)
    simulators: list[LSPSimulator] = []

    for p in profiles:
        flex = p["avg_flexibility"]
        if flex < 0.4:
            personality = "aggressive"
        elif flex < 0.7:
            personality = "moderate"
        else:
            personality = "soft"

        # Initial quoted price: higher for less flexible LSPs
        initial = base * (1.35 - 0.35 * flex) + random.uniform(-50, 50)
        # Reservation (floor): realistic cost floor — LSPs won't go below this
        reservation = base * (0.88 + 0.10 * (1 - flex)) + random.uniform(-15, 15)

        simulators.append(LSPSimulator(
            lsp_id=p["lsp_id"],
            name=p["name"],
            personality=personality,
            initial_price=round(initial, 2),
            reservation_price=round(reservation, 2),
            on_time_pct=p.get("hist_on_time", 0.9) * 100,
        ))

    return simulators


if __name__ == "__main__":
    sim = LSPSimulator(
        lsp_id="LSP_TEST",
        name="Test Carrier",
        personality="moderate",
        initial_price=1300.0,
        reservation_price=1000.0,
    )

    offer = 900.0
    for _ in range(8):
        result = sim.respond(offer)
        print(f"Round {sim.round_count}: offer={offer:.2f} -> lsp={result['new_price']:.2f} "
              f"accepted={result['accepted']}")
        if result["accepted"]:
            break
        # Manufacturer raises offer
        offer = (offer + result["new_price"]) / 2
