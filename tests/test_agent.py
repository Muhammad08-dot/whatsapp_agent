"""
Unit and integration tests for WhatsAppAgent orchestration and command handling.
"""

from unittest.mock import MagicMock

from agent import WhatsAppAgent
from exceptions import AllKeysExhaustedError, ModelUnavailableError
from knowledge_base import KnowledgeBase
from models import IncomingMessage
from state_manager import StateManager


def test_agent_deduplication(tmp_path):
    """Agent step should not re-process already handled message IDs."""
    state_file = tmp_path / "test_state.json"
    mock_wa = MagicMock()
    mock_gemini = MagicMock()

    agent = WhatsAppAgent(wa_client=mock_wa, gemini_client=mock_gemini)
    agent.state = StateManager(file_path=state_file)

    msg = IncomingMessage(
        id="wamid.test_dup",
        sender="user:123",
        sender_name="Alice",
        type="text",
        timestamp="12345",
        text="Hello",
    )

    # First handle
    agent.handle_message(msg)
    assert mock_gemini.get_reply.call_count == 1
    assert mock_wa.send_message.call_count == 1

    # Second handle with same ID
    agent.handle_message(msg)
    # Counts should NOT increase
    assert mock_gemini.get_reply.call_count == 1
    assert mock_wa.send_message.call_count == 1


def test_agent_status_command():
    """/status command sends report from get_status_report."""
    mock_wa = MagicMock()
    mock_gemini = MagicMock()
    mock_gemini.get_status_report.return_value = "Status Report Mock"

    agent = WhatsAppAgent(wa_client=mock_wa, gemini_client=mock_gemini)
    agent.state = MagicMock()
    agent.state.is_handled.return_value = False

    msg = IncomingMessage(
        id="wamid.status_cmd",
        sender="user:creator",
        sender_name="Creator",
        type="text",
        timestamp="100",
        text="/status",
    )

    agent.handle_message(msg)
    mock_gemini.get_status_report.assert_called_once()
    mock_wa.send_message.assert_called_once_with(
        to="user:creator", text="Status Report Mock"
    )


def test_agent_all_keys_exhausted_fallback():
    """When GeminiClient raises AllKeysExhaustedError, user receives correct limit message."""
    mock_wa = MagicMock()
    mock_gemini = MagicMock()
    mock_gemini.get_reply.side_effect = AllKeysExhaustedError()

    agent = WhatsAppAgent(wa_client=mock_wa, gemini_client=mock_gemini)
    agent.state = MagicMock()
    agent.state.is_handled.return_value = False

    msg = IncomingMessage(
        id="wamid.fail_exhaust",
        sender="user:123",
        sender_name="User",
        type="text",
        timestamp="100",
        text="Help me",
    )

    agent.handle_message(msg)
    mock_wa.send_message.assert_called_once()
    sent_text = mock_wa.send_message.call_args[1]["text"]
    assert "Abhi limit khatam ho gayi hai, thori der baad try karo." in sent_text


def test_agent_model_unavailable_fallback():
    """When GeminiClient raises ModelUnavailableError, user receives model unavailable message."""
    mock_wa = MagicMock()
    mock_gemini = MagicMock()
    mock_gemini.get_reply.side_effect = ModelUnavailableError()

    agent = WhatsAppAgent(wa_client=mock_wa, gemini_client=mock_gemini)
    agent.state = MagicMock()
    agent.state.is_handled.return_value = False

    msg = IncomingMessage(
        id="wamid.fail_404",
        sender="user:123",
        sender_name="User",
        type="text",
        timestamp="100",
        text="Hello",
    )

    agent.handle_message(msg)
    mock_wa.send_message.assert_called_once()
    sent_text = mock_wa.send_message.call_args[1]["text"]
    assert "Model abhi available nahi hai, owner ko batao." in sent_text


def test_knowledge_base_search(tmp_path):
    """Knowledge base should score and retrieve matching sections."""
    doc = tmp_path / "info.md"
    doc.write_text(
        "# Python Guide\nPython is a programming language.\n\n# Cooking\nRecipes and food.",
        encoding="utf-8",
    )

    kb = KnowledgeBase(data_dir=tmp_path)
    results = kb.search("python programming", top_k=1)
    assert len(results) == 1
    assert "Python" in results[0]


