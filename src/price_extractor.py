"""
Price Extractor – extracts numeric prices from free-text LSP messages.

Strategy: fast regex patterns first, then Claude extraction_agent fallback.
"""

from __future__ import annotations

import re
from typing import Optional


# Common price patterns in negotiation replies
_PRICE_PATTERNS = [
    # Currency symbol + number: $1,234.56 or Rs. 1234 or INR 1200
    r'(?:Rs\.?|INR|USD|\$|EUR|€|₹)\s*([\d,]+(?:\.\d{1,2})?)',
    # Number + "per shipment/trip/load/unit"
    r'([\d,]+(?:\.\d{1,2})?)\s*(?:per\s+(?:shipment|trip|load|unit|container))',
    # "price/rate/offer/quote/cost" followed by number
    r'(?:price|rate|offer|quote|cost)[:\s]*(?:Rs\.?|INR|USD|\$|₹)?\s*([\d,]+(?:\.\d{1,2})?)',
    # "we can do/offer/accept" followed by number
    r'(?:we\s+can\s+(?:do|offer|accept))[:\s]*(?:Rs\.?|\$|₹)?\s*([\d,]+(?:\.\d{1,2})?)',
    # "best/final/revised" price followed by number
    r'(?:best|final|revised|lowest)[:\s]+(?:Rs\.?|\$|₹)?\s*([\d,]+(?:\.\d{1,2})?)',
    # Number followed by currency
    r'([\d,]+(?:\.\d{1,2})?)\s*(?:Rs|INR|USD|EUR)',
]

# Phrases that strongly indicate acceptance
_ACCEPT_PHRASES = [
    "agree", "accepted", "deal", "confirmed", "works for us",
    "let's proceed", "we accept", "we agree", "sounds good",
    "happy to confirm", "approve", "go ahead",
]

# Phrases that strongly indicate rejection / walking away
_REJECT_PHRASES = [
    "cannot accept", "we decline", "no deal", "walk away",
    "not possible", "too low", "final offer", "cannot go lower",
]


def extract_price(text: str, use_claude: bool = False) -> Optional[float]:
    """Extract a numeric price from an LSP's response text.

    Args:
        text: The raw message text from the LSP.
        use_claude: Whether to fall back to Claude API for extraction.

    Returns:
        Extracted price as a float, or None if no price could be found.
    """
    for pattern in _PRICE_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            price_str = match.group(1).replace(",", "")
            try:
                value = float(price_str)
                # Sanity check: reject implausible prices
                if 10 < value < 1_000_000:
                    return value
            except ValueError:
                continue

    # Claude fallback using existing extraction agent
    if use_claude:
        try:
            from src.extraction_agent import extract_quote
            result = extract_quote(text)
            if "quoted_price" in result and result["quoted_price"] is not None:
                return float(result["quoted_price"])
        except Exception:
            pass

    return None


def detect_acceptance(text: str) -> bool:
    """Heuristic: detect if the LSP accepted our offer."""
    text_lower = text.lower()
    return any(phrase in text_lower for phrase in _ACCEPT_PHRASES)


def detect_rejection(text: str) -> bool:
    """Heuristic: detect if the LSP is walking away."""
    text_lower = text.lower()
    return any(phrase in text_lower for phrase in _REJECT_PHRASES)


if __name__ == "__main__":
    # Quick test
    samples = [
        "We can do $1,100 per shipment for this lane.",
        "Our revised rate is Rs. 1,080. Let me know.",
        "Best we can offer is 950 USD per container.",
        "That works for us! We accept $1,050.",
        "We cannot go below 1200. This is our final offer.",
    ]
    for s in samples:
        price = extract_price(s)
        accepted = detect_acceptance(s)
        rejected = detect_rejection(s)
        print(f"Price={price} | Accept={accepted} | Reject={rejected} | \"{s[:60]}\"")
