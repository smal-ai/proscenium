"""Identity-based original-turn oracle. Multiple assistant messages are legitimate."""


def assistant_projection(messages, events, required_reply):
    completed = [event for event in events if event["event_type"] == "assistant.completed"]
    message_ids = [message["message_id"] for message in messages]
    event_ids = [message["event_id"] for message in messages]
    identities_unique = (
        bool(messages)
        and len(message_ids) == len(set(message_ids))
        and len(event_ids) == len(set(event_ids))
    )
    projection_exact = len(completed) == len(messages) and all(
        len(
            [
                event
                for event in completed
                if event["native_event_id"] == message["event_id"]
                and event["payload"]["message_id"] == message["message_id"]
                and event["payload"]["text"] == message["content"]
            ]
        )
        == 1
        for message in messages
    )
    return {
        "assistant_native_ids_unique": bool(identities_unique),
        "each_assistant_message_projected_once": bool(projection_exact),
        "meaningful_final_marker_context_reply": any(
            all(value in message["content"] for value in required_reply) for message in messages
        ),
    }
