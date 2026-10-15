# Commands & Capabilities: Sufi_vela

## Available Commands
- `/status`: Generates a real-time health and diagnostic report of the agent. Shows total loaded keys, active key index, per-key cooldown status, permanent disable flags, and available Gemini models. (All API keys are strictly masked).
- `/reset`: Clears the user's active conversational memory history, resetting context back to a clean state for subsequent interactions.

## Core Capabilities
- **Multi-Turn Chat**: Natural multi-turn conversations in Roman Urdu, Urdu, and English tailored for WhatsApp.
- **Sequential Multi-Key Rotation**: Automatic failover across up to 20 Gemini API keys with intelligent 65s or Pacific midnight quota cooldowns.
- **Model Fallback**: Dynamic failover across high-availability Gemini models if any model is retired or unavailable.
- **Non-Text Graceful Fallback**: Automatically responds with a friendly guidance message when receiving stickers, voice notes, images, or media files.
- **Duplicate Prevention**: Inbound WhatsApp message IDs are deduplicated and tracked atomically in persistent state.
