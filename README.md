---
title: WhatsApp Gemini Agent
emoji: 🤖
colorFrom: green
colorTo: blue
sdk: gradio
sdk_version: 5.20.0
app_file: app.py
pinned: false
---

# 🤖 WhatsApp Gemini Agent

[![CI Pipeline](https://github.com/Muhammad08-dot/whatsapp_agent/actions/workflows/ci.yml/badge.svg)](https://github.com/Muhammad08-dot/whatsapp_agent/actions)
[![Python Version](https://img.shields.io/badge/python-3.13%2B-blue.svg)](https://www.python.org/)
[![Code Style: Black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![Linting: Ruff](https://img.shields.io/badge/lint-ruff-orange.svg)](https://github.com/astral-sh/ruff)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

An enterprise-grade, high-availability conversational AI agent integrating the **WhatsApp Agent Platform** (`https://api.whatsapp.com/agent/v1`) with **Google Gemini LLM** using the official Google GenAI SDK. 

Engineered for 24/7 resilience with sequential multi-key rotation, dynamic model fallback chains, sliding-window rate limiting, and atomic state recovery.

---

## 🌟 Key Highlights

- **WhatsApp Agent Platform Compliant**: Strict adherence to the platform spec (Bearer authentication, `user:<id>` identity preservation, auto-splitting messages exceeding 4,096 characters, and status receipt processing).
- **Sequential Multi-Key Failover**: Automatically rotates across up to 20 Gemini API keys. Stays on the active key while healthy, and triggers automated 65-second (or Pacific midnight daily quota) cooldowns on HTTP 429 (`RESOURCE_EXHAUSTED`).
- **Dynamic Model Fallback Chain**: Caches unavailable `(key, model)` pairs on HTTP 404 and transitions gracefully across fallback models (`gemini-3.5-flash` → `gemini-3.8-flash` → `gemini-3.7-flash` → `gemini-2.5-flash`).
- **Poison-Message Resilience**: Isolated exception handling ensures that any single failing message is courteously answered, marked handled, and logged with masked IDs without blocking the batch or causing infinite replay loops.
- **Atomic Offset & State Persistence**: Guarantees zero dropped or duplicated messages across server restarts by advancing `next_offset` only after all messages in a batch are processed.
- **Sliding-Window Rate Limiting**: Built-in token bucket throttlers strictly enforce WhatsApp rate limits (12 calls/min for sends, 15 calls/min for long-polling).
- **Graceful Shutdown**: Intercepts `SIGINT` and `SIGTERM` to safely finish active messages, persist state, and cleanly close network sessions.
- **Enterprise Privacy**: Automated masking of credentials, tokens, and `wamid` message IDs across status reports and application logs.

---

## 🏗️ Architecture & Data Flow

```text
       ┌────────────────────────────────────────────────────────┐
       │                WhatsApp Agent Platform                 │
       │           (https://api.whatsapp.com/agent/v1)          │
       └──────────────┬───────────────────────────▲─────────────┘
                      │ Long-Poll (/updates)      │ Send (/messages)
                      ▼                           │
             ┌─────────────────┐         ┌────────┴─────────┐
             │ WhatsAppClient  │         │ Rate Limiter     │
             │   (Transport)   │         │  (12 req/min)    │
             └────────┬────────┘         └────────▲─────────┘
                      │ Messages                  │ Outbound Text
                      ▼                           │
             ┌────────────────────────────────────┴─────────┐
             │                 WhatsAppAgent                │
             │            (Core Orchestrator)               │
             └───┬────────────────────────────┬─────────────┘
                 │ State & Dedup              │ Context & Inference
                 ▼                            ▼
        ┌─────────────────┐          ┌──────────────────────┐
        │  StateManager   │          │     GeminiClient     │
        │  (state.json)   │          │ (Sequential Failover)│
        └─────────────────┘          └──────────┬───────────┘
                                                │
                                       ┌────────┴────────┐
                                       │ Google GenAI    │
                                       │ (1 to 20 Keys)  │
                                       └─────────────────┘
```

---

## 📁 Repository Structure

```text
whatsapp_gemini_agent/
├── .github/
│   └── workflows/ci.yml     # Automated CI testing on Python 3.13
├── data/                    # Knowledge base & persona documents
│   ├── commands.md          # Reference of bot commands and capabilities
│   ├── faq.md               # Customizable FAQ template
│   └── profile.md           # Bot personality, identity, and tone
├── tests/                   # Pytest automated test suite
│   ├── conftest.py          # Shared fixtures (mock clients, tmp state)
│   ├── test_agent.py        # Poison messages, commands, offset safety
│   ├── test_gemini_client.py# Key rotation, model fallbacks, cooldowns
│   ├── test_rate_limiter.py # Sliding-window rate limiting tests
│   ├── test_state_manager.py# Atomic JSON persistence tests
│   └── test_whatsapp_client.py # Transport, payload splitting, spec checks
├── logs/                    # Rotating application logs (ignored by git)
├── .dockerignore            # Docker exclusion rules
├── .env.example             # Clean environment configuration template
├── .gitignore               # Strict exclusion rules for secrets and caches
├── Dockerfile               # Production container definition (non-root)
├── Makefile                 # Common execution targets
├── README.md                # Project documentation
├── agent.py                 # Core orchestration loop
├── config.py                # Centralized configuration and constants
├── exceptions.py            # Custom exception hierarchy
├── gemini_client.py         # Gemini SDK client with sequential key failover
├── knowledge_base.py        # Document search & context injection
├── main.py                  # Process entry point, signal handlers, logging
├── models.py                # Typed dataclasses (IncomingMessage, StatusUpdate)
├── prompts.py               # Dynamic prompt assembly
├── pytest.ini               # Test runner configuration
├── rate_limiter.py          # Sliding-window rate limiter
├── requirements.txt         # Core production dependencies
├── requirements-dev.txt     # Linting and testing dependencies
├── ruff.toml                # Ruff linter configuration
├── run.bat                  # Windows convenience launcher
├── state_manager.py         # Atomic state file manager
└── whatsapp_client.py       # WhatsApp Agent Platform HTTP transport
```

---

## ⚙️ Configuration

Create your `.env` file by copying [.env.example](file:///.env.example):
```bash
cp .env.example .env
```

### Environment Parameters

| Variable | Type | Required | Default | Description |
|:---|:---:|:---:|:---|:---|
| `WHATSAPP_API_TOKEN` | String | **Yes** | — | Bearer token from WhatsApp App > Settings > Agents > Chat info > API key. |
| `GEMINI_API_KEYS` | String | **Yes** | — | Comma-separated Gemini API keys (supports 1 to 20 keys with automatic failover). |
| `GEMINI_MODEL` | String | No | `gemini-3.5-flash` | Primary Gemini model for inference. |
| `GEMINI_MODELS` | String | No | `gemini-3.5-flash,gemini-3.8-flash,gemini-3.7-flash,gemini-2.5-flash` | Fallback model chain. |
| `STATE_FILE` | String | No | `state.json` | Path to the persistent state file (mount to a persistent volume for Docker). |
| `AGENT_NAME` | String | No | `Sufi_vela` | Persona name displayed in system prompts and logs. |
| `LOG_LEVEL` | String | No | `INFO` | Application log verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |

> [!WARNING]
> **CRITICAL SECURITY REQUIREMENT:** Never commit `.env` or expose API keys. The codebase automatically masks all keys, tokens, and `wamid` IDs across logs and status outputs.

> [!CAUTION]
> **SINGLE INSTANCE ENFORCEMENT:** Never run multiple agent processes with the same `WHATSAPP_API_TOKEN`. Polling WhatsApp concurrently from two processes triggers `HTTP 409 Conflict` errors and out-of-order state offsets.

---

## 💬 Chat Commands

Users can interact with special commands directly inside the WhatsApp conversation:

- `/status` — Delivers a diagnostic summary displaying active key index, per-key operational states (OK, Cooldown remaining, Disabled), and model availability. (All keys remain masked).
- `/reset` — Clears conversation history and memory for the user, starting a fresh conversation context.

---

## 🚀 Quickstart Guide

### 1. Install Dependencies
```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

### 2. Run Verification Suite
```bash
# Execute unit tests
pytest -v

# Run linter and formatter validation
ruff check .
black --check .
```

### 3. Launch the Agent
```bash
python main.py
```

---

## 🐳 Production Deployment (Docker & 24/7 VPS)

For production environments, containerize the application and mount a volume to persist `state.json`:

```bash
# 1. Build the production image
docker build -t whatsapp-gemini-agent .

# 2. Run container with persistent storage
docker run -d \
  --name whatsapp-gemini-agent \
  --restart unless-stopped \
  --env-file .env \
  -e STATE_FILE=/app/data/state.json \
  -v whatsapp_agent_data:/app/data \
  whatsapp-gemini-agent
```

View real-time masked logs:
```bash
docker logs -f whatsapp-gemini-agent
```

---

## 🛠️ Diagnostics & HTTP Status Codes

| Status Code | Component | Cause | Agent Handling |
|:---|:---:|:---|:---|
| **200 / 201** | WhatsApp | Successful request | Message dispatched or updates parsed. |
| **204** | WhatsApp | Poll timeout (no messages) | Standard long-poll behavior; immediately continues next poll cycle. |
| **400** | Gemini | `INVALID_ARGUMENT` | Logged as bad request; returns polite error without disabling key or burning other keys. |
| **401 / 403** | WhatsApp | Expired or invalid token | Logged as critical authentication failure. Regenerate key from WhatsApp mobile app. |
| **401 / 403** | Gemini | `API_KEY_INVALID` | Offending key is permanently disabled for the run. Other keys continue serving. |
| **404** | Gemini | Model unavailable / retired | Cached in `unavailable_pairs`; attempts other keys on the same model or falls back to next model. |
| **409** | WhatsApp | Concurrent polling conflict | Another process is polling the same token. Stop the other instance immediately. |
| **429** | WhatsApp | Rate limit exceeded | Token-bucket rate limiter throttles and retries with exponential backoff and jitter. |
| **429** | Gemini | `RESOURCE_EXHAUSTED` | Key enters 65s cooldown (or until Pacific midnight if daily quota); immediately fails over to next key. |
| **5xx** | Both | Transient service error | Retried once with backoff; falls back to friendly error message if unresolved. |

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
