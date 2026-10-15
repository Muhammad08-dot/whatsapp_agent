"""
WhatsApp Agent Platform Transport Client.

Exclusively handles HTTP transport, authentication, connection pooling,
rate limiting, and response parsing compliant with WhatsApp Agent Platform Version 1 spec.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from config import (
    MAX_POLLS_PER_MINUTE,
    MAX_REPLY_LENGTH,
    MAX_SENDS_PER_MINUTE,
    NETWORK_BUFFER_TIMEOUT,
    POLL_LIMIT,
    POLL_TIMEOUT,
    WHATSAPP_API_TOKEN,
    WHATSAPP_BASE_URL,
    mask_secret,
)
from models import IncomingMessage, StatusUpdate
from rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


class WhatsAppClient:
    """Robust transport client for WhatsApp Agent Platform."""

    def __init__(self, api_token: str = WHATSAPP_API_TOKEN):
        if not api_token:
            raise ValueError(
                "❌ WHATSAPP_API_TOKEN is not configured. Check your .env file."
            )
        self.base_url = WHATSAPP_BASE_URL
        self.api_token = api_token

        # Dedicated sliding rate limiters
        self.poll_limiter = RateLimiter(
            max_calls=MAX_POLLS_PER_MINUTE, period_seconds=60.0
        )
        self.send_limiter = RateLimiter(
            max_calls=MAX_SENDS_PER_MINUTE, period_seconds=60.0
        )

        # Reusable Session with pool limits and retry adapter for idempotent operations
        self.session = requests.Session()
        retries = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[502, 503, 504],
            allowed_methods=["GET"],
        )
        adapter = HTTPAdapter(max_retries=retries, pool_connections=10, pool_maxsize=10)
        self.session.mount("https://", adapter)

        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.api_token}",
                "Content-Type": "application/json",
            }
        )

    def close(self) -> None:
        """Close the underlying HTTP session."""
        self.session.close()

    def get_updates(
        self, offset: int | None = None
    ) -> tuple[list[IncomingMessage], list[StatusUpdate], int | None] | None:
        """
        Poll /agent/v1/updates using long polling.

        Returns:
            Tuple of (messages, statuses, next_offset) or None on empty/timeout.
        """
        self.poll_limiter.acquire()

        params: dict[str, Any] = {
            "limit": POLL_LIMIT,
            "timeout": POLL_TIMEOUT,
        }
        if offset is not None:
            params["offset"] = offset

        client_timeout = POLL_TIMEOUT + NETWORK_BUFFER_TIMEOUT

        try:
            resp = self.session.get(
                f"{self.base_url}/updates",
                params=params,
                timeout=client_timeout,
            )

            # Per spec: HTTP 204 means no updates before timeout
            if resp.status_code == 204:
                logger.debug("Poll timed out with 204 No Content.")
                return None

            if resp.status_code == 429:
                self._handle_rate_limit(resp)
                return None

            resp.raise_for_status()
            data = resp.json()
            return self.parse_updates(data)

        except requests.exceptions.Timeout:
            logger.debug("Client-side read timeout on long-poll; retrying...")
            return None
        except requests.exceptions.RequestException as e:
            self._log_api_error("GET /updates", e)
            return None

    def send_message(self, to: str, text: str) -> dict[str, Any] | None:
        """
        Send an outbound text message to a WhatsApp user.
        Recipient must be exactly the string 'user:<id>' received from updates.
        """
        if not to or not to.startswith("user:"):
            logger.error(
                f"❌ Invalid recipient identifier '{to}'. Spec requires 'user:<id>'."
            )
            return None

        # Auto-split if length exceeds 4096 characters per spec
        chunks = self.split_text(text, max_length=MAX_REPLY_LENGTH)
        last_resp = None

        for idx, chunk in enumerate(chunks):
            self.send_limiter.acquire()
            payload = {
                "messaging_product": "whatsapp",
                "to": to,
                "type": "text",
                "text": {"body": chunk},
            }

            last_resp = self._post_with_backoff(f"{self.base_url}/messages", payload)
            if not last_resp:
                logger.error(f"❌ Failed to send chunk {idx + 1}/{len(chunks)} to {to}")
                break

        return last_resp

    def mark_as_read(self, message_id: str, show_typing: bool = False) -> bool:
        """Mark inbound message as read and optionally trigger typing indicator."""
        if not message_id:
            return False

        self.send_limiter.acquire()
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
        }
        if show_typing:
            payload["typing_indicator"] = {"type": "text"}

        resp = self._post_with_backoff(f"{self.base_url}/statuses", payload)
        return resp is not None

    def _post_with_backoff(
        self, url: str, json_data: dict[str, Any], max_attempts: int = 3
    ) -> dict[str, Any] | None:
        """Execute POST request with exponential backoff and jitter on 429/503."""
        attempt = 0
        backoff = 1.0

        while attempt < max_attempts:
            attempt += 1
            try:
                resp = self.session.post(url, json=json_data, timeout=20)

                if resp.status_code in (200, 201):
                    data = resp.json()
                    msg_id = data.get("messages", [{}])[0].get("id", "status_ok")
                    masked_mid = (
                        mask_secret(msg_id, prefix_len=10, suffix_len=6)
                        if msg_id != "status_ok"
                        else msg_id
                    )
                    logger.debug(f"✅ Success from {url} - ID: {masked_mid}")
                    return data

                if resp.status_code == 429:
                    jitter = random.uniform(0.1, 0.5)
                    wait_time = backoff + jitter
                    logger.warning(
                        f"⚠️ 429 Rate limit on {url}. Backing off {wait_time:.2f}s..."
                    )
                    time.sleep(wait_time)
                    backoff *= 2
                    continue

                if resp.status_code == 503:
                    # Per spec: 503 means message not accepted; retry after backoff
                    jitter = random.uniform(0.1, 0.5)
                    wait_time = backoff + jitter
                    logger.warning(
                        f"⚠️ 503 Service Unavailable on {url}. Retrying in {wait_time:.2f}s..."
                    )
                    time.sleep(wait_time)
                    backoff *= 2
                    continue

                # Unrecoverable 4xx
                self._log_api_error(f"POST {url}", requests.HTTPError(response=resp))
                return None

            except requests.exceptions.RequestException as e:
                logger.warning(f"⚠️ Connection error on attempt {attempt}: {e}")
                if attempt >= max_attempts:
                    self._log_api_error(f"POST {url}", e)
                    return None
                time.sleep(backoff)
                backoff *= 2

        return None

    @staticmethod
    def split_text(text: str, max_length: int = MAX_REPLY_LENGTH) -> list[str]:
        """Split text safely at line breaks or word boundaries without truncating."""
        if len(text) <= max_length:
            return [text]

        chunks: list[str] = []
        while text:
            if len(text) <= max_length:
                chunks.append(text)
                break

            # Find split point before max_length
            split_idx = text.rfind("\n", 0, max_length)
            if split_idx == -1 or split_idx < max_length // 2:
                split_idx = text.rfind(" ", 0, max_length)

            if split_idx == -1 or split_idx == 0:
                split_idx = max_length

            chunk = text[:split_idx].strip()
            if chunk:
                chunks.append(chunk)
            text = text[split_idx:].strip()

        return chunks

    @staticmethod
    def parse_updates(
        response: dict[str, Any],
    ) -> tuple[list[IncomingMessage], list[StatusUpdate], int | None]:
        """Parse raw response from /agent/v1/updates into typed dataclasses."""
        messages: list[IncomingMessage] = []
        statuses: list[StatusUpdate] = []
        next_offset = response.get("next_offset")

        for entry in response.get("entry", []):
            for change in entry.get("changes", []):
                val = change.get("value", {})
                contacts = val.get("contacts", [])

                contact_names: dict[str, str] = {}
                for c in contacts:
                    wa_id = str(c.get("wa_id", "")).strip()
                    profile = c.get("profile") or {}
                    name = profile.get("name")
                    if name:
                        contact_names[wa_id] = name
                        # Also map without/with 'user:' prefix so lookup never misses
                        if wa_id.startswith("user:"):
                            contact_names[wa_id[5:]] = name
                        else:
                            contact_names[f"user:{wa_id}"] = name

                # Parse inbound messages
                for raw_msg in val.get("messages", []):
                    msg_id = raw_msg.get("id", "")
                    sender = raw_msg.get("from", "")
                    msg_type = raw_msg.get("type", "")
                    timestamp = raw_msg.get("timestamp", "")
                    sender_name = contact_names.get(sender) or "Unknown"

                    # Extract text or captions
                    text_content = ""
                    caption_content = ""
                    if msg_type == "text":
                        text_content = raw_msg.get("text", {}).get("body", "")
                    elif msg_type in ("image", "video", "document"):
                        caption_content = raw_msg.get(msg_type, {}).get("caption", "")
                        text_content = caption_content

                    msg = IncomingMessage(
                        id=msg_id,
                        sender=sender,
                        sender_name=sender_name,
                        type=msg_type,
                        timestamp=timestamp,
                        text=text_content,
                        caption=caption_content,
                        raw=raw_msg,
                    )
                    messages.append(msg)

                # Parse status receipts
                for raw_status in val.get("statuses", []):
                    status = StatusUpdate(
                        id=raw_status.get("id", ""),
                        recipient_id=raw_status.get("recipient_id", ""),
                        status=raw_status.get("status", ""),
                        timestamp=raw_status.get("timestamp", ""),
                    )
                    statuses.append(status)

        return messages, statuses, next_offset

    def _handle_rate_limit(self, resp: requests.Response) -> None:
        """Handle 429 rate limit backoff."""
        fbtrace = resp.headers.get("x-fb-trace-id", "unknown")
        logger.warning(
            f"⚠️ Received HTTP 429 Rate Limit (fbtrace_id: {fbtrace}). Sleeping 5s..."
        )
        time.sleep(5.0)

    def _log_api_error(self, action: str, err: Exception) -> None:
        """Log API errors along with x-fb-trace-id if present."""
        fbtrace = "unknown"
        body = ""
        if hasattr(err, "response") and err.response is not None:
            fbtrace = err.response.headers.get("x-fb-trace-id", "unknown")
            body = err.response.text

        logger.error(f"❌ {action} failed: {err} [fbtrace_id: {fbtrace}]")
        if body:
            logger.debug(f"   API Response Body: {body}")
