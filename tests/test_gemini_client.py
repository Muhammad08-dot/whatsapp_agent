"""
Unit tests for GeminiClient sequential failover, 404 caching, cooldowns, and error types.
"""

from unittest.mock import MagicMock

import pytest
from google.genai.errors import APIError

from exceptions import AllKeysExhaustedError, ModelUnavailableError
from gemini_client import GeminiClient


@pytest.fixture(autouse=True)
def fast_time(monkeypatch):
    """Monkeypatch time.sleep and time in tests/test_gemini_client.py so tests run instantly without delay."""
    # Fast mock for genai.Client to avoid multi-second initialization per key
    import google.genai as genai

    monkeypatch.setattr(genai, "Client", lambda *args, **kwargs: MagicMock())
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    monkeypatch.setattr("gemini_client.time.sleep", lambda seconds: None)
    yield


def test_404_on_key_a_moves_to_key_b_for_same_model(tmp_path):
    """Key A returns 404 on model X, but Key B succeeds on model X without switching models."""
    client = GeminiClient(
        api_keys=["keyA", "keyB"], models_fallback=["modelX", "modelY"]
    )

    err_404 = APIError(
        code=404,
        response_json={
            "error": {"message": "Model not available for key", "status": "NOT_FOUND"}
        },
    )
    mock_resp = MagicMock()
    mock_resp.text = "Success on modelX via keyB"

    client._clients["keyA"].models.generate_content = MagicMock(side_effect=err_404)
    client._clients["keyB"].models.generate_content = MagicMock(return_value=mock_resp)

    reply = client.get_reply(user_id="user:1", user_message="test 404 key failover")
    assert reply == "Success on modelX via keyB"

    # Verify modelX was called on both keys, and modelY was NOT touched
    client._clients["keyA"].models.generate_content.assert_called_once()
    assert (
        client._clients["keyA"].models.generate_content.call_args[1]["model"]
        == "modelX"
    )
    client._clients["keyB"].models.generate_content.assert_called_once()
    assert (
        client._clients["keyB"].models.generate_content.call_args[1]["model"]
        == "modelX"
    )
    assert ("keyA", "modelX") in client.unavailable_pairs


def test_all_keys_404_on_model_x_switches_to_model_y(tmp_path):
    """When all keys return 404 on model X, client switches to model Y."""
    client = GeminiClient(api_keys=["k1", "k2"], models_fallback=["modelX", "modelY"])

    err_404 = APIError(
        code=404,
        response_json={"error": {"message": "Model retired", "status": "NOT_FOUND"}},
    )
    mock_resp = MagicMock()
    mock_resp.text = "Success on modelY"

    def mock_generate(model, contents, config):
        if model == "modelX":
            raise err_404
        return mock_resp

    client._clients["k1"].models.generate_content = MagicMock(side_effect=mock_generate)
    client._clients["k2"].models.generate_content = MagicMock(side_effect=mock_generate)

    reply = client.get_reply(user_id="user:2", user_message="switch model")
    assert reply == "Success on modelY"
    assert ("k1", "modelX") in client.unavailable_pairs
    assert ("k2", "modelX") in client.unavailable_pairs


def test_unavailable_pairs_not_retried_on_next_message(tmp_path):
    """(key, model) pairs recorded as 404 are not retried on future calls."""
    client = GeminiClient(api_keys=["k1", "k2"], models_fallback=["modelX", "modelY"])
    # Pre-mark (k1, modelX) as unavailable
    client.unavailable_pairs.add(("k1", "modelX"))

    mock_resp = MagicMock()
    mock_resp.text = "k2 answered modelX"
    client._clients["k1"].models.generate_content = MagicMock()
    client._clients["k2"].models.generate_content = MagicMock(return_value=mock_resp)

    reply = client.get_reply(user_id="user:3", user_message="skip k1")
    assert reply == "k2 answered modelX"
    # k1 was not even called for modelX
    client._clients["k1"].models.generate_content.assert_not_called()
    client._clients["k2"].models.generate_content.assert_called_once()


