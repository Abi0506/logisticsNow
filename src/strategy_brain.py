"""
Strategy Brain – ZOPA engine, reservation price prediction, and persona selection.

Provides the core decision-making logic that determines *what* to aim for
and *how* to negotiate with each LSP.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import LabelEncoder

DATA_DIR = Path(__file__).parent.parent / "data"


@dataclass
class Strategy:
    target_price: float
    persona: str
    predicted_reservation_price: float
    zopa_low: float
    zopa_high: float


class StrategyBrain:
    """Computes negotiation strategies including ZOPA bounds and persona selection."""

    def __init__(self) -> None:
        self._model: LinearRegression | None = None
        self._lane_enc = LabelEncoder()
        self._lsp_enc = LabelEncoder()
        self._profiles: pd.DataFrame | None = None
        self._bids: pd.DataFrame | None = None
        self._budgets: dict[str, float] = {}
        self._trained = False

    # ---- data loading & training ----

    def load_data(
        self,
        bids_path: str | Path | None = None,
        profiles_path: str | Path | None = None,
        budgets_path: str | Path | None = None,
    ) -> None:
        bids_path = bids_path or DATA_DIR / "historical_bids.csv"
        profiles_path = profiles_path or DATA_DIR / "lsp_profiles.csv"
        budgets_path = budgets_path or DATA_DIR / "budgets.csv"

        self._bids = pd.read_csv(bids_path)
        self._profiles = pd.read_csv(profiles_path)
        budgets_df = pd.read_csv(budgets_path)
        self._budgets = dict(zip(budgets_df["lane_id"], budgets_df["budget"]))

    def train(self) -> None:
        """Train a LinearRegression model to predict accepted (reservation) prices."""
        if self._bids is None or self._profiles is None:
            raise RuntimeError("Call load_data() before train().")

        df = self._bids.merge(self._profiles, on="lsp_id", how="left")

        # Encode categoricals
        df["lane_enc"] = self._lane_enc.fit_transform(df["lane_id"])
        df["lsp_enc"] = self._lsp_enc.fit_transform(df["lsp_id"])

        features = [
            "lane_enc",
            "lsp_enc",
            "quoted_price",
            "on_time_delivery_pct",
            "response_time_hours",
            "avg_flexibility",
        ]
        X = df[features].values
        y = df["accepted_price"].values

        self._model = LinearRegression()
        self._model.fit(X, y)
        self._trained = True

        preds = self._model.predict(X)
        mae = np.mean(np.abs(preds - y))
        print(f"[StrategyBrain] Reservation-price model trained. MAE = {mae:.2f}")

    # ---- prediction ----

    def predict_reservation_price(
        self, lsp_id: str, lane_id: str, quoted_price: float
    ) -> float:
        """Predict the LSP's likely minimum acceptable price."""
        if not self._trained or self._model is None:
            raise RuntimeError("Model not trained. Call train() first.")

        profile = self._profiles[self._profiles["lsp_id"] == lsp_id].iloc[0]

        lane_enc = self._lane_enc.transform([lane_id])[0]
        lsp_enc = self._lsp_enc.transform([lsp_id])[0]

        X = np.array([[
            lane_enc,
            lsp_enc,
            quoted_price,
            profile["hist_on_time"] * 100,
            profile["typical_response_hours"],
            profile["avg_flexibility"],
        ]])
        return float(self._model.predict(X)[0])

    # ---- ZOPA ----

    def compute_zopa(
        self,
        lsp_id: str,
        lane_id: str,
        quoted_price: float,
        budget: float | None = None,
    ) -> tuple[float, float]:
        """Return (zopa_low, zopa_high) – the zone of possible agreement.

        zopa_low  = predicted reservation price of the LSP (their floor)
        zopa_high = manufacturer budget for the lane (our ceiling)
        """
        reservation = self.predict_reservation_price(lsp_id, lane_id, quoted_price)
        budget = budget or self._budgets.get(lane_id, quoted_price)
        return (round(reservation, 2), round(budget, 2))

    # ---- persona selection ----

    def select_persona(self, lsp_id: str, lane_id: str) -> str:
        """Rule-based persona selection."""
        if self._bids is None or self._profiles is None:
            return "Balanced Negotiator"

        # Count distinct LSPs serving this lane
        lsps_on_lane = self._bids[self._bids["lane_id"] == lane_id]["lsp_id"].nunique()

        if lsps_on_lane > 3:
            return "Aggressive Cutter"

        profile = self._profiles[self._profiles["lsp_id"] == lsp_id]
        if not profile.empty and profile.iloc[0]["avg_flexibility"] > 0.7:
            return "Collaborative Partner"

        return "Balanced Negotiator"

    # ---- combined strategy ----

    def get_strategy(
        self,
        lsp_id: str,
        lane_id: str,
        budget: float | None = None,
        market_demand_factor: float = 1.0,
        quoted_price: float | None = None,
    ) -> Strategy:
        """Return the full negotiation strategy for an LSP-lane pair.

        Args:
            lsp_id: Identifier of the LSP.
            lane_id: Identifier of the lane.
            budget: Override manufacturer budget for this lane.
            market_demand_factor: Multiplier (>1 means higher demand → less aggressive).
            quoted_price: The LSP's initial quoted price.  If None, use median
                          historical quoted price.
        """
        budget = budget or self._budgets.get(lane_id, 1500)

        if quoted_price is None:
            lane_bids = self._bids[
                (self._bids["lsp_id"] == lsp_id) & (self._bids["lane_id"] == lane_id)
            ]
            quoted_price = float(lane_bids["quoted_price"].median()) if not lane_bids.empty else budget * 1.2

        reservation = self.predict_reservation_price(lsp_id, lane_id, quoted_price)
        zopa_low, zopa_high = self.compute_zopa(lsp_id, lane_id, quoted_price, budget)

        persona = self.select_persona(lsp_id, lane_id)

        # Target price: midpoint of ZOPA adjusted by demand factor and persona
        midpoint = (zopa_low + zopa_high) / 2

        persona_shift = {
            "Aggressive Cutter": -0.10,
            "Collaborative Partner": 0.05,
            "Balanced Negotiator": 0.0,
        }
        shift = persona_shift.get(persona, 0.0)
        target = midpoint * (1 + shift) * market_demand_factor
        target = max(target, zopa_low)
        target = min(target, budget)

        return Strategy(
            target_price=round(target, 2),
            persona=persona,
            predicted_reservation_price=round(reservation, 2),
            zopa_low=zopa_low,
            zopa_high=zopa_high,
        )


if __name__ == "__main__":
    brain = StrategyBrain()
    brain.load_data()
    brain.train()

    for lsp_id in ["LSP_01", "LSP_05", "LSP_06"]:
        s = brain.get_strategy(lsp_id, "Lane_A")
        print(f"{lsp_id} | Lane_A | persona={s.persona} target={s.target_price} "
              f"ZOPA=[{s.zopa_low}, {s.zopa_high}]")
