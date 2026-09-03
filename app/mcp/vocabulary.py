"""Controlled labelling vocabulary, sourced from the canonical enums so the MCP
never drifts from what the API accepts."""

from __future__ import annotations

from app.schemas.recommendations import HueKeys, ReadingAbilityKey


def vocabulary() -> dict:
    """Valid hue keys (dominant reading feel) and reading-ability tiers
    (text difficulty, SPOT easiest → HARRY_POTTER hardest)."""
    return {
        "hues": [h.value for h in HueKeys],
        "reading_abilities": [r.value for r in ReadingAbilityKey],
    }
