import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services.book_feedback import normalize_feedback
from app.services.chat_runtime import QuestionNodeProcessor, chat_runtime


def test_only_unique_offered_books_count_and_conflicts_are_excluded():
    feedback = {
        "liked": [
            {"isbn": "a"},
            {"isbn": "a"},
            None,
            "a",
            {"isbn": "unknown"},
            {"isbn": "b"},
        ],
        "disliked": [{"edition_isbn": "b"}],
        "read": [{"edition_isbn": "c"}],
    }
    assert normalize_feedback(json.dumps(feedback), ["a", "b", "c"]) == {
        "liked": ["a"],
        "disliked": [],
        "read": ["c"],
    }


@pytest.mark.parametrize("feedback", ["null", "[]", "invalid", '{"liked": null}', "{}"])
def test_invalid_feedback_is_unknown_not_zero(feedback):
    assert normalize_feedback(feedback, ["a"]) is None


def test_historical_offered_books_are_unknown():
    assert normalize_feedback('{"liked": [], "disliked": [], "read": []}', None) is None


@pytest.mark.asyncio
async def test_question_and_submission_record_verifiable_book_choices(monkeypatch):
    from app.repositories.chat_repository import chat_repo

    monkeypatch.setattr("app.services.chat_runtime.chat_repo", chat_repo)
    history = AsyncMock()
    offered = AsyncMock(return_value=["9780140328721"])
    monkeypatch.setattr(chat_repo, "add_interaction_history", history)
    monkeypatch.setattr(chat_repo, "get_offered_books", offered)
    monkeypatch.setattr(chat_repo, "get_node_connections", AsyncMock(return_value=[]))
    session = SimpleNamespace(
        id=uuid4(),
        state={
            "temp": {"books": [{"isbn": "9780140328721", "display_title": "Matilda"}]}
        },
        revision=1,
    )
    node = SimpleNamespace(
        node_id="books",
        flow_id=uuid4(),
        content={
            "text": "Your books",
            "input_type": "book_feedback",
            "book_source": "temp.books",
        },
    )
    processor = QuestionNodeProcessor(chat_runtime)
    result = await processor.process(AsyncMock(), node, session, {})
    assert result["books"] == session.state["temp"]["books"]
    assert history.call_args.kwargs["content"]["offered_isbns"] == ["9780140328721"]
    payload = json.dumps({"liked": result["books"] * 2, "disliked": [], "read": []})
    await processor.process_response(
        AsyncMock(), node, session, payload, "book_feedback"
    )
    offered.assert_awaited_once()
    assert offered.call_args.kwargs == {"session_id": session.id, "node_id": "books"}
    assert history.call_args.kwargs["content"]["validated_feedback"] == {
        "liked": ["9780140328721"],
        "disliked": [],
        "read": [],
    }
    offered.return_value = None
    await processor.process_response(
        AsyncMock(), node, session, payload, "book_feedback"
    )
    assert history.call_args.kwargs["content"]["validated_feedback"] is None
