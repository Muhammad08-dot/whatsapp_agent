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

An enterprise-grade, high-availability conversational AI assistant connecting the **WhatsApp Agent Platform** (`https://api.whatsapp.com/agent/v1`) directly to **Google Gemini LLM** using the official Google GenAI SDK.

Engineered for 24/7 resilience with sequential multi-key rotation, dynamic model fallback chains, sliding-window rate limiting, and atomic state recovery.

---

## 🌟 Key Highlights

- **WhatsApp Agent Platform Compliant**: Built strictly around `https://api.whatsapp.com/agent/v1` (Bearer auth, `user:<id>` identity preservation, automatic splitting of replies over 4,096 characters, and delivery receipts).
- **Sequential Multi-Key Failover**: Supports 1 to 20 Gemini API keys in rotation. Automatically applies 65-second (or Pacific midnight daily quota) cooldowns on HTTP 429 (`RESOURCE_EXHAUSTED`) and fails over sequentially.
- **Dynamic Model Fallback Chain**: Caches unavailable `(key, model)` pairs on HTTP 404 and transitions gracefully across fallback models (`gemini-3.5-flash` → `gemini-3.8-flash` → `gemini-3.7-flash` → `gemini-2.5-flash`).
- **Poison-Message Immunity**: A failing message is caught gracefully, logged with masked IDs, answered with a courteous technical error response, and marked handled so batches never loop forever.
- **Atomic State Persistence**: Advances `next_offset` only after every message in a batch has been handled or skipped, preventing duplicate replies and lost messages across restarts.
- **Sliding-Window Rate Limiting**: Built-in token bucket throttlers strictly enforce WhatsApp rate limits (12 calls/min for sends, 15 calls/min for long-polling).
- **Secret & Privacy Masking**: Automatically masks sensitive API keys, bearer tokens, and `wamid` message IDs across status reports and application logs.

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

## 🚀 24/7 Free Cloud Hosting (Hugging Face Spaces)

You can run this agent **100% Free, 24/7 in the cloud** without keeping your computer on.

### Step 1: Create a Hugging Face Space
1. Sign up or log in at [Hugging Face](https://huggingface.co).
2. Go to **Spaces > Create new Space**.
3. Choose:
   - **Space SDK**: `Gradio` (Blank template)
   - **Space Hardware**: `Free` (CPU Basic or ZeroGPU Free)
   - **Visibility**: `Private`
4. Click **Create Space**.

### Step 2: Add Secrets (Environment Variables)
Inside your Space:
1. Navigate to **Settings > Variables and secrets**.
2. Click **New secret** and add:
   - `WHATSAPP_API_TOKEN` = Your WhatsApp Agent API key.
   - `GEMINI_API_KEYS` = Your comma-separated Gemini keys (`key1,key2,key3...`).
   - `GEMINI_MODEL` = `gemini-3.5-flash`
   - `AGENT_NAME` = `Sufi_vela`

### Step 3: Deploy via Git
Generate an Access Token with **Write** permission at [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens). Then run in your terminal:

```bash
cd whatsapp_gemini_agent
git remote add space https://huggingface.co/spaces/YOUR_USERNAME/YOUR_SPACE_NAME
git push space main --force
```
*(Enter your HF username and your `hf_...` token as the password).*

Hugging Face will automatically build and start the agent background worker. You can monitor the real-time status using the built-in Gradio web interface!

---

## ⚙️ Configuration Variables

| Variable | Required | Default | Description |
|:---|:---:|:---|:---|
| `WHATSAPP_API_TOKEN` | **Yes** | — | Bearer token from WhatsApp App > Settings > Agents > Chat info > API key. |
| `GEMINI_API_KEYS` | **Yes** | — | Comma-separated Gemini API keys (supports 1 to 20 keys with automatic failover). |
| `GEMINI_MODEL` | No | `gemini-3.5-flash` | Primary Gemini model for inference. |
| `GEMINI_MODELS` | No | `gemini-3.5-flash,gemini-3.8-flash,gemini-3.7-flash,gemini-2.5-flash` | Fallback model chain. |
| `AGENT_NAME` | No | `Sufi_vela` | Persona name displayed in system prompts and logs. |
| `LOG_LEVEL` | No | `INFO` | Application log verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |

> [!WARNING]
> **SINGLE INSTANCE ENFORCEMENT:** Never run two agent instances simultaneously on the same `WHATSAPP_API_TOKEN`. Polling WhatsApp concurrently from multiple locations triggers `HTTP 409 Conflict` errors and out-of-order state offsets. Turn off local runs before running on the cloud.

---

## 💬 Bot Commands

Users can interact with special commands directly inside the WhatsApp conversation:

- `/status` — Delivers a diagnostic summary displaying active key index, per-key operational states (OK, Cooldown remaining, Disabled), and model availability. (All keys remain masked).
- `/reset` — Clears conversation history and memory for the user, starting a fresh conversation context.

---

## 💻 Local Development & Testing

### 1. Install Dependencies
```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

### 2. Run Test Suite
```bash
pytest -v
ruff check .
black --check .
```

### 3. Run Locally
```bash
python main.py
```

---

## 🛠️ HTTP Status Codes & Error Handling

| Status Code | Component | Cause | Agent Handling |
|:---|:---:|:---|:---|
| **200 / 201** | WhatsApp | Successful request | Message dispatched or updates parsed. |
| **204** | WhatsApp | Poll timeout (no messages) | Standard long-poll behavior; immediately continues next poll cycle. |
| **400** | Gemini | `INVALID_ARGUMENT` | Logged as bad request; returns polite error without disabling key. |
| **401 / 403** | WhatsApp | Expired or invalid token | Logged as critical authentication failure. Regenerate key from WhatsApp app. |
| **401 / 403** | Gemini | `API_KEY_INVALID` | Offending key is permanently disabled for the run. Other keys continue serving. |
| **404** | Gemini | Model unavailable / retired | Cached in `unavailable_pairs`; attempts other keys on the same model or falls back to next model. |
| **409** | WhatsApp | Concurrent polling conflict | Another process is polling the same token. Stop the other instance immediately. |
| **429** | WhatsApp | Rate limit exceeded | Token-bucket rate limiter throttles and retries with exponential backoff and jitter. |
| **429** | Gemini | `RESOURCE_EXHAUSTED` | Key enters 65s cooldown (or until Pacific midnight if daily quota); immediately fails over to next key. |
| **5xx** | Both | Transient service error | Retried once with backoff; falls back to friendly error message if unresolved. |

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
