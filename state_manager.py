"""
Persistent State Storage for WhatsApp Agent.

Saves next_offset and handled message IDs atomically to prevent duplicate
responses and lost position across server restarts.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from config import STATE_FILE

logger = logging.getLogger(__name__)


class StateManager:
    """Handles atomic reads and writes of agent state."""

    def __init__(self, file_path: Path = STATE_FILE, max_history_ids: int = 1000):
        self.file_path = file_path
        self.max_history_ids = max_history_ids
        self.next_offset: int | None = None
        self.handled_ids: set[str] = set()
        self.load()

    def load(self) -> None:
        """Load state from disk if present."""
        if not self.file_path.exists():
            logger.info("ℹ️ No previous state.json found; starting fresh.")
            return

        try:
            with open(self.file_path, encoding="utf-8") as f:
                data: dict[str, Any] = json.load(f)
                self.next_offset = data.get("next_offset")
                raw_ids = data.get("handled_ids", [])
                self.handled_ids = set(raw_ids[-self.max_history_ids :])
            logger.info(
                f"💾 Loaded state: offset={self.next_offset}, handled_count={len(self.handled_ids)}"
            )
        except Exception as e:
            logger.warning(
                f"⚠️ Failed to read state.json ({e}); continuing with in-memory state."
            )

    def is_handled(self, message_id: str) -> bool:
        """Check if message id has already been processed."""
        return message_id in self.handled_ids

    def record_handled(self, message_id: str) -> None:
        """Record handled message ID and trim older records if needed."""
        if not message_id:
            return
        self.handled_ids.add(message_id)
        if len(self.handled_ids) > self.max_history_ids * 2:
            # Keep newest elements
            as_list = list(self.handled_ids)
            self.handled_ids = set(as_list[-self.max_history_ids :])

    def set_offset(self, offset: int | None) -> None:
        """Update next_offset."""
        if offset is not None:
            self.next_offset = offset

    def save(self) -> None:
        """Write state to disk atomically using a temp file."""
        payload = {
            "next_offset": self.next_offset,
            "handled_ids": list(self.handled_ids)[-self.max_history_ids :],
        }

        parent_dir = self.file_path.parent
        parent_dir.mkdir(parents=True, exist_ok=True)

        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=parent_dir,
                delete=False,
                suffix=".tmp",
            ) as tf:
                json.dump(payload, tf, indent=2)
                temp_name = tf.name

            # Atomic replace
            os.replace(temp_name, self.file_path)
            logger.debug(f"💾 State saved: offset={self.next_offset}")
        except Exception as e:
            logger.error(f"❌ Failed to persist state atomically: {e}")
