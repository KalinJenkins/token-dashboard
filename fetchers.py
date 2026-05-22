"""
fetchers.py

Anthropic:  pings /v1/models with a regular API key to verify liveness and
            capture live rate-limit headers (requests & tokens remaining).
            Credit balance is static from .env — updated manually after top-ups.

ElevenLabs: hits the documented /v1/user/subscription endpoint for live
            character usage, limit, tier, reset date, and overage.
"""

import os
import logging
from datetime import datetime

import requests

log = logging.getLogger(__name__)
TIMEOUT = 10


# ── Anthropic ─────────────────────────────────────────────────────────────────

def fetch_anthropic() -> dict:
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return {"error": "no key"}

    headers = {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
    }

    result: dict = {}

    # Ping /v1/models — cheap, no tokens consumed, exposes rate-limit headers
    try:
        r = requests.get(
            "https://api.anthropic.com/v1/models",
            headers=headers,
            timeout=TIMEOUT,
        )

        if r.status_code == 200:
            result["key_valid"] = True
        elif r.status_code == 401:
            return {"error": "invalid key", "key_valid": False}
        else:
            result["key_valid"] = False
            result["error"] = f"HTTP {r.status_code}"

        # Rate-limit headers are present on every response, valid or not
        def _int(h):
            v = r.headers.get(h)
            return int(v) if v and v.isdigit() else None

        result["req_limit"]     = _int("anthropic-ratelimit-requests-limit")
        result["req_remaining"] = _int("anthropic-ratelimit-requests-remaining")
        result["tok_limit"]     = _int("anthropic-ratelimit-tokens-limit")
        result["tok_remaining"] = _int("anthropic-ratelimit-tokens-remaining")

        # Reset times (ISO strings)
        result["req_reset"] = r.headers.get("anthropic-ratelimit-requests-reset")
        result["tok_reset"] = r.headers.get("anthropic-ratelimit-tokens-reset")

    except requests.RequestException as e:
        log.error(f"Anthropic fetch: {e}")
        return {"error": str(e)[:40], "key_valid": False}

    return result


# ── ElevenLabs ────────────────────────────────────────────────────────────────

def fetch_elevenlabs() -> dict:
    key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if not key:
        return {"error": "no key"}

    try:
        r = requests.get(
            "https://api.elevenlabs.io/v1/user/subscription",
            headers={"xi-api-key": key, "Accept": "application/json"},
            timeout=TIMEOUT,
        )
    except requests.RequestException as e:
        return {"error": str(e)[:40]}

    if r.status_code == 401:
        return {"error": "invalid key"}
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}"}

    d = r.json()

    result: dict = {
        "tier":        d.get("tier", "—"),
        "status":      d.get("status", "—"),
        "chars_used":  d.get("character_count"),
        "chars_limit": d.get("character_limit"),
    }

    reset_unix = d.get("next_character_count_reset_unix")
    if reset_unix:
        result["next_reset"] = datetime.utcfromtimestamp(reset_unix).strftime("%b %d")

    overage = d.get("current_overage", {})
    try:
        amt = float(overage.get("amount", 0))
        if amt > 0:
            result["overage"] = amt
            result["overage_currency"] = overage.get("currency", "usd").upper()
    except (ValueError, TypeError):
        pass

    return result


# ── Combined ──────────────────────────────────────────────────────────────────

def fetch_all() -> dict:
    log.info("Fetching Anthropic…")
    anthropic  = fetch_anthropic()
    log.info("Fetching ElevenLabs…")
    elevenlabs = fetch_elevenlabs()
    return {"anthropic": anthropic, "elevenlabs": elevenlabs}
