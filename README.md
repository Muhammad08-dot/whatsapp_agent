# 🤖 WhatsApp Gemini Agent (Sufi_vela)

An intelligent, production-grade conversational AI assistant connecting the **WhatsApp Agent Platform** (`https://api.whatsapp.com/agent/v1`) directly to **Google Gemini LLM** using modern Google GenAI SDK with multi-key failover and resilient state handling.

---

## 🌟 Key Features

- **WhatsApp Agent Platform Spec Compliance**: Built strictly around `https://api.whatsapp.com/agent/v1` with Bearer auth, `user:<id>` validation, 4096-character reply splitting, and status receipt handling.
- **Sequential Multi-Key Rotation**: Supports 1 to 20 Gemini API keys in `.env`. Stays on the first working key, and fails over sequentially when encountering 429 rate limits with automatic 65s or Pacific midnight quota cooldowns.
- **Model Fallback Chain**: Dynamically switches models (`gemini-3.5-flash`, `gemini-3.8-flash`, `gemini-3.7-flash`, etc.) when models return 404 NOT_FOUND. Remembers unavailable `(key, model)` pairs without redundant retries.
- **Poison-Message Immunity**: A failing message is caught gracefully, logged with masked message IDs, answered with a courteous technical error response, and marked handled so batches never loop forever.
- **Sliding-Window Rate Limiting**: Enforces strict WhatsApp rate limits (12 requests/min for sends & statuses; 15 requests/min for long-polling).
- **Atomic State Persistence**: Tracks `next_offset` and handled message IDs in `state.json` atomically, committing offsets only after every message in a batch has been processed.
- **Graceful Shutdown**: Responsive signal handling (`SIGINT`, `SIGTERM`) exits cleanly between iterations, saves state atomically, and closes HTTP sessions.
- **Privacy & Secret Masking**: Masks sensitive API keys, bearer tokens, and `wamid` message IDs across all status reports and logs.
- **Fast Test Suite**: Comprehensive mocked test suite executing in under 2.5 seconds.

---

## 🏗️ Architecture One-Liner

Long-poll WhatsApp `/updates` -> Deduplicate via `state.json` -> Retrieve context via `data/` -> Generate reply via sequential-failover `GeminiClient` -> Dispatch to WhatsApp `/messages` -> Commit offset atomically.

---

## 📁 Project Structure

```text
whatsapp_gemini_agent/
├── .github/
│   └── workflows/ci.yml     # Automated CI (Python 3.13, ruff, black, pytest)
├── data/                    # Knowledge documents & bot instructions
│   ├── profile.md           # Bot identity and personality
│   ├── commands.md          # Facts and command descriptions
│   └── faq.md               # User-editable FAQ template
├── tests/                   # Pytest automated test suite
│   ├── conftest.py          # Shared fixtures (fake keys, mock clients, tmp state)
│   ├── test_agent.py        # Agent orchestration, commands, and poison message tests
│   ├── test_gemini_client.py# Multi-key rotation, 404 caching, and cooldown tests
│   ├── test_rate_limiter.py # Sliding-window rate limiter tests
│   ├── test_state_manager.py# Atomic file persistence and deduplication tests
│   └── test_whatsapp_client.py # WhatsApp transport and spec compliance tests
├── logs/                    # Rotating log files (agent.log)
├── .dockerignore            # Docker build ignore rules
├── .env.example             # Environment configuration template
├── .gitignore               # Secret and cache ignore rules
├── Dockerfile               # Production container image (python:3.13-slim, non-root)
├── agent.py                 # Core orchestration logic
├── config.py                # Centralized settings and constants
├── gemini_client.py         # Gemini SDK client with sequential key failover
├── knowledge_base.py        # Keyword relevance document search
├── main.py                  # Poll loop, signal handling, and entry point
├── models.py                # Typed dataclasses (IncomingMessage, StatusUpdate)
├── prompts.py               # System prompts and instructions
├── rate_limiter.py          # Token-bucket sliding window rate limiter
├── requirements.txt         # Production dependencies
├── requirements-dev.txt     # Development and testing dependencies
├── state_manager.py         # Atomic state persistence
└── whatsapp_client.py       # WhatsApp Agent Platform transport client
```

---

## ⚙️ Environment Variables

