# Swarm Intelligence Negotiator (SIN)

AI-powered parallel logistics rate negotiation system that simultaneously negotiates with multiple Logistics Service Providers (LSPs), adapts strategies based on behavior, and balances cost with service reliability.

## Architecture

```
logisticsNow/
├── generate_data.py          # Synthetic data generator
├── run_negotiation.py        # CLI entry point
├── app.py                    # Streamlit dashboard
├── requirements.txt
├── data/                     # Generated CSV datasets
│   ├── historical_bids.csv
│   ├── lsp_profiles.csv
│   └── budgets.csv
├── logs/                     # Negotiation run logs (JSON)
└── src/
    ├── __init__.py
    ├── extraction_agent.py   # Claude-powered quote parsing
    ├── strategy_brain.py     # ZOPA engine + persona selection
    ├── tactical_agent.py     # Counter-offer generation + sentiment analysis
    ├── lsp_simulator.py      # Rule-based LSP behavior simulation
    └── orchestrator.py       # Async parallel negotiation manager
```

## Key Components

| Module | Purpose |
|---|---|
| **Strategy Brain** | Trains a regression model on historical bids to predict LSP reservation prices. Computes ZOPA (Zone of Possible Agreement) and selects negotiation personas. |
| **Tactical Agent** | Generates counter-offer messages using Claude API (or offline rule-based fallback). Performs sentiment analysis on LSP replies. |
| **LSP Simulator** | Simulates LSP negotiation behavior with three personality types (aggressive, moderate, soft) and configurable concession rates. |
| **Orchestrator** | Manages parallel async negotiations across all LSPs. Maintains shared memory for cross-agent learning. Applies ClosureGuard to validate final deals. |
| **Extraction Agent** | Parses unstructured quote text (emails) into structured JSON using Claude. |

## Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Set API Key

The Claude API key is needed for the Extraction Agent, sentiment analysis, and counter-offer generation. Without it, the system uses offline rule-based fallbacks.

```bash
# Windows
set ANTHROPIC_API_KEY=your-key-here

# Linux/Mac
export ANTHROPIC_API_KEY=your-key-here
```

## Usage

### Generate Synthetic Data

```bash
python generate_data.py
```

Creates three CSV files in `data/`:
- `historical_bids.csv` – 1,000 historical bid records (10 LSPs x 5 lanes x 20 records)
- `lsp_profiles.csv` – LSP attributes (flexibility, response time, OTD performance)
- `budgets.csv` – Manufacturer budget per lane

### Run Negotiation (CLI)

```bash
# All lanes, all LSPs, offline mode
python run_negotiation.py

# Single lane
python run_negotiation.py --lane Lane_A

# With Claude API for tactical messaging
python run_negotiation.py --use-claude

# Custom reliability weight
python run_negotiation.py --reliability-weight 1.5 --max-lsps 5
```

### Launch Dashboard

```bash
streamlit run app.py
```

The dashboard provides:
- **Live negotiation view** – split-screen showing 3 LSPs negotiating simultaneously
- **KPI metrics** – accepted deals, total savings, average savings percentage
- **ZOPA visualization** – interactive chart showing the negotiation zone for any LSP
- **Offer history** – round-by-round price trajectory
- **Reliability-weighted scoring** – ranks deals considering both price and on-time delivery
- **Interactive controls** – adjust budget, reliability weight, and market demand factor

## How It Works

1. **Data & Model Training** – Historical bid data trains a LinearRegression model to predict each LSP's reservation price (the lowest they'll accept).

2. **Strategy Computation** – For each LSP-lane pair, the ZOPA engine computes the negotiation zone. A persona is selected based on market competition and LSP flexibility.

3. **Parallel Negotiation** – The orchestrator launches async negotiation sessions for all LSPs simultaneously. Each session:
   - Generates a counter-offer using the selected persona
   - Sends it to the LSP simulator
   - Analyzes the LSP's response sentiment
   - Updates shared memory with observed concession patterns
   - Adjusts the next offer based on learned flexibility

4. **Closure Guard** – Before finalizing any deal, validates that the price falls within budget + reliability premium (higher OTD performance allows a higher price ceiling).

## Demo Script

For a hackathon presentation:

1. Run `python generate_data.py` to show data generation
2. Run `python run_negotiation.py --lane Lane_A` to demonstrate CLI negotiation
3. Launch `streamlit run app.py` and walk through:
   - Click "Run Negotiation" with default settings
   - Show the live negotiation charts
   - Adjust the reliability weight slider and re-run to show how it affects which deals get accepted
   - Select different LSPs in the ZOPA analysis to explain the negotiation zone
   - Point out the shared memory effect: LSPs that concede more get less aggressive counter-offers

## Expected Output

A typical run produces:
- **60-80% of LSPs** reach accepted deals
- **10-25% average savings** from initial quotes
- **Sub-second** total negotiation time for 10 LSPs across 5 lanes
- Logs saved as JSON in `logs/` folder
