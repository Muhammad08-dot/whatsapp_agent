"""
Google Gemini LLM Client with Advanced Sequential Key Management.

Features:
- Migrated to modern google-genai SDK (per-key client instance, no global genai.configure).
- Thread-safe key state management with Lock.
- Sequential failover:
  * Always starts from the first usable key (not disabled, not cooling down).
  * Stays on that key while it works.
  * On 429/RESOURCE_EXHAUSTED, marks cooldown (65s, or Pacific midnight on daily quota) and uses next key.
  * When a key's cooldown expires, selection returns to the earliest usable key automatically.
  * Logs switches with key index only (masked keys).
- 404 Model Handling:
  * 404 marks only that (key, model) pair as unavailable in `unavailable_pairs`.
  * Moves to the NEXT KEY for the same model.
  * Moves to the next model only when no key can serve the current one.
  * Remembers unavailable pairs for this run so they are not retried on every message.
- Error Differentiation & Messages:
  * Real quota exhaustion -> AllKeysExhaustedError("Abhi limit khatam ho gayi hai, thori der baad try karo.")
  * All models 404 everywhere -> ModelUnavailableError("Model abhi available nahi hai, owner ko batao.")
  * Generic 400 -> returns "Request process nahi ho saki." (does NOT disable key, does NOT burn other keys).
- /status command support:
  * Returns active key index, per-key status, remaining cooldowns, disabled flags, available models.
- GenerateContentConfig with max_output_tokens=800 and request timeout=30000ms.
- Builds contents_payload once before model/key loops.
- Bounded 6-turn conversational memory with /reset command.
- Fast TTL cache for identical queries.
- Masks keys in all log statements.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from google import genai
from google.genai import types

from config import (
    ANSWER_CACHE_TTL_SECONDS,
    GEMINI_API_KEYS,
    GEMINI_MODEL,
    GEMINI_MODELS,
    KEY_COOLDOWN_SECONDS,
    MAX_CONVERSATION_TURNS,
    mask_secret,
)
from exceptions import AllKeysExhaustedError, ModelUnavailableError
from knowledge_base import KnowledgeBase
from prompts import build_system_prompt

logger = logging.getLogger(__name__)


def _seconds_until_pacific_midnight() -> float:
    """Calculate seconds from now until the next midnight in Pacific Time (approx UTC-7)."""
    pacific_tz = timezone(timedelta(hours=-7))
    now = datetime.now(pacific_tz)
    tomorrow = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=5, microsecond=0
    )
    return max((tomorrow - now).total_seconds(), 65.0)


class GeminiClient:
    """Production-grade Gemini Client with sequential key failover and model fallback."""

    def __init__(
        self,
        api_keys: list[str] | None = None,
        model_name: str = GEMINI_MODEL,
        models_fallback: list[str] | None = None,
        cooldown_seconds: float = KEY_COOLDOWN_SECONDS,
    ):
        raw_keys = api_keys if api_keys is not None else GEMINI_API_KEYS
        # Dedupe while preserving order, cap at 20 keys
        seen: set[str] = set()
        self.api_keys: list[str] = []
        for k in (item.strip() for item in raw_keys):
            if k and k not in seen:
                seen.add(k)
                self.api_keys.append(k)
                if len(self.api_keys) >= 20:
                    break

        if not self.api_keys:
            raise ValueError(
                "❌ No valid GEMINI_API_KEYS configured. Check your .env file."
            )

        if models_fallback is not None:
            self.models_fallback = list(models_fallback)
            self.model_name = (
                self.models_fallback[0] if self.models_fallback else model_name
            )
        else:
            self.model_name = model_name
            self.models_fallback = list(GEMINI_MODELS)
            if self.model_name not in self.models_fallback:
                self.models_fallback.insert(0, self.model_name)

        self.cooldown_seconds = cooldown_seconds
        self.kb = KnowledgeBase()

        # Thread-safe lock protecting multi-key states
        self._lock = threading.Lock()
        self.key_cooldowns: dict[str, float] = {}  # key -> cooldown expiry (monotonic)
        self.disabled_keys: set[str] = set()  # permanent disabled keys (401/403)
        self.unavailable_pairs: set[tuple[str, str]] = set()  # (key, model) 404 cache

        # Active key index for monitoring / /status
        self.active_key_index: int = 0

        # Dedicated per-key genai.Client objects (no global configure)
        self._clients: dict[str, genai.Client] = {}
        for key in self.api_keys:
            self._clients[key] = genai.Client(api_key=key)

        # Per-user conversational memory: user_id -> list of types.Content
        self.conversations: dict[str, list[types.Content]] = {}

        # TTL cache: query_key -> (timestamp, reply)
        self.response_cache: dict[str, tuple[float, str]] = {}

        logger.info(
            f"✅ Gemini LLM initialized — Model: {self.model_name} (Fallbacks: {self.models_fallback}) | "
            f"Loaded {len(self.api_keys)} keys (Active start: index 0 [{mask_secret(self.api_keys[0])}])"
        )

    def reset_user_history(self, user_id: str) -> None:
        """Clear conversation memory for a user."""
        with self._lock:
            if user_id in self.conversations:
                self.conversations[user_id].clear()
                logger.info(f"🧹 Reset conversation memory for {user_id}")

    def get_status_report(self) -> str:
        """Generate human-readable status report for /status command (no full keys)."""
        now = time.monotonic()
        with self._lock:
            lines = [
                "📊 *Agent & Gemini Status Report*",
                f"• Active Key Index: {self.active_key_index}",
                f"• Total Keys: {len(self.api_keys)}",
                f"• Models Fallback Chain: {', '.join(self.models_fallback)}",
                "",
                "*Per-Key Details:*",
            ]
            for idx, key in enumerate(self.api_keys):
                masked = mask_secret(key)
                if key in self.disabled_keys:
                    status = "DISABLED 🚫"
                elif key in self.key_cooldowns and now < self.key_cooldowns[key]:
                    rem = int(self.key_cooldowns[key] - now)
                    status = f"COOLDOWN ({rem}s left) ⏳"
                else:
                    status = "OK ✅"

                # Check which models are available for this key
                avail_models = [
                    m
                    for m in self.models_fallback
                    if (key, m) not in self.unavailable_pairs
                ]
                models_desc = (
                    ", ".join(avail_models) if avail_models else "None (all 404)"
                )

                lines.append(f"[{idx}] {masked}: {status} | Models: {models_desc}")

            return "\n".join(lines)

    def _get_usable_keys_for_model(self, model: str) -> list[tuple[int, str]]:
        """
        Return all currently usable (key_index, key) pairs for the given model,
        strictly starting from index 0 (earliest available key) to enforce sequential failover.
        Skips disabled keys, keys cooling down, and (key, model) 404 pairs.
        """
        now = time.monotonic()
        with self._lock:
            usable: list[tuple[int, str]] = []
            for idx, k in enumerate(self.api_keys):
                if k in self.disabled_keys:
                    continue
                if k in self.key_cooldowns and now < self.key_cooldowns[k]:
                    continue
                if (k, model) in self.unavailable_pairs:
                    continue
                usable.append((idx, k))
            return usable

    def _mark_key_cooldown(self, key: str, idx: int, duration_seconds: float) -> None:
        """Mark a key as cooling down."""
        with self._lock:
            self.key_cooldowns[key] = time.monotonic() + duration_seconds
            masked = mask_secret(key)
            logger.warning(
                f"⏳ Key index {idx} ({masked}) entered cooldown for {duration_seconds:.1f}s."
            )

    def _mark_key_disabled(self, key: str, idx: int, reason: str) -> None:
        """Permanently disable an invalid key (401/403/API_KEY_INVALID)."""
        with self._lock:
            self.disabled_keys.add(key)
            masked = mask_secret(key)
            logger.error(
                f"🚫 Key index {idx} ({masked}) permanently DISABLED: {reason} "
                f"({len(self.disabled_keys)}/{len(self.api_keys)} keys disabled)."
            )

    def _mark_pair_unavailable(self, key: str, model: str, idx: int) -> None:
        """Remember that (key, model) returned 404 so it is not retried on this run."""
        with self._lock:
            self.unavailable_pairs.add((key, model))
            logger.warning(
                f"⚠️ Model '{model}' unavailable on key index {idx} (404 NOT_FOUND). "
                f"Marked pair as unavailable for this session."
            )

    def get_reply(
        self, user_id: str, user_message: str, user_name: str = "User"
    ) -> str:
        """
        Generate intelligent response with sequential key failover,
        per-(key, model) 404 caching, and differentiated error messages.
        """
        clean_msg = user_message.strip()

        # Handle explicit /reset command
        if clean_msg.lower() == "/reset":
            self.reset_user_history(user_id)
            return "Aapki chat history reset kar di gayi hai! Naye sirey se bataiye, main aapki kya madad kar sakta hoon? 😊"

        # Check TTL response cache
        cache_key = f"{user_id}:{clean_msg.lower()}"
        now = time.monotonic()
        if cache_key in self.response_cache:
            cached_time, cached_reply = self.response_cache[cache_key]
            if now - cached_time < ANSWER_CACHE_TTL_SECONDS:
                logger.info(f"⚡ Served reply from TTL cache for {user_name}")
                return cached_reply

        # Retrieve relevant knowledge chunks
        relevant_chunks = self.kb.search(clean_msg)
        context_str = "\n---\n".join(relevant_chunks)
        system_instruction = build_system_prompt(context_str)

        # Retrieve user history and build contents payload ONCE before loops
        with self._lock:
            history = list(self.conversations.setdefault(user_id, []))
            if len(history) > MAX_CONVERSATION_TURNS * 2:
                history = history[-(MAX_CONVERSATION_TURNS * 2) :]
                self.conversations[user_id] = history

        contents_payload: list[types.Content] = []
        for item in history:
            contents_payload.append(item)

        user_content = types.Content(
            role="user",
            parts=[types.Part.from_text(text=f"[User ({user_name})]: {clean_msg}")],
        )
        contents_payload.append(user_content)

        # GenerateContentConfig with max_output_tokens=800 and 30s request timeout
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            max_output_tokens=800,
            http_options={"timeout": 30000},
        )

        start_time = time.monotonic()
        encountered_404_count = 0
        total_model_attempts = 0

        # Sequential model and key selection
        for model_candidate in self.models_fallback:
            usable_keys = self._get_usable_keys_for_model(model_candidate)
            if not usable_keys:
                continue

            for key_idx, current_key in usable_keys:
                total_model_attempts += 1
                client = self._clients[current_key]
                masked_key = mask_secret(current_key)

                # Up to 2 attempts on 5xx/timeout for the SAME key
                for attempt in range(2):
                    try:
                        response = client.models.generate_content(
                            model=model_candidate,
                            contents=contents_payload,
                            config=config,
                        )

                        reply = response.text.strip() if response.text else ""
                        if not reply:
                            raise ValueError(
                                "Received empty text response from Gemini."
                            )

                        latency = (time.monotonic() - start_time) * 1000

                        with self._lock:
                            if self.active_key_index != key_idx:
                                logger.info(
                                    f"🔄 Switched active key from index {self.active_key_index} to index {key_idx}"
                                )
                            self.active_key_index = key_idx

                        logger.info(
                            f"🧠 Gemini reply generated in {latency:.1f}ms | Model: {model_candidate} | "
                            f"Key index: {key_idx}/{len(self.api_keys)} ({masked_key}) | Length: {len(reply)}"
                        )

                        # Update conversation history
                        with self._lock:
                            user_turn = types.Content(
                                role="user",
                                parts=[types.Part.from_text(text=clean_msg)],
                            )
                            model_turn = types.Content(
                                role="model",
                                parts=[types.Part.from_text(text=reply)],
                            )
                            self.conversations[user_id].append(user_turn)
                            self.conversations[user_id].append(model_turn)
                            self.response_cache[cache_key] = (now, reply)

                        return reply

                    except Exception as e:
                        status_code = getattr(e, "code", None)
                        status_str = str(getattr(e, "status", "")).upper()
                        msg_str = str(getattr(e, "message", str(e)))
                        err_repr = (
                            f"code={status_code}, status={status_str}, msg={msg_str}"
                        )

                        # 1. 404 NOT_FOUND: Mark only (key, model) unavailable and try NEXT KEY for same model
                        if (
                            status_code == 404
                            or "NOT_FOUND" in status_str
                            or "404" in msg_str
                        ):
                            encountered_404_count += 1
                            self._mark_pair_unavailable(
                                current_key, model_candidate, key_idx
                            )
                            break  # move to next key for same model

                        # 2. 429 / RESOURCE_EXHAUSTED: Rate limit cooldown -> next key
                        if (
                            status_code == 429
                            or "RESOURCE_EXHAUSTED" in status_str
                            or "429" in msg_str
                            or "QUOTA" in status_str
                        ):
                            if (
                                "per-day" in msg_str.lower()
                                or "per day" in msg_str.lower()
                                or "daily" in msg_str.lower()
                            ):
                                cooldown = _seconds_until_pacific_midnight()
                                logger.warning(
                                    f"⏳ Daily quota exhausted on key index {key_idx}. "
                                    f"Cooling down until Pacific midnight ({cooldown:.0f}s)."
                                )
                            else:
                                cooldown = self.cooldown_seconds
                            self._mark_key_cooldown(current_key, key_idx, cooldown)
                            break  # move to next key

                        # 3. 401 / 403 / API_KEY_INVALID: Permanently disable ONLY this key
                        if (
                            status_code in (401, 403)
                            or "API_KEY_INVALID" in msg_str
                            or "API_KEY_INVALID" in status_str
                            or "PERMISSION_DENIED" in status_str
                        ):
                            self._mark_key_disabled(current_key, key_idx, err_repr)
                            break  # move to next key

                        # 4. Generic 400 INVALID_ARGUMENT: Do NOT disable key, do NOT burn other keys
                        if status_code == 400 or "INVALID_ARGUMENT" in status_str:
                            logger.error(
                                f"❌ Bad request (400 INVALID_ARGUMENT) on key index {key_idx}: {msg_str}. "
                                f"Key is NOT disabled and other keys will NOT be burned."
                            )
                            return "Request process nahi ho saki."

                        # 5. 5xx Server Error or Timeout: Retry once on SAME key, then next key
                        is_server_error = (
                            status_code is not None and 500 <= status_code < 600
                        ) or "timeout" in msg_str.lower()
                        if is_server_error:
                            if attempt == 0:
                                logger.warning(
                                    f"⚠️ Server error/timeout on key index {key_idx} ({masked_key}): {msg_str}. "
                                    f"Retrying once on same key..."
                                )
                                time.sleep(0.5)
                                continue
                            logger.warning(
                                f"⚠️ Retry on key index {key_idx} also failed. Moving to next key..."
                            )
                            break

                        logger.warning(
                            f"⚠️ Error on key index {key_idx} ({masked_key}) on model {model_candidate}: {e}. "
                            f"Trying next key..."
                        )
                        break

        latency = (time.monotonic() - start_time) * 1000

        # Differentiate failure: if everything encountered was 404
        if total_model_attempts > 0 and encountered_404_count >= total_model_attempts:
            logger.error(
                f"❌ ModelUnavailableError: All models returned 404 across all keys ({latency:.1f}ms)."
            )
            raise ModelUnavailableError(
                "Model abhi available nahi hai, owner ko batao."
            )

        logger.error(
            f"❌ AllKeysExhaustedError: All {len(self.api_keys)} keys exhausted or in cooldown ({latency:.1f}ms)."
        )
        raise AllKeysExhaustedError(
            "Abhi limit khatam ho gayi hai, thori der baad try karo."
        )
