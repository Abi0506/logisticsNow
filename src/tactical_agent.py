"""
Tactical Agent – generates counter-offer messages and performs sentiment analysis
on incoming LSP messages, both powered by Claude API.
"""

import json
import os
from typing import Any

try:
    from anthropic import Anthropic
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False

_client = None


def _get_client():
    global _client
    if not _ANTHROPIC_AVAILABLE:
        raise ImportError("anthropic package is not installed. Run: pip install anthropic")
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise EnvironmentError("ANTHROPIC_API_KEY environment variable is not set.")
        _client = Anthropic(api_key=api_key)
    return _client


# ---------- Persona prompt templates ----------

PERSONA_PROMPTS: dict[str, str] = {
    "Aggressive Cutter": (
        "You are an aggressive logistics procurement negotiator. "
        "Your primary goal is to minimize cost. Be direct, cite market data, "
        "reference competing bids, and push hard for the lowest possible price. "
        "Maintain professionalism but apply strong pressure."
    ),
    "Collaborative Partner": (
        "You are a collaborative logistics procurement negotiator. "
        "You value long-term partnerships and reliability. "
        "Acknowledge the LSP's strengths, suggest win-win terms, "
        "and negotiate firmly but with warmth. Offer volume commitments "
        "in exchange for better pricing."
    ),
    "Balanced Negotiator": (
        "You are a balanced logistics procurement negotiator. "
        "You seek fair pricing while maintaining good relationships. "
        "Present facts, be reasonable in your counter-offers, "
        "and show willingness to compromise within your budget."
    ),
}


def generate_counter_offer(
    lsp_message: str,
    current_offer: float,
    target_price: float,
    persona: str,
    history: list[dict[str, Any]] | None = None,
) -> str:
    """Generate a counter-offer message using Claude.

    Args:
        lsp_message: Latest message from the LSP.
        current_offer: The LSP's current asking price.
        target_price: Our ideal target price.
        persona: One of the three negotiation personas.
        history: Prior negotiation exchanges for context.

    Returns:
        A ready-to-send counter-offer message string.
    """
    client = _get_client()

    persona_instruction = PERSONA_PROMPTS.get(persona, PERSONA_PROMPTS["Balanced Negotiator"])

    history_block = ""
    if history:
        history_lines = []
        for entry in history[-5:]:  # last 5 turns for context window efficiency
            role = entry.get("role", "unknown")
            text = entry.get("text", "")
            history_lines.append(f"[{role}] {text}")
        history_block = "\n\nNegotiation history (most recent):\n" + "\n".join(history_lines)

    user_prompt = (
        f"LSP's latest message:\n\"{lsp_message}\"\n\n"
        f"Their current asking price: {current_offer:.2f}\n"
        f"Our target price: {target_price:.2f}\n"
        f"{history_block}\n\n"
        "Compose a counter-offer message (2-4 sentences). "
        "Include a specific price number. Do NOT reveal our target price directly."
    )

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=300,
        system=persona_instruction,
        messages=[{"role": "user", "content": user_prompt}],
    )

    return message.content[0].text.strip()


def analyze_sentiment(message: str) -> dict[str, Any]:
    """Analyze the sentiment of an LSP message using Claude.

    Returns:
        Dictionary with keys: sentiment (str), confidence (float 0-1),
        flexibility_signal (str), key_phrases (list[str]).
    """
    client = _get_client()

    system_prompt = (
        "You are a negotiation sentiment analyst. Analyze the sentiment of the "
        "logistics provider's message. Return ONLY valid JSON with these fields: "
        "sentiment (one of: positive, neutral, negative, frustrated), "
        "confidence (float 0 to 1), "
        "flexibility_signal (one of: willing_to_negotiate, firm, walking_away), "
        "key_phrases (list of up to 3 notable phrases from the message)."
    )

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=256,
        system=system_prompt,
        messages=[{"role": "user", "content": message}],
    )

    response_text = response.content[0].text.strip()
    if response_text.startswith("```"):
        lines = response_text.split("\n")
        response_text = "\n".join(lines[1:-1])

    try:
        return json.loads(response_text)
    except json.JSONDecodeError:
        return {
            "sentiment": "neutral",
            "confidence": 0.5,
            "flexibility_signal": "firm",
            "key_phrases": [],
            "raw_response": response_text,
        }


# ---------- Offline fallback for demo without API key ----------

def generate_counter_offer_offline(
    lsp_message: str,
    current_offer: float,
    target_price: float,
    persona: str,
    history: list[dict[str, Any]] | None = None,
) -> str:
    """Rule-based counter-offer generation (no API required)."""
    gap = current_offer - target_price
    counter_price = current_offer - gap * 0.4  # concede 40% of the gap

    if persona == "Aggressive Cutter":
        return (
            f"Thank you for your quote. However, given competitive bids we've received, "
            f"we can offer {counter_price:.2f}. We need you to sharpen your pricing "
            f"to move forward."
        )
    elif persona == "Collaborative Partner":
        return (
            f"We appreciate your partnership and quality service. "
            f"To make this work within our budget, could you consider {counter_price:.2f}? "
            f"We're happy to discuss volume commitments in return."
        )
    else:
        return (
            f"Thanks for the quote. After reviewing market rates, "
            f"we'd like to propose {counter_price:.2f}. "
            f"Let us know if this works for you."
        )


def analyze_sentiment_offline(message: str) -> dict[str, Any]:
    """Rule-based sentiment analysis (no API required)."""
    message_lower = message.lower()

    negative_words = ["cannot", "impossible", "final", "no further", "walk away", "regret"]
    positive_words = ["happy", "agree", "flexible", "willing", "accommodate", "partnership"]

    neg_count = sum(1 for w in negative_words if w in message_lower)
    pos_count = sum(1 for w in positive_words if w in message_lower)

    if neg_count > pos_count:
        sentiment = "negative"
        flexibility = "firm" if neg_count < 3 else "walking_away"
    elif pos_count > neg_count:
        sentiment = "positive"
        flexibility = "willing_to_negotiate"
    else:
        sentiment = "neutral"
        flexibility = "firm"

    return {
        "sentiment": sentiment,
        "confidence": 0.7,
        "flexibility_signal": flexibility,
        "key_phrases": [],
    }


if __name__ == "__main__":
    # Quick offline demo
    sample_msg = "We appreciate your interest but our costs are firm at 1300. We may have some room on delivery timelines."
    print("Sentiment:", analyze_sentiment_offline(sample_msg))
    print()
    print("Counter-offer (Aggressive):")
    print(generate_counter_offer_offline(sample_msg, 1300, 1050, "Aggressive Cutter"))
