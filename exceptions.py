"""
Custom exceptions for WhatsApp Gemini Agent.
"""

from __future__ import annotations


class AllKeysExhaustedError(Exception):
    """Raised when all configured Gemini API keys are invalid, cooling down, or exhausted."""

    def __init__(
        self,
        message: str = "Abhi limit khatam ho gayi hai, thori der baad try karo.",
    ):
        super().__init__(message)


class ModelUnavailableError(Exception):
    """Raised when models return 404 everywhere across all keys."""

    def __init__(
        self,
        message: str = "Model abhi available nahi hai, owner ko batao.",
    ):
        super().__init__(message)
