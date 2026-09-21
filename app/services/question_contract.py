"""Validation of rendered questions before requesting user input."""

from typing import Any

from structlog import get_logger

from app.services.chat_exceptions import NodeProcessingError

CHOICE_INPUTS = {"choice", "multiple_choice", "image_choice", "button", "carousel"}


def _nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_question(question: dict[str, Any]) -> None:
    prompt = question.get("question")
    input_type = question.get("input_type", "text")
    reason = None
    if not isinstance(prompt, dict) or not _nonempty_text(prompt.get("text")):
        reason = "missing_prompt"
    elif input_type in CHOICE_INPUTS:
        options = question.get("options")
        if not isinstance(options, list) or not options:
            reason = "missing_options"
        elif any(
            not isinstance(option, dict)
            or not any(
                _nonempty_text(option.get(key)) for key in ("label", "title", "text")
            )
            or not (
                _nonempty_text(option.get("value"))
                or isinstance(option.get("value"), (int, float, bool))
            )
            for option in options
        ):
            reason = "invalid_option"
    if reason:
        get_logger().error(
            "Chat question incomplete",
            reason=reason,
            input_type=input_type
            if input_type in CHOICE_INPUTS | {"text", "book_feedback"}
            else "other",
        )
        raise NodeProcessingError(
            "Cannot await input for an incomplete question",
            node_id=question.get("node_id"),
            node_type="question",
        )