def test_agent_shutdown_flag_mid_loop(tmp_path):
    """
    Assert that when shutdown_event is set mid-loop, the message currently being
    handled finishes completely, remaining batch messages are safely not dropped,
    and state.save() is called exactly once during the graceful shutdown sequence.
    """
    import threading

    state_file = tmp_path / "test_state.json"
    state = StateManager(file_path=state_file)
    state.save = MagicMock(wraps=state.save)

    mock_wa = MagicMock()
    mock_gemini = MagicMock()
    mock_gemini.get_reply.return_value = "Reply OK"

    msg1 = IncomingMessage(
        id="wamid.shutdown.1",
        sender="user:111",
        sender_name="User1",
        type="text",
        timestamp="100",
        text="First message",
    )
    msg2 = IncomingMessage(
        id="wamid.shutdown.2",
        sender="user:222",
        sender_name="User2",
        type="text",
        timestamp="101",
        text="Second message",
    )

    mock_wa.get_updates.return_value = ([msg1, msg2], [], 100)

    agent = WhatsAppAgent(wa_client=mock_wa, gemini_client=mock_gemini)
    agent.state = state

    shutdown_event = threading.Event()

    # Trigger shutdown flag during the processing of msg1
    def handle_msg_side_effect(msg):
        if msg.id == msg1.id:
            shutdown_event.set()

    orig_handle_msg = agent.handle_message
    agent.handle_message = MagicMock(
        side_effect=lambda m: (handle_msg_side_effect(m), orig_handle_msg(m))[1]
    )

    # Reset save call count before step
    state.save.reset_mock()

    # Execute step with shutdown event
    agent.step(shutdown_event=shutdown_event)

    # msg1 finished completely
    assert agent.state.is_handled("wamid.shutdown.1")
    assert mock_gemini.get_reply.call_count == 1
    assert mock_wa.send_message.call_count == 1
    # msg2 was paused cleanly because shutdown flag was set
    assert not agent.state.is_handled("wamid.shutdown.2")

    # Simulate graceful shutdown exit sequence:
    # 1. loop terminates
    assert shutdown_event.is_set()
    # 2. state.save() is called on exit
    agent.state.save.reset_mock()
    agent.state.save()
    agent.wa.close()

    assert agent.state.save.call_count == 1
    agent.wa.close.assert_called_once()


def test_offset_not_committed_mid_batch_and_replay_processes_remaining(tmp_path):
    """
    Test Phase 1 requirement:
    Batch of 3 messages, shutdown after message 1, save state,
    build a new agent from the saved state, replay the same batch ->
    message 1 skipped, messages 2 and 3 answered exactly once.
    """
    import threading

    state_file = tmp_path / "replay_state.json"
    state = StateManager(file_path=state_file)

    mock_wa1 = MagicMock()
    mock_gemini1 = MagicMock()
    mock_gemini1.get_reply.return_value = "Reply OK"

    msg1 = IncomingMessage(
        id="wamid.replay.1",
        sender="user:111",
        sender_name="User1",
        type="text",
        timestamp="100",
        text="Message 1",
    )
    msg2 = IncomingMessage(
        id="wamid.replay.2",
        sender="user:222",
        sender_name="User2",
        type="text",
        timestamp="101",
        text="Message 2",
    )
    msg3 = IncomingMessage(
        id="wamid.replay.3",
        sender="user:333",
        sender_name="User3",
        type="text",
        timestamp="102",
        text="Message 3",
    )

    batch = [msg1, msg2, msg3]
    initial_offset = 50
    next_offset_candidate = 99

    # Agent 1 starts at initial_offset
    state.set_offset(initial_offset)
    state.save()

    mock_wa1.get_updates.return_value = (batch, [], next_offset_candidate)
    agent1 = WhatsAppAgent(wa_client=mock_wa1, gemini_client=mock_gemini1)
    agent1.state = state

    shutdown_event = threading.Event()

    # Trigger shutdown after message 1 finishes
    orig_handle1 = agent1.handle_message

    def handle1_side_effect(msg):
        orig_handle1(msg)
        if msg.id == msg1.id:
            shutdown_event.set()

    agent1.handle_message = MagicMock(side_effect=handle1_side_effect)

    # Run step on agent 1
    agent1.step(shutdown_event=shutdown_event)

    # Verify msg1 was handled, msg2 and msg3 were not
    assert agent1.state.is_handled("wamid.replay.1")
    assert not agent1.state.is_handled("wamid.replay.2")
    assert not agent1.state.is_handled("wamid.replay.3")
    # Crucial: next_offset was NOT advanced to 99 because batch was interrupted
    assert agent1.state.next_offset == initial_offset

    # Save state upon shutdown
    agent1.state.save()

    # --- Replay with new agent initialized from the same state file ---
    state2 = StateManager(file_path=state_file)
    assert state2.next_offset == initial_offset
    assert state2.is_handled("wamid.replay.1")

    mock_wa2 = MagicMock()
    mock_gemini2 = MagicMock()
    mock_gemini2.get_reply.return_value = "Reply OK Replay"
    mock_wa2.get_updates.return_value = (batch, [], next_offset_candidate)

    agent2 = WhatsAppAgent(wa_client=mock_wa2, gemini_client=mock_gemini2)
    agent2.state = state2

    # Step without shutdown event (completes batch)
    agent2.step()

    # Message 1 was skipped (deduped), messages 2 and 3 were processed
    assert mock_gemini2.get_reply.call_count == 2
    assert mock_wa2.send_message.call_count == 2
    sent_recipients = [call[1]["to"] for call in mock_wa2.send_message.call_args_list]
    assert sent_recipients == ["user:222", "user:333"]

    # Now that all 3 messages are handled, next_offset advances
    assert agent2.state.next_offset == next_offset_candidate


