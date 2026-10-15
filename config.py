"""
Configuration settings for WhatsApp Gemini Agent.

Centralizes all environment variables, timeouts, rate limits, and thresholds.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Base paths
PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
LOGS_DIR = PROJECT_ROOT / "logs"

# Load .env file early so env overrides apply
load_dotenv(PROJECT_ROOT / ".env")

_env_state_file = os.getenv("STATE_FILE")
STATE_FILE: Path = (
    Path(_env_state_file).resolve() if _env_state_file else PROJECT_ROOT / "state.json"
)

# WhatsApp Agent Platform API Spec
WHATSAPP_BASE_URL: str = "https://api.whatsapp.com/agent/v1"
WHATSAPP_API_TOKEN: str = os.getenv("WHATSAPP_API_TOKEN", "")

# Rate Limits (per WhatsApp Agent Platform spec)
# Spec allows 12 requests/min for /messages & /statuses; 15 requests/min for /updates
MAX_SENDS_PER_MINUTE: int = 12
MAX_POLLS_PER_MINUTE: int = 15

# Polling constraints (per spec: limit <= 100, timeout 0-25s)
POLL_TIMEOUT: int = 15
POLL_LIMIT: int = 50
NETWORK_BUFFER_TIMEOUT: int = 10  # Client read timeout = POLL_TIMEOUT + 10

# Message constraints (per spec)
MAX_REPLY_LENGTH: int = 4096

# LLM & Conversation Settings
# Supports comma-separated keys in GEMINI_API_KEYS, single GEMINI_API_KEY, or multiple GEMINI_API_KEY lines in .env
_seen_keys: set[str] = set()
GEMINI_API_KEYS: list[str] = []


def _register_key(candidate: str) -> None:
    candidate = candidate.strip()
    if candidate and candidate not in _seen_keys and len(GEMINI_API_KEYS) < 20:
        _seen_keys.add(candidate)
        GEMINI_API_KEYS.append(candidate)


# 1. Comma-separated GEMINI_API_KEYS env var
_raw_keys = os.getenv("GEMINI_API_KEYS", "")
for _part in _raw_keys.split(","):
    _register_key(_part)

# 2. Parse .env directly for multiple GEMINI_API_KEY / GEMINI_API_KEYS lines if present
_env_file = PROJECT_ROOT / ".env"
if _env_file.exists():
    try:
        with open(_env_file, encoding="utf-8") as _ef:
            for _line in _ef:
                _line = _line.strip()
                if not _line or _line.startswith("#") or "=" not in _line:
                    continue
                _k, _v = _line.split("=", 1)
                _k = _k.strip()
                _v = _v.strip().strip("'\"")
                if _k in ("GEMINI_API_KEY", "GEMINI_API_KEYS"):
                    for _item in _v.split(","):
                        _register_key(_item)
    except Exception:
        pass

# 3. Fallback to standard os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEYS and os.getenv("GEMINI_API_KEY"):
    _register_key(os.getenv("GEMINI_API_KEY", ""))

# Backward compatibility single key
GEMINI_API_KEY: str = GEMINI_API_KEYS[0] if GEMINI_API_KEYS else ""

# Primary model and model fallback list (overridable from .env)
# Prioritize verified working flash models, newest first; 2.5 models as last fallbacks
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
_raw_models = os.getenv(
    "GEMINI_MODELS",
    "gemini-3.5-flash,gemini-3.8-flash,gemini-3.7-flash,gemini-2.5-flash",
)
GEMINI_MODELS: list[str] = [m.strip() for m in _raw_models.split(",") if m.strip()]
if GEMINI_MODEL not in GEMINI_MODELS:
    GEMINI_MODELS.insert(0, GEMINI_MODEL)

# Multi-key rate-limit cooldown
KEY_COOLDOWN_SECONDS: float = 65.0

AGENT_NAME: str = os.getenv("AGENT_NAME", "Sufi_vela")
MAX_CONVERSATION_TURNS: int = 6  # Last 6 conversation turns
ANSWER_CACHE_TTL_SECONDS: int = 300  # 5 minutes TTL for identical query cache
TOP_K_KNOWLEDGE_CHUNKS: int = 3

# Logging
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FILE_PATH: Path = LOGS_DIR / "agent.log"
LOG_MAX_BYTES: int = 5 * 1024 * 1024  # 5 MB rotating
LOG_BACKUP_COUNT: int = 5


def mask_secret(secret: str, prefix_len: int = 4, suffix_len: int = 4) -> str:
    """Mask sensitive tokens for safe logging and status reporting."""
    if not secret:
        return "<EMPTY>"
    if len(secret) <= prefix_len + suffix_len:
        return "***"
    return f"{secret[:prefix_len]}...{secret[-suffix_len:]}"
