import json
from collections import Counter


def book_identifier(book: object) -> str | None:
    if not isinstance(book, dict):
        return None
    identifier = book.get("isbn") or book.get("edition_isbn")
    return identifier if isinstance(identifier, str) and identifier else None


def normalize_feedback(user_input: str, offered: object) -> dict | None:
    if not isinstance(offered, list):
        return None
    try:
        feedback = json.loads(user_input)
    except (TypeError, ValueError):
        return None
    if not isinstance(feedback, dict):
        return None
    categories = ("liked", "disliked", "read")
    if any(not isinstance(feedback.get(key), list) for key in categories):
        return None
    allowed = {identifier for identifier in offered if isinstance(identifier, str)}
    choices = {
        key: {book_identifier(book) for book in feedback[key]} & allowed
        for key in categories
    }
    occurrences = Counter(
        identifier for values in choices.values() for identifier in values
    )
    return {
        key: sorted(identifier for identifier in values if occurrences[identifier] == 1)
        for key, values in choices.items()
    }
