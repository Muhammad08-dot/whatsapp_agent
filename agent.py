"""
Agent Orchestration Module.

Orchestrates flow between WhatsApp transport, state manager, and Gemini LLM:
1. Polls updates from WhatsApp.
2. Filters out duplicate message IDs via StateManager.
3. Sends read receipt and typing indicators.
4. Generates response using GeminiClient.
5. Sends reply using WhatsAppClient.
6. Persists state atomically.
"""

from __future__ import annotations

import logging
import threading

from config import mask_secret
from exceptions import AllKeysExhaustedError, ModelUnavailableError
from gemini_client import GeminiClient
from models import IncomingMessage, StatusUpdate
from state_manager import StateManager
from whatsapp_client import WhatsAppClient

logger = logging.getLogger(__name__)


class WhatsAppAgent:
    """Core orchestration engine for WhatsApp Agent."""

    def __init__(
        self,
        wa_client: WhatsAppClient | None = None,
        gemini_client: GeminiClient | None = None,
    ):
        self.wa = wa_client or WhatsAppClient()
        self.gemini = gemini_client or GeminiClient()
        self.state = StateManager()

    def step(self, shutdown_event: threading.Event | None = None) -> None:
        """
        Execute a single polling and processing cycle.
        If shutdown_event is provided, checks the event between processing messages.
        """
        updates = self.wa.get_updates(offset=self.state.next_offset)
        if updates is None:
            return

        messages, statuses, next_offset = updates

        # Handle delivery / read status receipts
        for status in statuses:
            if shutdown_event and shutdown_event.is_set():
                break
            self.handle_status(status)

        # Process each incoming message
        batch_completed = True
        for message in messages:
            if shutdown_event and shutdown_event.is_set():
                logger.info(
                    "🛑 Shutdown requested during message batch. Pausing after current message."
                )
                batch_completed = False
                break
            self.handle_message(message)

        # Advance next_offset only after every message in the batch has been handled or skipped
        if (
            batch_completed
            and next_offset is not None
            and next_offset != self.state.next_offset
        ):
            self.state.set_offset(next_offset)
            self.state.save()

    def handle_message(self, message: IncomingMessage) -> None:
        """
        Process a single incoming message.
        Guarantees that a failing message is handled gracefully, logged with masked ID,
        answered with a polite error if possible, marked handled, and never causes the
        batch to replay forever.
        """
        masked_id = mask_secret(message.id, prefix_len=10, suffix_len=6)

        # Spec rule: deduplicate message ids
        if self.state.is_handled(message.id):
            logger.debug(f"Ignoring already handled message ID: {masked_id}")
            return

        # Record handling immediately to avoid duplicate processing across interruptions
        self.state.record_handled(message.id)
        self.state.save()

        logger.info(f"📩 [{message.sender_name}] ({message.type}) — ID: {masked_id}")
        logger.debug(f"Message payload text: {message.text}")

        try:
            # Mark as read and show typing indicator
            self.wa.mark_as_read(message.id, show_typing=True)

            # Check message content
            if not message.is_text_message:
                logger.info(f"Non-text message type received: {message.type}")
                self.wa.send_message(
                    to=message.sender,
                    text="Main abhi sirf text messages samajh sakta hoon. 😊 Apna sawal text mein likhein!",
                )
                return

            # Handle /status command (creator inspection)
            if message.text.strip().lower() == "/status":
                status_text = self.gemini.get_status_report()
                self.wa.send_message(to=message.sender, text=status_text)
                return

            # Handle /reset command (clear user conversational memory)
            if message.text.strip().lower() == "/reset":
                self.gemini.reset_user_history(message.sender)
                self.wa.send_message(
                    to=message.sender,
                    text="Aapki conversation memory reset kar di gayi hai! Naye siray se shuru karte hain. 😊",
                )
                return

            # Generate response via LLM
            try:
                reply_text = self.gemini.get_reply(
                    user_id=message.sender,
                    user_message=message.text,
                    user_name=message.sender_name,
                )
            except AllKeysExhaustedError:
                logger.error("❌ All keys exhausted / cooling down.")
                reply_text = "Abhi limit khatam ho gayi hai, thori der baad try karo."
            except ModelUnavailableError:
                logger.error("❌ Model unavailable everywhere.")
                reply_text = "Model abhi available nahi hai, owner ko batao."
            except Exception as e:
                logger.error(f"❌ Unexpected Gemini error: {e}")
                reply_text = "Request process nahi ho saki."

            # Send response back to user:<id> unchanged
            result = self.wa.send_message(to=message.sender, text=reply_text)
            if result:
                logger.info(
                    f"📤 Sent reply to {message.sender_name} for message {masked_id}"
                )
            else:
                logger.error(f"❌ Failed to dispatch reply to {message.sender_name}")

        except Exception as exc:
            logger.error(
                f"⚠️ Error handling message {masked_id} from {message.sender}: {exc}",
                exc_info=True,
            )
            try:
                self.wa.send_message(
                    to=message.sender,
                    text="Maaf kijiye, aapka paigham process karte waqt aik takneeki masla pesh aya.",
                )
            except Exception as send_err:
                logger.error(
                    f"❌ Could not deliver error response to {message.sender}: {send_err}"
                )

    def handle_status(self, status: StatusUpdate) -> None:
        """Log status receipts with masked message ID."""
        masked_id = mask_secret(status.id, prefix_len=10, suffix_len=6)
        logger.debug(
            f"📊 Status receipt: {status.status} for msg {masked_id} from {status.recipient_id}"
        )
