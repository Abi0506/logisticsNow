# Swarm Intelligence Negotiator (SIN)

AI-powered parallel logistics rate negotiation system that simultaneously negotiates with multiple Logistics Service Providers (LSPs), adapts strategies based on behavior, and balances cost with service reliability.

Supports **three communication channels**: local Simulator (demo), WhatsApp (Twilio), and Gmail (SMTP/IMAP).

## Architecture

```
logisticsNow/
├── generate_data.py          # Synthetic data generator
├── run_negotiation.py        # CLI entry point (all channels)
├── run_server.py             # Unified FastAPI webhook server + negotiator
├── app.py                    # Streamlit dashboard (live animation)
├── requirements.txt
├── config/
│   └── lsp_contacts.json     # LSP contact details (phones, emails)
├── data/                     # Generated CSV datasets
│   ├── historical_bids.csv
│   ├── lsp_profiles.csv
│   └── budgets.csv
├── logs/                     # Negotiation run logs (JSON)
└── src/
    ├── extraction_agent.py   # Claude-powered quote parsing
    ├── strategy_brain.py     # ZOPA engine + persona selection
    ├── tactical_agent.py     # Counter-offer generation + sentiment analysis
    ├── lsp_simulator.py      # Rule-based LSP behavior simulation
    ├── orchestrator.py       # Async parallel negotiation manager
    ├── price_extractor.py    # Regex + Claude price extraction
    ├── session_store.py      # SQLite persistence for multi-day negotiations
    ├── config_loader.py      # Config file parser with $ENV_VAR expansion
    ├── webhook_server.py     # FastAPI webhook server for Twilio
    └── channels/
        ├── base.py           # Abstract MessageChannel interface
        ├── simulator_channel.py  # Local LSP simulator backend
        ├── whatsapp_channel.py   # Twilio WhatsApp channel
        └── gmail_channel.py      # SMTP/IMAP email channel
```

## Key Components

| Module | Purpose |
|---|---|
| **Strategy Brain** | Trains a regression model on historical bids to predict LSP reservation prices. Computes ZOPA (Zone of Possible Agreement) and selects negotiation personas. |
| **Tactical Agent** | Generates counter-offer messages using Claude API (or offline rule-based fallback). Performs sentiment analysis on LSP replies. |
| **LSP Simulator** | Simulates LSP negotiation behavior with three personality types (aggressive, moderate, soft), rich varied message templates, and configurable concession rates. |
| **Orchestrator** | Manages parallel async negotiations across all LSPs. Maintains shared memory for cross-agent learning. Applies ClosureGuard to validate final deals. Emits live events for dashboard animation. |
| **Extraction Agent** | Parses unstructured quote text (emails) into structured JSON using Claude. |
| **Channel Layer** | Pluggable communication backends: Simulator (instant), WhatsApp (Twilio API + webhooks), Gmail (SMTP send + IMAP poll). |

## Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Set API Key (Optional)

The Claude API key is needed for AI-powered counter-offers and sentiment analysis. Without it, the system uses offline rule-based fallbacks that work well for demos.

```bash
# Windows
set ANTHROPIC_API_KEY=your-key-here

# Linux/Mac
export ANTHROPIC_API_KEY=your-key-here
```

### 3. Channel Configuration (for WhatsApp / Gmail)

**WhatsApp (Twilio):**
```bash
set TWILIO_ACCOUNT_SID=your-sid
set TWILIO_AUTH_TOKEN=your-token
set TWILIO_WHATSAPP_FROM=+14155238886
```

**Gmail:**
```bash
set GMAIL_ADDRESS=you@gmail.com
set GMAIL_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
```

LSP contact details (phone numbers, email addresses) are configured in `config/lsp_contacts.json`. The config loader automatically expands `$ENV_VAR` placeholders from your environment.

## Usage

### Generate Synthetic Data

```bash
python generate_data.py
```

Creates three CSV files in `data/`:
- `historical_bids.csv` -- 1,000 historical bid records (10 LSPs x 5 lanes x 20 records)
- `lsp_profiles.csv` -- LSP attributes (flexibility, response time, OTD performance)
- `budgets.csv` -- Manufacturer budget per lane

