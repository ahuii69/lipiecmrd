"""Pola diagnostyczne historii w trace."""

from aihub.chat_contracts import ChatMessage, ChatTurnInput
from aihub.chat_history_trace import build_history_trace


def test_build_history_trace_previews():
    turn = ChatTurnInput(
        user_id="u",
        session_id="s",
        message="meta",
        history=[
            ChatMessage(role="user", content="pierwsza od użytkownika"),
            ChatMessage(role="assistant", content="odpowiedź"),
            ChatMessage(role="user", content="druga od usera"),
        ],
    )
    t = build_history_trace(turn)
    assert t["history_message_count"] == 3
    assert t["history_user_turns_in_payload"] == 2
    assert t["history_first_user_message_preview"] == "pierwsza od użytkownika"
    assert t["history_last_user_message_preview"] == "druga od usera"
