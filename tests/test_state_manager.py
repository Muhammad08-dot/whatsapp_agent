"""
Unit tests for StateManager atomic persistence and offset tracking.
"""

from state_manager import StateManager


def test_state_manager_dedup_and_offset(tmp_path):
    """StateManager should track offset and deduplicate IDs."""
    state_file = tmp_path / "test_state.json"
    sm = StateManager(file_path=state_file)

    assert sm.is_handled("msg_1") is False
    sm.record_handled("msg_1")
    sm.set_offset(1050)
    sm.save()

    # Reopen to verify disk persistence
    sm2 = StateManager(file_path=state_file)
    assert sm2.next_offset == 1050
    assert sm2.is_handled("msg_1") is True
    assert sm2.is_handled("msg_2") is False
