"""
Extraction Agent – parses unstructured quote text (emails, messages)
into structured JSON using Claude API.
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


EXTRACTION_SYSTEM_PROMPT = (
    "You are a logistics data extraction assistant. "
    "Extract structured quote information from the raw text provided by the user. "
    "Return ONLY valid JSON with these fields: "
    "lsp_name (str), lane_id (str), quoted_price (float), "
    "vehicle_type (str), delivery_days (int), valid_until (str, ISO date). "
    "If a field is missing, use null. Do NOT include any text outside the JSON object."
)


def extract_quote(raw_text: str) -> dict[str, Any]:
    """Send raw quote text to Claude and return a structured dict.

    Args:
        raw_text: Unstructured text such as an email body containing a logistics quote.

    Returns:
        Dictionary with keys: lsp_name, lane_id, quoted_price, vehicle_type,
        delivery_days, valid_until.
    """
    client = _get_client()

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=512,
        system=EXTRACTION_SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": raw_text},
        ],
    )

    response_text = message.content[0].text.strip()

    # Handle potential markdown code fences in response
    if response_text.startswith("```"):
        lines = response_text.split("\n")
        response_text = "\n".join(lines[1:-1])

    try:
        return json.loads(response_text)
    except json.JSONDecodeError as exc:
        return {
            "error": f"Failed to parse Claude response: {exc}",
            "raw_response": response_text,
        }


# --------------- Demo / test helper ---------------

SAMPLE_EMAIL = """
Hi team,

Following up on our discussion, here's our quote for the Mumbai-Delhi corridor:

Carrier: SwiftHaul Logistics
Route: Lane_A (Mumbai → Delhi)
Price: ₹1,150 per shipment
Vehicle: 20-ft container truck
Estimated delivery: 3 days
This quote is valid until 2025-03-15.

Let me know if you'd like to proceed.

Best regards,
Rajesh Kumar
SwiftHaul Logistics
"""

if __name__ == "__main__":
    result = extract_quote(SAMPLE_EMAIL)
    print(json.dumps(result, indent=2))
