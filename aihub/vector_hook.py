from .vector_engine import add_memory


def remember_turn(user_id, user_msg, assistant_msg):

    if user_msg:
        add_memory(user_msg, user_id=user_id)

    if assistant_msg:
        add_memory(assistant_msg, user_id=user_id)
