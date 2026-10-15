"""
Knowledge Base Engine with Relevance Search.

Loads .txt, .md, and .json files from the /data directory, splits them into chunks,
and retrieves only the most relevant chunks using fast keyword scoring.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from config import DATA_DIR, TOP_K_KNOWLEDGE_CHUNKS

logger = logging.getLogger(__name__)


class KnowledgeBase:
    """Manages document retrieval from the data folder."""

    def __init__(self, data_dir: Path = DATA_DIR):
        self.data_dir = data_dir
        self.chunks: list[dict[str, str]] = []
        self.reload()

    def reload(self) -> None:
        """Load and parse all files from the data directory into searchable chunks."""
        self.chunks.clear()
        if not self.data_dir.exists():
            logger.info(f"Directory {self.data_dir} does not exist; skipping load.")
            return

        for path in self.data_dir.glob("*"):
            if path.suffix.lower() in (".txt", ".md"):
                self._load_text_file(path)
            elif path.suffix.lower() == ".json":
                self._load_json_file(path)

        logger.info(
            f"📚 Knowledge Base loaded {len(self.chunks)} chunks from {self.data_dir}"
        )

    def _load_text_file(self, path: Path) -> None:
        """Split markdown/text files into section chunks."""
        try:
            content = path.read_text(encoding="utf-8")
            # Split by double newlines or headers
            raw_sections = re.split(r"\n(?=#{1,3}\s)", content)
            for section in raw_sections:
                cleaned = section.strip()
                if cleaned:
                    self.chunks.append(
                        {
                            "source": path.name,
                            "content": cleaned,
                        }
                    )
        except Exception as e:
            logger.warning(f"⚠️ Failed reading knowledge file {path.name}: {e}")

    def _load_json_file(self, path: Path) -> None:
        """Load structured items or FAQs from JSON."""
        try:
            with open(path, encoding="utf-8") as f:
                data: Any = json.load(f)
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict):
                            # Convert dict to readable summary
                            text = "\n".join(f"{k}: {v}" for k, v in item.items())
                            self.chunks.append({"source": path.name, "content": text})
                elif isinstance(data, dict):
                    text = "\n".join(f"{k}: {v}" for k, v in data.items())
                    self.chunks.append({"source": path.name, "content": text})
        except Exception as e:
            logger.warning(f"⚠️ Failed reading JSON file {path.name}: {e}")

    def search(self, query: str, top_k: int = TOP_K_KNOWLEDGE_CHUNKS) -> list[str]:
        """
        Perform fast keyword relevance search on chunks without heavy embedding dependencies.
        Returns top-k most relevant chunk text.
        """
        if not query or not self.chunks:
            return [c["content"] for c in self.chunks[:top_k]]

        # Tokenize query words
        query_words = set(re.findall(r"\w+", query.lower()))
        # Remove common short words
        stop_words = {
            "the",
            "is",
            "a",
            "an",
            "and",
            "or",
            "in",
            "on",
            "at",
            "to",
            "for",
            "ka",
            "ki",
            "ke",
            "hai",
            "kya",
            "ko",
        }
        meaningful_words = query_words - stop_words
        if not meaningful_words:
            meaningful_words = query_words

        scored_chunks: list[tuple[float, str]] = []
        for item in self.chunks:
            chunk_text = item["content"]
            chunk_lower = chunk_text.lower()
            score = 0.0

            for word in meaningful_words:
                count = chunk_lower.count(word)
                if count > 0:
                    score += count * (1.5 if len(word) > 4 else 1.0)

            if score > 0:
                scored_chunks.append((score, chunk_text))

        # Sort by score descending
        scored_chunks.sort(key=lambda x: x[0], reverse=True)

        if scored_chunks:
            return [chunk for _, chunk in scored_chunks[:top_k]]

        # Fallback to top default chunks
        return [c["content"] for c in self.chunks[:top_k]]
