"""
Models and Dataclasses for WhatsApp Agent Platform.

Strictly adheres to WhatsApp Agent Platform Version 1 spec:
- All participant identifiers are formatted as user:<id> or agent:<id>.
- Incoming messages are parsed into strongly typed dataclasses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class IncomingMessage:
    """Represents a validated inbound message received from WhatsApp."""

    id: str
    sender: str  # Format: "user:<id>"
    sender_name: str
    type: str  # "text", "image", "audio", "video", "document", "sticker", "reaction"
    timestamp: str
    text: str = ""
    caption: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_text_message(self) -> bool:
        """Check if message contains actionable text content."""
        return bool(self.text.strip())


@dataclass(frozen=True)
class StatusUpdate:
    """Represents a delivery or read status update for an outbound message."""

    id: str
    recipient_id: str
    status: str  # "delivered" or "read"
    timestamp: str


@dataclass
class ConversationState:
    """Lightweight conversation history item."""

    role: str  # "user" or "model"
    content: str