### Run Negotiation (CLI)

```bash
# Simulator mode (default -- instant, no external deps)
python run_negotiation.py --lane Lane_A

# With Claude API for tactical messaging
python run_negotiation.py --lane Lane_A --use-claude

# WhatsApp mode
python run_negotiation.py --channel whatsapp --config config/lsp_contacts.json

# Gmail mode
python run_negotiation.py --channel gmail --config config/lsp_contacts.json

# Mixed channels (each LSP uses its preferred channel)
python run_negotiation.py --channel mixed --config config/lsp_contacts.json
```

### Launch Dashboard (Recommended)

```bash
streamlit run app.py
```

The dashboard provides:
- **Channel selection** -- switch between Simulator, WhatsApp, and Gmail directly from the sidebar
- **Live animated negotiation** -- watch round-by-round as offers and counter-offers happen in real time
- **Chat bubble interface** -- see the actual negotiation messages exchanged with each LSP
- **Real-time status** -- pulsing indicators show which negotiations are active, accepted, or timed out
- **KPI metrics** -- accepted deals, total savings, average savings percentage
- **ZOPA visualization** -- interactive chart showing the negotiation zone for any LSP
- **Offer history** -- round-by-round price trajectory charts
- **Sentiment trail** -- per-round sentiment analysis of LSP responses
- **Reliability-weighted scoring** -- ranks deals considering both price and on-time delivery
- **Interactive controls** -- adjust budget, reliability weight, market demand, and LSP count

### Launch Webhook Server (for WhatsApp with Twilio)

```bash
python run_server.py --lane Lane_A --config config/lsp_contacts.json
```

This starts both a FastAPI webhook server (port 8000) and the negotiation orchestrator. Configure Twilio to POST incoming WhatsApp messages to `https://<your-domain>:8000/webhooks/whatsapp`.

## How It Works

1. **Data & Model Training** -- Historical bid data trains a LinearRegression model to predict each LSP's reservation price (the lowest they'll accept).

2. **Strategy Computation** -- For each LSP-lane pair, the ZOPA engine computes the negotiation zone. A persona is selected based on market competition and LSP flexibility:
   - **Aggressive Cutter** -- >3 competing LSPs on the lane
   - **Collaborative Partner** -- LSP flexibility score > 0.7
   - **Balanced Negotiator** -- default fallback

3. **Parallel Negotiation** -- The orchestrator launches async negotiation sessions for all LSPs simultaneously. Each session:
   - Generates a counter-offer using the selected persona
   - Sends it via the configured channel (Simulator / WhatsApp / Gmail)
   - Waits for the LSP response (instant for simulator, up to 24h for real channels)
   - Analyzes the response sentiment
   - Updates shared memory with observed concession patterns
   - Adjusts the next offer based on learned flexibility
   - Emits live events so the dashboard can update in real time

4. **Closure Guard** -- Before finalizing any deal, validates that the price falls within budget + reliability premium (higher OTD performance allows a higher price ceiling).

## Demo Script

For a hackathon presentation:

1. Run `streamlit run app.py`
2. Leave channel on **Simulator (Demo)** and click **Run Negotiation**
3. Watch the live animation -- point out:
   - The pulsing green dots showing active negotiations
   - Chat bubbles updating in real time as each round completes
   - Price trajectory charts converging towards agreement
   - Different LSP personalities reflected in their messages
4. After completion, walk through:
   - KPI metrics (savings, deal rate)
   - Expand individual LSP conversations to see full chat history
   - Sentiment trail per LSP (how tone changes over rounds)
   - ZOPA analysis -- show where the final price landed in the zone
5. Re-run with adjusted **reliability weight** to show how the system accepts different deals
6. Switch to **WhatsApp** or **Gmail** in the sidebar to demonstrate real-channel integration (show config fields)

## Expected Output

A typical simulator run produces:
- **80-100% of LSPs** reach accepted deals
- **10-25% average savings** from initial quotes
- **~8-10 seconds** total negotiation time for 5 LSPs (with animation pacing)
- Rich, varied negotiation messages reflecting LSP personalities
- Logs saved as JSON in `logs/` folder
