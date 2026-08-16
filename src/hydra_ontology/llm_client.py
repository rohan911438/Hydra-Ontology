"""Thin LLM wrapper. Everything else in this project talks to the LLM only
through generate_json() so the provider (currently Gemini) is swappable
without touching extraction/resolution/conflict/query logic."""
from __future__ import annotations

import json
import logging
import re
import socket
import threading
import time

from .config import CONFIG

# The google-genai SDK logs a one-time warning on the direct
# generate_content() path recommending Chat.send_message() instead -- noise
# for this project's use case (one-shot JSON extraction calls, not a chat
# session), and it interleaves with this module's own printed Cypher/output
# in the CLI. Quiet that specific logger only, not warnings generally.
logging.getLogger("google_genai.models").setLevel(logging.ERROR)

_client = None

# Observed live on this machine: DNS resolution and IPv4 connectivity are
# both fine (confirmed with nslookup/ping), but the Gemini SDK's HTTPS
# client hangs indefinitely rather than erroring -- consistent with this
# network having broken/blackholed IPv6 while generativelanguage.googleapis.com
# advertises IPv6 addresses first and the client doesn't fall back quickly.
# Forcing IPv4-only resolution for this process is the standard fix.
_orig_getaddrinfo = socket.getaddrinfo


def _ipv4_only_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)


socket.getaddrinfo = _ipv4_only_getaddrinfo

# The free tier enforces a per-MINUTE request cap (confirmed live: 15 req/min
# for gemini-3.5-flash-lite), not a per-day one -- so simple client-side
# pacing between calls avoids most 429s instead of just retrying into them.
_MIN_INTERVAL_SECONDS = 4.5
_pacing_lock = threading.Lock()
_last_call_at = 0.0

_RETRY_DELAY_RE = re.compile(r"retryDelay['\"]?\s*:\s*['\"]?(\d+(?:\.\d+)?)s")


def _get_client():
    global _client
    if _client is None:
        from google import genai
        from google.genai import types

        if not CONFIG.gemini_api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. Put it in a local .env (see .env.example)."
            )
        # Explicit timeout so a hung/black-holed connection fails fast and
        # gets retried, instead of blocking generate_json() indefinitely
        # (observed live: the underlying client has no timeout by default).
        _client = genai.Client(
            api_key=CONFIG.gemini_api_key,
            http_options=types.HttpOptions(timeout=20_000),  # milliseconds
        )
    return _client


def _pace() -> None:
    global _last_call_at
    with _pacing_lock:
        wait = _MIN_INTERVAL_SECONDS - (time.time() - _last_call_at)
        if wait > 0:
            time.sleep(wait)
        _last_call_at = time.time()


def generate_json(prompt: str, *, max_retries: int = 5) -> dict | list:
    """Sends a prompt, asking Gemini to reply with JSON only, and parses it.
    Retries on transient failures / malformed JSON, honoring the server's
    suggested retryDelay on a 429 rather than a blind short backoff."""
    from google.genai import types

    client = _get_client()
    last_err: Exception | None = None
    for attempt in range(max_retries):
        _pace()
        try:
            resp = client.models.generate_content(
                model=CONFIG.gemini_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.0,
                ),
            )
            text = resp.text
            return json.loads(text)
        except Exception as exc:  # noqa: BLE001 - broad on purpose, we retry
            last_err = exc
            msg = str(exc)
            if "PerDayPerProjectPerModel" in msg:
                # The whole model's daily quota is gone -- no per-minute
                # retryDelay will fix that today, so fail fast instead of
                # burning 5 attempts x ~60s each waiting on a dead bucket.
                raise RuntimeError(
                    f"Gemini model '{CONFIG.gemini_model}' has hit its daily quota: {exc}"
                ) from exc
            m = _RETRY_DELAY_RE.search(msg)
            if m:
                time.sleep(float(m.group(1)) + 1.0)
            elif "RESOURCE_EXHAUSTED" in msg or "429" in msg:
                time.sleep(20.0)
            else:
                time.sleep(min(2**attempt, 8))
    raise RuntimeError(f"Gemini call failed after {max_retries} attempts: {last_err}")
