"""
Gradio entry point for Hugging Face Spaces hosting.
Runs the WhatsApp Agent polling loop in a persistent background daemon thread,
while providing a clean web UI for live status and health monitoring.
"""

from __future__ import annotations

import logging
import threading
import time

import gradio as gr

from agent import WhatsAppAgent
from config import AGENT_NAME, GEMINI_MODEL, mask_secret, WHATSAPP_API_TOKEN
from gemini_client import GeminiClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("hf_space")

# Global agent reference
bot: WhatsAppAgent | None = None
agent_thread: threading.Thread | None = None
is_running: bool = False


def run_agent_loop():
    """Background polling loop for WhatsApp Agent."""
    global bot, is_running
    logger.info("🚀 Launching WhatsApp Agent background worker...")
    try:
        bot = WhatsAppAgent()
        is_running = True
        logger.info("📡 WhatsApp Agent background loop started successfully!")
        while is_running:
            try:
                bot.step()
            except Exception as e:
                logger.error(f"Poll loop error: {e}")
                time.sleep(5)
    except Exception as e:
        logger.error(f"Failed to initialize WhatsAppAgent: {e}", exc_info=True)


def start_worker():
    """Ensure worker thread is active."""
    global agent_thread
    if agent_thread is None or not agent_thread.is_alive():
        agent_thread = threading.Thread(target=run_agent_loop, daemon=True)
        agent_thread.start()


# Auto-start on load
start_worker()


def get_agent_status() -> str:
    """Return live status report for web dashboard."""
    if bot and bot.gemini:
        return bot.gemini.get_status_report()
    return "⏳ Agent is initializing background loop..."


# Build simple Gradio Web Interface
with gr.Blocks(title=f"{AGENT_NAME} - WhatsApp Agent") as demo:
    gr.Markdown(f"# 🤖 {AGENT_NAME} — WhatsApp AI Agent")
    gr.Markdown(
        f"**Model:** `{GEMINI_MODEL}` | **WhatsApp Token:** `{mask_secret(WHATSAPP_API_TOKEN)}`"
    )
    gr.Markdown("---")

    status_output = gr.Textbox(
        label="Agent Health & Gemini Keys Status",
        value=get_agent_status,
        lines=10,
        interactive=False,
    )

    refresh_btn = gr.Button("🔄 Refresh Status")
    refresh_btn.click(fn=get_agent_status, outputs=status_output)

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
