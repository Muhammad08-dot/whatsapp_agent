"""
WhatsApp Gemini Agent Entry Point.

Handles:
- Startup configuration & credential validation.
- Rotating file and console logging configuration.
- Graceful shutdown signal handling (SIGINT, SIGTERM).
- Polling loop execution.
"""

from __future__ import annotations

import io
import logging
import os
import signal
import sys
import threading
import time
from logging.handlers import RotatingFileHandler

# Ensure package root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    AGENT_NAME,
    GEMINI_API_KEYS,
    GEMINI_MODEL,
    LOG_BACKUP_COUNT,
    LOG_FILE_PATH,
    LOG_LEVEL,
    LOG_MAX_BYTES,
    WHATSAPP_API_TOKEN,
    mask_secret,
)


def setup_logging() -> None:
    """Configure console and rotating file logging."""
    LOG_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    numeric_level = getattr(logging, LOG_LEVEL, logging.INFO)
    root_logger.setLevel(numeric_level)

    # Clean existing handlers
    root_logger.handlers.clear()

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(numeric_level)
    root_logger.addHandler(console_handler)

    # Rotating File Handler
    file_handler = RotatingFileHandler(
        filename=LOG_FILE_PATH,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(numeric_level)
    root_logger.addHandler(file_handler)


def validate_environment() -> bool:
    """Verify presence of required secrets and settings before startup."""
    errors = []

    if not WHATSAPP_API_TOKEN or "yahan_" in WHATSAPP_API_TOKEN:
        errors.append(
            "❌ Missing or placeholder WHATSAPP_API_TOKEN in .env file.\n"
            "   Obtain from WhatsApp app > Settings > Agents > Chat info > API key."
        )

    if not GEMINI_API_KEYS or any("yahan_" in k for k in GEMINI_API_KEYS):
        errors.append(
            "❌ Missing or placeholder GEMINI_API_KEYS in .env file.\n"
            "   Obtain from https://aistudio.google.com/apikey"
        )

    if errors:
        for err in errors:
            print(f"\n{err}", file=sys.stderr)
        return False

    return True


def main() -> None:
    """Main execution flow and poll loop."""
    # Ensure UTF-8 output on Windows terminal
    if sys.stdout.encoding.lower() != "utf-8":
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer, encoding="utf-8", errors="replace"
        )

    setup_logging()
    logger = logging.getLogger("main")

    logger.info("==================================================")
    logger.info(f"🤖 Starting WhatsApp Gemini Agent: {AGENT_NAME}")
    logger.info(f"   Model: {GEMINI_MODEL}")
    logger.info(f"   Token: {mask_secret(WHATSAPP_API_TOKEN)}")
    logger.info(f"   Loaded {len(GEMINI_API_KEYS)} keys")
    logger.info("==================================================")

    if not validate_environment():
        logger.error("💀 Environment validation failed. Exiting.")
        sys.exit(1)

    from agent import WhatsAppAgent

    bot = WhatsAppAgent()
    shutdown_event = threading.Event()

    def handle_exit(signum, frame):
        # ONLY set the shutdown flag. No state saving, closing, or sys.exit inside signal handler.
        shutdown_event.set()

    # Register OS signals if running in main thread
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGINT, handle_exit)
        signal.signal(signal.SIGTERM, handle_exit)

    # On Windows, also register a console control handler to reliably capture Ctrl+C/Break events
    global _win_ctrl_handler
    if sys.platform == "win32":
        try:
            import ctypes

            def win_handler(dwCtrlType):
                shutdown_event.set()
                return True

            _win_ctrl_handler = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_ulong)(
                win_handler
            )
            ctypes.windll.kernel32.SetConsoleCtrlHandler(_win_ctrl_handler, True)
        except Exception as e:
            logger.debug(f"Could not register Win32 console handler: {e}")

    logger.info(
        "📡 Agent polling loop active. Waiting for incoming WhatsApp messages..."
    )

    consecutive_errors = 0
    while not shutdown_event.is_set():
        try:
            bot.step(shutdown_event=shutdown_event)
            consecutive_errors = 0
        except KeyboardInterrupt:
            shutdown_event.set()
            break
        except Exception as e:
            consecutive_errors += 1
            delay = min(2**consecutive_errors, 30)
            logger.error(
                f"❌ Unhandled error in poll loop: {e}. Backing off {delay}s...",
                exc_info=True,
            )
            # Sleep in short increments to remain responsive to shutdown_event
            end_sleep = time.monotonic() + delay
            while time.monotonic() < end_sleep and not shutdown_event.is_set():
                time.sleep(0.2)

    logger.info("🛑 Shutdown flag detected. Saving state and shutting down...")
    bot.state.save()
    logger.info("💾 State saved successfully.")
    bot.wa.close()
    logger.info("👋 Shutdown complete. Goodbye!")
    sys.exit(0)


if __name__ == "__main__":
    main()