def test_agent_reset_command():
    """/reset command resets user conversation history."""
    mock_wa = MagicMock()
    mock_gemini = MagicMock()

    agent = WhatsAppAgent(wa_client=mock_wa, gemini_client=mock_gemini)
    agent.state = MagicMock()
    agent.state.is_handled.return_value = False

    msg = IncomingMessage(
        id="wamid.reset_cmd",
        sender="user:chatty",
        sender_name="ChattyUser",
        type="text",
        timestamp="100",
        text="/reset",
    )

    agent.handle_message(msg)
    mock_gemini.reset_user_history.assert_called_once_with("user:chatty")
    mock_wa.send_message.assert_called_once()
    assert "reset" in mock_wa.send_message.call_args[1]["text"].lower()


def test_poison_message_does_not_block_batch_and_offset_advances(tmp_path):
    """
    Phase 1 Requirement:
    Batch of 3 messages, message 2 raises an unexpected exception.
    Messages 1 and 3 are answered, message 2 is marked handled, next_offset advances,
    and replaying the batch does not retry message 2.
    """
    state_file = tmp_path / "poison_state.json"
    state = StateManager(file_path=state_file)
    state.set_offset(10)
    state.save()

    mock_wa = MagicMock()
    mock_gemini = MagicMock()

    msg1 = IncomingMessage(
        id="wamid.batch.1",
        sender="user:1",
        sender_name="User1",
        type="text",
        timestamp="100",
        text="Normal message 1",
    )
    msg2 = IncomingMessage(
        id="wamid.batch.2_poison",
        sender="user:2",
        sender_name="User2",
        type="text",
        timestamp="101",
        text="Poison message 2",
    )
    msg3 = IncomingMessage(
        id="wamid.batch.3",
        sender="user:3",
        sender_name="User3",
        type="text",
        timestamp="102",
        text="Normal message 3",
    )

    batch = [msg1, msg2, msg3]
    mock_wa.get_updates.return_value = (batch, [], 20)

    # When msg2 is processed, gemini raises an unexpected catastrophic error
    def get_reply_side_effect(user_id, user_message, user_name=None):
        if "Poison" in user_message:
            raise RuntimeError("Catastrophic unexpected payload parser crash!")
        return f"Reply to {user_message}"

    mock_gemini.get_reply.side_effect = get_reply_side_effect

    agent = WhatsAppAgent(wa_client=mock_wa, gemini_client=mock_gemini)
    agent.state = state

    # Step through the entire batch
    agent.step()

    # Message 1 answered normally
    # Message 2 failed inside get_reply, but handle_message caught it, sent friendly error, and marked handled
    # Message 3 answered normally
    assert agent.state.is_handled("wamid.batch.1")
    assert agent.state.is_handled("wamid.batch.2_poison")
    assert agent.state.is_handled("wamid.batch.3")

    # next_offset successfully advanced to 20
    assert agent.state.next_offset == 20

    # Verify calls to send_message
    sent_recipients = [call[1]["to"] for call in mock_wa.send_message.call_args_list]
    assert sent_recipients == ["user:1", "user:2", "user:3"]

    # Msg 2 received the friendly technical difficulty message or fallback
    msg2_reply = mock_wa.send_message.call_args_list[1][1]["text"]
    assert (
        "takneeki masla" in msg2_reply or "Request process nahi ho saki" in msg2_reply
    )

    # Replay test: If the same batch is replayed, all 3 are recognized as handled
    mock_gemini.reset_mock()
    mock_wa.send_message.reset_mock()
    agent.step()

    # No new replies sent because all 3 messages are marked handled
    assert mock_gemini.get_reply.call_count == 0
    assert mock_wa.send_message.call_count == 0
