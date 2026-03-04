"""
Config Loader – reads lsp_contacts.json with $ENV_VAR expansion.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any


def _expand_env_vars(raw: str) -> str:
    """Replace $ENV_VAR placeholders in a string with their environment values."""
    def _replace(match: re.Match) -> str:
        var_name = match.group(1)
        return os.environ.get(var_name, match.group(0))
    return re.sub(r'\$([A-Z_][A-Z0-9_]*)', _replace, raw)


def load_config(config_path: str | Path = "config/lsp_contacts.json") -> dict[str, Any]:
    """Load and parse the LSP contacts configuration file.

    All string values containing $VAR_NAME patterns are expanded from
    the environment, so secrets stay out of the JSON file.
    """
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path, encoding="utf-8") as f:
        raw = f.read()

    expanded = _expand_env_vars(raw)
    config = json.loads(expanded)

    # Build convenience lookups
    contacts = config.get("contacts", [])
    config["_by_lsp_id"] = {c["lsp_id"]: c for c in contacts}
    config["_whatsapp_contacts"] = {
        c["lsp_id"]: c["channels"]["whatsapp"]
        for c in contacts
        if "whatsapp" in c.get("channels", {})
    }
    config["_gmail_contacts"] = {
        c["lsp_id"]: c["channels"]["gmail"]
        for c in contacts
        if "gmail" in c.get("channels", {})
    }

    return config


def get_lsp_metadata(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Extract lsp_id -> {name, initial_quote, on_time_pct, preferred_channel} map."""
    result = {}
    for contact in config.get("contacts", []):
        result[contact["lsp_id"]] = {
            "name": contact["name"],
            "initial_quote": contact.get("initial_quote", 1200.0),
            "on_time_pct": contact.get("on_time_pct", 90.0),
            "preferred_channel": contact.get("preferred_channel", "simulator"),
            "lane_ids": contact.get("lane_ids", []),
        }
    return result