All settings are configured via `.env` (copy from `.env.example`):

| Variable | Required | Default | Description |
|:---|:---:|:---|:---|
| `WHATSAPP_API_TOKEN` | **Yes** | — | Bearer token from WhatsApp mobile app (Settings > Agents > Chat info > API key). |
| `GEMINI_API_KEYS` | **Yes** | — | Comma-separated Gemini API keys (supports 1 to 20 keys). |
| `GEMINI_MODEL` | No | `gemini-3.5-flash` | Primary Gemini model name. |
| `GEMINI_MODELS` | No | `gemini-3.5-flash,gemini-3.8-flash,gemini-3.7-flash,gemini-2.5-flash` | Fallback models chain. |
| `STATE_FILE` | No | `state.json` | Path to persistent state file (mount to a volume for Docker). |
| `AGENT_NAME` | No | `Sufi_vela` | Name of the assistant displayed in logs and prompts. |
| `LOG_LEVEL` | No | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |

> [!WARNING]
> **CRITICAL SECURITY RULE:** NEVER commit `.env` to Git or share raw contents. All logs and status reports automatically mask sensitive tokens as `abcd...wxyz`.

> [!CAUTION]
> **SINGLE INSTANCE ONLY:** NEVER run two agent instances simultaneously on the same WhatsApp API token. Running multiple pollers causes HTTP 409 Conflict errors, duplicate replies, and out-of-order offset advancement.

---

## 🤖 Bot Commands

Users or bot admins can issue direct slash commands in the WhatsApp chat:

- `/status`: Sends an instant diagnostic report showing total loaded keys, active key index, per-key health (OK, Cooldown seconds remaining, Disabled), and available models. All secrets remain masked.
- `/reset`: Clears the user's active conversational memory history, starting a clean conversational context.

---

## 🚀 Setup & Local Execution

### 1. Install Dependencies
```powershell
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

### 2. Configure Environment
Copy `.env.example` to `.env` and insert your actual keys:
```powershell
cp .env.example .env
```

### 3. Run Agent
```powershell
python main.py
```

### 4. Run Verification Suite
```powershell
pytest -v
ruff check .
black --check .
```

---

## 🐳 Running 24/7 with Docker or on a VPS

To run the agent 24/7 reliably, use Docker with an external volume to persist `state.json` across container restarts:

```bash
# 1. Build the Docker image
docker build -t whatsapp-gemini-agent .

# 2. Run container with environment file and persistent data volume
docker run -d \
  --name whatsapp-gemini-agent \
  --restart unless-stopped \
  --env-file .env \
  -e STATE_FILE=/app/data/state.json \
  -v whatsapp_agent_data:/app/data \
  whatsapp-gemini-agent
```

To view live masked logs:
```bash
docker logs -f whatsapp-gemini-agent
```

---

## 🛠️ Troubleshooting & Status Codes

| Error / Code | Root Cause | Automatic Agent Action & Resolution |
|:---|:---|:---|
| **204 No Content** | Poll timeout (no new messages) | Normal WhatsApp long-polling behavior; agent continues polling. |
| **401 / 403 (WhatsApp)** | Invalid or expired WhatsApp token | Check `WHATSAPP_API_TOKEN` in `.env`. Regenerate key in WhatsApp mobile app. |
| **401 / 403 (Gemini)** | `API_KEY_INVALID` or permission error | Key is permanently disabled for this run. Other valid keys continue serving. |
| **404 NOT_FOUND (Gemini)** | Model retired or unavailable | Marked in `unavailable_pairs`; agent tries next key on same model or falls back to next model. |
| **409 Conflict (WhatsApp)** | Another polling process is active | **Stop all other running instances!** Only one poller can query an agent token at a time. |
| **429 Rate Limit (Gemini)** | RPM or daily TPM/RPD quota reached | Key enters 65s cooldown (or until Pacific midnight for daily limits); agent immediately fails over to next key. |
| **429 Rate Limit (WhatsApp)** | Outbound/polling frequency exceeded | Token bucket rate limiter automatically throttles and backs off with jitter. |
| **500 / 503 Service Unavailable** | Upstream WhatsApp/Gemini temporary error | Retried once carefully with exponential backoff; if persistent, friendly Urdu error returned. |