def test_sequential_success_keeps_using_key_1(tmp_path):
    """As long as key 1 succeeds, it is always used (no round-robin advancing)."""
    keys = ["k1", "k2", "k3"]
    client = GeminiClient(api_keys=keys)

    mock_resp = MagicMock()
    mock_resp.text = "Reply from k1"
    client._clients["k1"].models.generate_content = MagicMock(return_value=mock_resp)
    client._clients["k2"].models.generate_content = MagicMock()

    for i in range(3):
        res = client.get_reply(user_id="user:seq", user_message=f"msg_{i}")
        assert res == "Reply from k1"

    assert client._clients["k1"].models.generate_content.call_count == 3
    client._clients["k2"].models.generate_content.assert_not_called()


def test_429_on_key_1_switches_to_key_2_and_returns_after_cooldown(tmp_path):
    """Key 1 on 429 enters cooldown, key 2 is used; when cooldown expires, key 1 is used again."""
    client = GeminiClient(api_keys=["k1", "k2"], cooldown_seconds=65.0)

    err_429 = APIError(
        code=429,
        response_json={
            "error": {"message": "Quota exceeded", "status": "RESOURCE_EXHAUSTED"}
        },
    )
    mock_k2_resp = MagicMock()
    mock_k2_resp.text = "k2 reply"
    mock_k1_resp = MagicMock()
    mock_k1_resp.text = "k1 restored reply"

    client._clients["k1"].models.generate_content = MagicMock(
        side_effect=[err_429, mock_k1_resp]
    )
    client._clients["k2"].models.generate_content = MagicMock(return_value=mock_k2_resp)

    # Call 1: k1 fails, k2 answers
    rep1 = client.get_reply(user_id="user:cd", user_message="call1")
    assert rep1 == "k2 reply"
    assert "k1" in client.key_cooldowns

    # Call 2: while cooling down, k1 is skipped, k2 answers
    rep2 = client.get_reply(user_id="user:cd", user_message="call2")
    assert rep2 == "k2 reply"

    # Simulate cooldown expiry for k1
    client.key_cooldowns["k1"] = 0.0

    # Call 3: k1 is available again and takes priority over k2
    rep3 = client.get_reply(user_id="user:cd", user_message="call3")
    assert rep3 == "k1 restored reply"


def test_daily_quota_429_gets_long_cooldown(tmp_path):
    """Daily quota 429 sets cooldown to midnight Pacific (>65s)."""
    client = GeminiClient(api_keys=["k_daily", "k_ok"], cooldown_seconds=65.0)

    err_daily = APIError(
        code=429,
        response_json={
            "error": {
                "message": "Exceeded daily per-day quota",
                "status": "RESOURCE_EXHAUSTED",
            }
        },
    )
    mock_resp = MagicMock()
    mock_resp.text = "k_ok reply"

    client._clients["k_daily"].models.generate_content = MagicMock(
        side_effect=err_daily
    )
    client._clients["k_ok"].models.generate_content = MagicMock(return_value=mock_resp)

    reply = client.get_reply(user_id="user:daily", user_message="daily check")
    assert reply == "k_ok reply"
    assert client.key_cooldowns["k_daily"] > 0


def test_api_key_invalid_permanently_disables_only_that_key(tmp_path):
    """401 API_KEY_INVALID permanently disables only that key."""
    client = GeminiClient(api_keys=["bad", "good"])
    err_401 = APIError(
        code=401,
        response_json={
            "error": {"message": "API key not valid", "status": "UNAUTHENTICATED"}
        },
    )
    mock_resp = MagicMock()
    mock_resp.text = "good key reply"

    client._clients["bad"].models.generate_content = MagicMock(side_effect=err_401)
    client._clients["good"].models.generate_content = MagicMock(return_value=mock_resp)

    reply = client.get_reply(user_id="user:bad", user_message="test invalid")
    assert reply == "good key reply"
    assert "bad" in client.disabled_keys
    assert "good" not in client.disabled_keys


