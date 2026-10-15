"""
Shared pytest fixtures for whatsapp_gemini_agent test suite.
"""

from unittest.mock import MagicMock

import pytest

from state_manager import StateManager
from whatsapp_client import WhatsAppClient


@pytest.fixture
def fake_keys():
    """Return a standard list of mock Gemini API keys."""
    return ["fake_key_1", "fake_key_2", "fake_key_3"]


@pytest.fixture
def tmp_state(tmp_path):
    """Return a StateManager instance pointed at an isolated temporary file."""
    state_file = tmp_path / "temp_state.json"
    return StateManager(file_path=state_file)


@pytest.fixture
def mock_wa_client():
    """Return a mock WhatsAppClient."""
    mock = MagicMock(spec=WhatsAppClient)
    mock.send_message.return_value = {"messages": [{"id": "wamid.mock_outbound"}]}
    mock.mark_as_read.return_value = True
    return mock


@pytest.fixture
def mock_genai_client(monkeypatch):
    """
    Patch google.genai.Client and time.sleep to ensure tests never make real network calls
    and execute instantly without real time delays.
    """
    import google.genai as genai

    mock_client_factory = MagicMock()
    monkeypatch.setattr(genai, "Client", lambda *args, **kwargs: mock_client_factory)
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    monkeypatch.setattr("gemini_client.time.sleep", lambda seconds: None)
    return mock_client_factory
