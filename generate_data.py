"""
Synthetic Data Generator for the Swarm Intelligence Negotiator.

Produces:
  - Historical bid data for 10 LSPs across 5 lanes
  - LSP profiles with flexibility scores and performance metrics
  - Manufacturer budget per lane

All outputs are saved as CSV files in the data/ folder.
"""

import os
import random
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 42
random.seed(SEED)
np.random.seed(SEED)

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

# ---------- Configuration ----------

LANES = ["Lane_A", "Lane_B", "Lane_C", "Lane_D", "Lane_E"]

LSP_PROFILES = [
    {"lsp_id": "LSP_01", "name": "SwiftHaul Logistics",   "avg_flexibility": 0.82, "typical_response_hours": 2.0,  "hist_on_time": 0.95},
    {"lsp_id": "LSP_02", "name": "PrimeFreight Co.",       "avg_flexibility": 0.45, "typical_response_hours": 6.0,  "hist_on_time": 0.88},
    {"lsp_id": "LSP_03", "name": "EcoTransit Partners",    "avg_flexibility": 0.73, "typical_response_hours": 3.5,  "hist_on_time": 0.92},
    {"lsp_id": "LSP_04", "name": "BlueArrow Shipping",     "avg_flexibility": 0.55, "typical_response_hours": 4.0,  "hist_on_time": 0.85},
    {"lsp_id": "LSP_05", "name": "NexGen Carriers",        "avg_flexibility": 0.90, "typical_response_hours": 1.5,  "hist_on_time": 0.97},
    {"lsp_id": "LSP_06", "name": "TruckStar Express",      "avg_flexibility": 0.30, "typical_response_hours": 8.0,  "hist_on_time": 0.78},
    {"lsp_id": "LSP_07", "name": "Atlas Freight Lines",    "avg_flexibility": 0.65, "typical_response_hours": 5.0,  "hist_on_time": 0.90},
    {"lsp_id": "LSP_08", "name": "VeloCity Movers",        "avg_flexibility": 0.78, "typical_response_hours": 2.5,  "hist_on_time": 0.93},
    {"lsp_id": "LSP_09", "name": "IronRoute Transport",    "avg_flexibility": 0.40, "typical_response_hours": 7.0,  "hist_on_time": 0.80},
    {"lsp_id": "LSP_10", "name": "GreenMile Logistics",    "avg_flexibility": 0.60, "typical_response_hours": 4.5,  "hist_on_time": 0.87},
]

# Base prices vary by lane (represents cost structure)
LANE_BASE_PRICES = {
    "Lane_A": 1000,
    "Lane_B": 1500,
    "Lane_C": 800,
    "Lane_D": 2000,
    "Lane_E": 1200,
}

BUDGET_PER_LANE = {
    "Lane_A": 1200,
    "Lane_B": 1700,
    "Lane_C": 950,
    "Lane_D": 2300,
    "Lane_E": 1400,
}


def _generate_historical_bids(n_records_per_combo: int = 20) -> pd.DataFrame:
    """Generate historical bid records for every LSP-lane combination."""
    rows: list[dict] = []
    start_date = datetime(2024, 1, 1)

    for lane_id in LANES:
        base = LANE_BASE_PRICES[lane_id]
        for profile in LSP_PROFILES:
            lsp_id = profile["lsp_id"]
            flex = profile["avg_flexibility"]
            on_time = profile["hist_on_time"]
            resp_hours = profile["typical_response_hours"]

            for i in range(n_records_per_combo):
                # Quoted price: base + noise; less-flexible LSPs quote higher
                noise_quote = np.random.normal(0, base * 0.10)
                quoted = base * (1.3 - 0.3 * flex) + noise_quote
                quoted = max(quoted, base * 0.7)

                # Accepted price: quoted * concession (more flexible → bigger concession)
                concession = np.random.uniform(0.80, 0.95) * (1 - 0.15 * flex)
                accepted = quoted * concession
                accepted = max(accepted, base * 0.6)

                otd = min(1.0, max(0.5, on_time + np.random.normal(0, 0.03)))
                resp = max(0.5, resp_hours + np.random.normal(0, 1.0))
                date = start_date + timedelta(days=random.randint(0, 365))

                rows.append({
                    "lane_id": lane_id,
                    "lsp_id": lsp_id,
                    "quoted_price": round(quoted, 2),
                    "accepted_price": round(accepted, 2),
                    "on_time_delivery_pct": round(otd * 100, 1),
                    "response_time_hours": round(resp, 1),
                    "date": date.strftime("%Y-%m-%d"),
                })

    return pd.DataFrame(rows)


def _generate_lsp_profiles() -> pd.DataFrame:
    return pd.DataFrame(LSP_PROFILES)


def _generate_budgets() -> pd.DataFrame:
    rows = [{"lane_id": k, "budget": v} for k, v in BUDGET_PER_LANE.items()]
    return pd.DataFrame(rows)


def generate_all() -> None:
    """Generate and persist all synthetic datasets."""
    bids_df = _generate_historical_bids()
    profiles_df = _generate_lsp_profiles()
    budgets_df = _generate_budgets()

    bids_df.to_csv(DATA_DIR / "historical_bids.csv", index=False)
    profiles_df.to_csv(DATA_DIR / "lsp_profiles.csv", index=False)
    budgets_df.to_csv(DATA_DIR / "budgets.csv", index=False)

    print(f"Generated {len(bids_df)} historical bid records  -> data/historical_bids.csv")
    print(f"Generated {len(profiles_df)} LSP profiles          -> data/lsp_profiles.csv")
    print(f"Generated {len(budgets_df)} budget entries          -> data/budgets.csv")


if __name__ == "__main__":
    generate_all()