def test_generic_400_does_not_disable_key_and_returns_error_message(tmp_path):
    """Generic 400 returns friendly message and does not disable key or burn other keys."""
    client = GeminiClient(api_keys=["k1", "k2"])
    err_400 = APIError(
        code=400,
        response_json={
            "error": {"message": "Invalid argument", "status": "INVALID_ARGUMENT"}
        },
    )

    client._clients["k1"].models.generate_content = MagicMock(side_effect=err_400)
    client._clients["k2"].models.generate_content = MagicMock()

    reply = client.get_reply(user_id="user:400", user_message="bad arg")
    assert reply == "Request process nahi ho saki."
    assert "k1" not in client.disabled_keys
    client._clients["k2"].models.generate_content.assert_not_called()


def test_all_models_404_raises_model_unavailable_error(tmp_path):
    """When all models return 404 on all keys, ModelUnavailableError is raised."""
    client = GeminiClient(api_keys=["k1"], models_fallback=["m1", "m2"])
    err_404 = APIError(
        code=404,
        response_json={"error": {"message": "Model not found", "status": "NOT_FOUND"}},
    )

    client._clients["k1"].models.generate_content = MagicMock(side_effect=err_404)

    with pytest.raises(ModelUnavailableError):
        client.get_reply(user_id="user:m_unavail", user_message="test all 404")


def test_all_keys_exhausted_raises_all_keys_exhausted_error(tmp_path):
    """When all keys return 429 / quota error, AllKeysExhaustedError is raised."""
    client = GeminiClient(api_keys=["k1", "k2"], models_fallback=["m1"])
    err_429 = APIError(
        code=429,
        response_json={
            "error": {"message": "Quota exceeded", "status": "RESOURCE_EXHAUSTED"}
        },
    )

    client._clients["k1"].models.generate_content = MagicMock(side_effect=err_429)
    client._clients["k2"].models.generate_content = MagicMock(side_effect=err_429)

    with pytest.raises(AllKeysExhaustedError):
        client.get_reply(user_id="user:exhaust", user_message="test exhaust")


def test_status_report_formatting(tmp_path):
    """get_status_report masks keys and shows models and cooldown status."""
    client = GeminiClient(api_keys=["key_test_1", "key_test_2"])
    client.disabled_keys.add("key_test_1")

    report = client.get_status_report()
    assert "Agent & Gemini Status Report" in report
    assert "key_...st_1: DISABLED 🚫" in report
    assert "key_test_1" not in report  # Full key never leaked
    assert "key_test_2" not in report


def test_dedupe_and_cap_keys():
    """Keys are deduped and capped at 20."""
    raw = ["k1", "k2", "k1", "k3"] + [f"extra_{i}" for i in range(25)]
    client = GeminiClient(api_keys=raw)
    assert len(client.api_keys) == 20
    assert client.api_keys[:3] == ["k1", "k2", "k3"]


def test_5xx_timeout_retries_once_on_same_key_then_moves_on(tmp_path):
    """5xx/timeout retries once on the same key; if that fails too, moves to the next key."""
    client = GeminiClient(api_keys=["k1", "k2"])

    err_503 = APIError(
        code=503,
        response_json={
            "error": {"message": "Service unavailable", "status": "UNAVAILABLE"}
        },
    )
    mock_k2_resp = MagicMock()
    mock_k2_resp.text = "k2 rescued response"

    # k1 fails twice with 503
    client._clients["k1"].models.generate_content = MagicMock(side_effect=err_503)
    # k2 succeeds
    client._clients["k2"].models.generate_content = MagicMock(return_value=mock_k2_resp)

    reply = client.get_reply(user_id="user:5xx", user_message="retry check")
    assert reply == "k2 rescued response"

    # k1 was called exactly twice (1 initial + 1 retry on same key)
    assert client._clients["k1"].models.generate_content.call_count == 2
    # k2 was called once
    assert client._clients["k2"].models.generate_content.call_count == 1
