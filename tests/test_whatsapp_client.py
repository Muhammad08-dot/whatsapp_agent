"""
Unit tests for WhatsAppClient transport, split_text, and spec compliance.
"""

from unittest.mock import MagicMock, patch

from whatsapp_client import WhatsAppClient


def test_split_text_under_limit():
    """Text under 4096 chars should not be split."""
    text = "Hello world! This is a short message."
    chunks = WhatsAppClient.split_text(text, max_length=4096)
    assert len(chunks) == 1
    assert chunks[0] == text


def test_split_text_over_limit():
    """Text exceeding limit should split gracefully at spaces/newlines."""
    long_text = ("This is a long sentence. " * 200).strip()
    assert len(long_text) > 4096
    chunks = WhatsAppClient.split_text(long_text, max_length=1000)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 1000
    assert "".join(chunks).replace(" ", "") == long_text.replace(" ", "")


def test_poll_204_handling():
    """HTTP 204 should return None without raising error."""
    client = WhatsAppClient(api_token="test_token")
    mock_resp = MagicMock()
    mock_resp.status_code = 204

    with patch.object(client.session, "get", return_value=mock_resp):
        result = client.get_updates(offset=100)
        assert result is None


def test_parse_updates_success():
    """Updates payload should parse into typed dataclasses."""
    raw_response = {
        "next_offset": 54321,
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "contacts": [
                                {
                                    "wa_id": "user:999888",
                                    "profile": {"name": "TestUser"},
                                }
                            ],
                            "messages": [
                                {
                                    "id": "wamid.123",
                                    "from": "user:999888",
                                    "type": "text",
                                    "timestamp": "1736844652",
                                    "text": {"body": "Salam!"},
                                },
                                {
                                    "id": "wamid.456",
                                    "from": "user:999888",
                                    "type": "reaction",
                                    "timestamp": "1736844660",
                                    "reaction": {"emoji": "👍"},
                                },
                            ],
                            "statuses": [
                                {
                                    "id": "wamid.out1",
                                    "recipient_id": "user:999888",
                                    "status": "read",
                                    "timestamp": "1736844655",
                                }
                            ],
                        }
                    }
                ]
            }
        ],
    }

    messages, statuses, next_offset = WhatsAppClient.parse_updates(raw_response)
    assert next_offset == 54321
    assert len(messages) == 2
    assert messages[0].id == "wamid.123"
    assert messages[0].sender == "user:999888"
    assert messages[0].sender_name == "TestUser"
    assert messages[0].text == "Salam!"
    assert messages[0].is_text_message is True

    # Reaction has no text body
    assert messages[1].id == "wamid.456"
    assert messages[1].type == "reaction"
    assert messages[1].is_text_message is False

    # Status check
    assert len(statuses) == 1
    assert statuses[0].status == "read"


def test_parse_updates_contacts_optional_and_prefix_handling():
    """Verify profile name works when wa_id lacks 'user:' prefix, and falls back to Unknown when omitted."""
    raw = {
        "next_offset": 10,
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "contacts": [
                                {
                                    "wa_id": "999888",  # No prefix
                                    "profile": {"name": "Zaid"},
                                }
                            ],
                            "messages": [
                                {
                                    "id": "wamid.1",
                                    "from": "user:999888",
                                    "type": "text",
                                    "text": {"body": "Hi"},
                                },
                                {
                                    "id": "wamid.2",
                                    "from": "user:unknown_user",
                                    "type": "text",
                                    "text": {"body": "Whoami"},
                                },
                            ],
                        }
                    }
                ]
            }
        ],
    }
    messages, _, _ = WhatsAppClient.parse_updates(raw)
    assert len(messages) == 2
    assert messages[0].sender_name == "Zaid"
    assert messages[1].sender_name == "Unknown"


def test_send_message_payload_compliance():
    """Verify outbound payload includes messaging_product, valid to, type, text body."""
    client = WhatsAppClient(api_token="test_token")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"messages": [{"id": "wamid.out_test"}]}

    with patch.object(client.session, "post", return_value=mock_resp) as mock_post:
        res = client.send_message(to="user:12345", text="Test message")
        assert res is not None
        mock_post.assert_called_once()
        url = mock_post.call_args[0][0]
        json_body = mock_post.call_args[1]["json"]

        assert url == f"{client.base_url}/messages"
        assert json_body["messaging_product"] == "whatsapp"
        assert json_body["to"] == "user:12345"
        assert json_body["type"] == "text"
        assert json_body["text"]["body"] == "Test message"


def test_send_message_invalid_recipient_rejected():
    """Recipient must begin with user:<id> per spec."""
    client = WhatsAppClient(api_token="test_token")
    res = client.send_message(to="12345", text="Invalid recipient format")
    assert res is None
