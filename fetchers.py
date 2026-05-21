"""
fetchers.py – pulls balance/usage data from each service API.

Each fetcher returns a dict.  On error it returns {"error": "<msg>"}.
fetch_all() merges them under service keys.
"""

import os
import logging
from datetime import datetime

import requests

log = logging.getLogger(__name__)

TIMEOUT = 10  # seconds per request

# ── Anthropic API ─────────────────────────────────────────────────────────────

def fetch_anthropic() -> dict:
    """
    Retrieves credit balance and usage from the Anthropic Console API.
    Requires ANTHROPIC_ADMIN_KEY (an Anthropic Console/Admin key, NOT a regular
    API key – generate one at console.anthropic.com → Settings → API keys).
    """
    key = os.getenv("ANTHROPIC_ADMIN_KEY", "").strip()
    if not key:
        return {"error": "no key"}

    headers = {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
    }

    result: dict = {}

    # --- Credit balance --------------------------------------------------
    try:
        r = requests.get(
            "https://api.anthropic.com/v1/organizations/credits/balance",
            headers=headers,
            timeout=TIMEOUT,
        )
        if r.status_code == 200:
            data = r.json()
            # Balance is returned in micro-dollars; convert to dollars
            raw = data.get("balance_microdollars") or data.get("balance") or 0
            if raw > 1_000:      # micro-dollars
                result["credit_balance"] = raw / 1_000_000
            else:                # assume already dollars
                result["credit_balance"] = float(raw)
        else:
            log.warning(f"Anthropic balance: {r.status_code} {r.text[:120]}")
            result["error"] = f"HTTP {r.status_code}"
    except Exception as e:
        log.error(f"Anthropic balance fetch: {e}")
        result["error"] = str(e)[:40]

    # --- Usage (current month) -------------------------------------------
    try:
        now = datetime.utcnow()
        params = {
            "start_time": now.replace(day=1, hour=0, minute=0, second=0).isoformat() + "Z",
        }
        r = requests.get(
            "https://api.anthropic.com/v1/organizations/usage",
            headers=headers,
            params=params,
            timeout=TIMEOUT,
        )
        if r.status_code == 200:
            data = r.json()
            raw = data.get("total_cost_microdollars") or data.get("total_cost") or 0
            if raw > 1_000:
                result["monthly_usage"] = raw / 1_000_000
            else:
                result["monthly_usage"] = float(raw)
        # Silently ignore if this endpoint isn't available on the plan
    except Exception as e:
        log.warning(f"Anthropic usage fetch: {e}")

    # --- Tier / plan info ------------------------------------------------
    try:
        r = requests.get(
            "https://api.anthropic.com/v1/organizations",
            headers=headers,
            timeout=TIMEOUT,
        )
        if r.status_code == 200:
            data = r.json()
            result["tier"] = data.get("tier") or data.get("plan") or "—"
    except Exception:
        pass

    return result


# ── Claude.ai (Unofficial – session cookie) ───────────────────────────────────

def fetch_claude_ai() -> dict:
    """
    Claude.ai does not have an official public API for quota data.
    This fetcher uses the internal /api/account endpoint with your session cookie.
    Set CLAUDE_SESSION_KEY in .env to your __session cookie value from claude.ai.

    To get it:
      1. Log into claude.ai in Chrome/Firefox
      2. Open DevTools → Application → Cookies → https://claude.ai
      3. Copy the value of the '__session' cookie
    """
    session_key = os.getenv("CLAUDE_SESSION_KEY", "").strip()
    if not session_key:
        return {"error": "no session key", "plan": "—"}

    headers = {
        "Cookie": f"__session={session_key}",
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
        "Referer": "https://claude.ai/",
    }

    result: dict = {}

    try:
        r = requests.get(
            "https://claude.ai/api/account",
            headers=headers,
            timeout=TIMEOUT,
        )
        if r.status_code == 200:
            data = r.json()
            result["plan"] = (
                data.get("active_subscription", {}).get("plan_name")
                or data.get("billing_plan")
                or "Free"
            )
        elif r.status_code == 401:
            return {"error": "session expired", "plan": "—"}
        else:
            return {"error": f"HTTP {r.status_code}", "plan": "—"}
    except Exception as e:
        return {"error": str(e)[:40], "plan": "—"}

    # Usage / limits
    try:
        r = requests.get(
            "https://claude.ai/api/account/usage",
            headers=headers,
            timeout=TIMEOUT,
        )
        if r.status_code == 200:
            data = r.json()
            result["messages_used"]  = data.get("message_count") or data.get("messages_used")
            result["messages_limit"] = data.get("message_limit") or data.get("messages_limit")
            reset_at = data.get("reset_at") or data.get("resets_at")
            if reset_at:
                # Trim to just the date part if it's a full ISO timestamp
                result["resets_at"] = reset_at[:10]
    except Exception:
        pass

    return result


# ── ElevenLabs ────────────────────────────────────────────────────────────────

def fetch_elevenlabs() -> dict:
    """
    Uses the ElevenLabs API to retrieve character usage quota.
    Set ELEVENLABS_API_KEY in .env.
    """
    key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if not key:
        return {"error": "no key"}

    headers = {
        "xi-api-key": key,
        "Accept": "application/json",
    }

    result: dict = {}

    try:
        r = requests.get(
            "https://api.elevenlabs.io/v1/user",
            headers=headers,
            timeout=TIMEOUT,
        )
        if r.status_code == 200:
            data = r.json()
            sub = data.get("subscription", {})
            result["tier"]        = sub.get("tier") or sub.get("plan") or "—"
            result["chars_used"]  = sub.get("character_count")
            result["chars_limit"] = sub.get("character_limit")
            reset_ts = sub.get("next_character_count_reset_unix")
            if reset_ts:
                result["next_reset"] = datetime.utcfromtimestamp(reset_ts).strftime("%Y-%m-%d")
        elif r.status_code == 401:
            return {"error": "invalid key"}
        else:
            return {"error": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"error": str(e)[:40]}

    return result


# ── Combined ──────────────────────────────────────────────────────────────────

def fetch_all() -> dict:
    log.info("Fetching Anthropic…")
    anthropic = fetch_anthropic()
    log.info("Fetching Claude.ai…")
    claude_ai = fetch_claude_ai()
    log.info("Fetching ElevenLabs…")
    elevenlabs = fetch_elevenlabs()
    return {
        "anthropic":  anthropic,
        "claude_ai":  claude_ai,
        "elevenlabs": elevenlabs,
    }
