from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.models.cms import FlowNode, NodeType, SessionStatus
from app.services.chat_exceptions import NodeProcessingError
from app.services.chat_runtime import ChatRuntime, QuestionNodeProcessor
from app.services.question_contract import validate_question


@pytest.mark.parametrize(
    "question,options,input_type",
    [
        (None, [], "choice"),
        ({"text": " "}, [{"label": "Yes", "value": "yes"}], "choice"),
        ({"text": "Choose"}, [], "image_choice"),
        ({"text": "Choose"}, [{"label": "Yes"}], "choice"),
        ({"text": "Choose"}, [{"value": "yes"}], "choice"),
    ],
)
def test_incomplete_input_request_is_rejected_and_logged(question, options, input_type):
    with patch("app.services.question_contract.get_logger") as log:
        with pytest.raises(NodeProcessingError):
            ChatRuntime._build_input_request(
                {"question": question, "options": options, "input_type": input_type}
            )
    assert log.return_value.error.call_args.args == ("Chat question incomplete",)
    assert set(log.return_value.error.call_args.kwargs) == {"reason", "input_type"}


def test_text_question_does_not_require_choices():
    validate_question({"question": {"text": "Your name?"}, "input_type": "text"})


@pytest.fixture
def harness():
    runtime = ChatRuntime()
    flow_id = uuid4()
    session = SimpleNamespace(
        id=uuid4(),
        flow_id=flow_id,
        current_flow_id=flow_id,
        current_node_id="start",
        state={
            "system": {"_current_options": [{"label": "Old", "value": "old"}]},
            "user": {"age_number": 8},
        },
        info={},
        revision=1,
        trace_enabled=False,
        status=SessionStatus.ACTIVE,
        session_token="synthetic",
    )
    nodes = {}

    def node(name, kind, content):
        item = FlowNode(node_id=name, flow_id=flow_id, node_type=kind, content=content)
        nodes[name] = item
        return item

    async def update(db, session_id, state_updates, **kwargs):
        for scope, values in state_updates.items():
            session.state.setdefault(scope, {}).update(values)
        for key in ["current_node_id", "current_flow_id"]:
            if kwargs.get(key) is not None:
                setattr(session, key, kwargs[key])
        session.revision += 1
        return session

    with patch("app.services.chat_runtime.chat_repo") as repo:
        repo.get_flow_node = AsyncMock(
            side_effect=lambda db, flow_id, node_id: nodes.get(node_id)
        )
        repo.update_session_state = AsyncMock(side_effect=update)
        repo.get_session_by_id = AsyncMock(return_value=session)
        repo.get_session_by_token = AsyncMock(return_value=session)
        repo.add_interaction_history = AsyncMock()
        repo.get_next_connection = AsyncMock(return_value=None)
        repo.get_node_connections = AsyncMock(return_value=[])
        repo.end_session = AsyncMock()
        yield runtime, session, node, repo


def optional_content(target="next"):
    return {
        "source": "random",
        "source_config": {
            "type": "question",
            "tags": ["synthetic"],
            "info_filters": {
                "min_age": "${user.age_number}",
                "max_age": "${user.age_number}",
            },
            "exclude_from": "temp.shown",
        },
        "input_type": "choice",
        "on_empty": target,
    }


@pytest.mark.asyncio
async def test_empty_optional_question_routes_without_recording_an_answer(harness):
    runtime, session, node, repo = harness
    target = node("next", NodeType.MESSAGE, {"messages": [{"text": "Moving on"}]})
    question = node("empty", NodeType.QUESTION, optional_content())
    with patch(
        "app.services.chat_runtime.CMSRepositoryImpl.get_random_content",
        new=AsyncMock(return_value=[]),
    ) as fetch:
        result = await runtime.process_node(AsyncMock(), question, session)
    assert result["next_node"] is target
    assert result["question_skipped"]
    assert session.state["system"]["_current_options"] == []
    repo.add_interaction_history.assert_not_awaited()
    assert fetch.await_args.kwargs["info_filters"] == {"min_age": 8, "max_age": 8}
    assert fetch.await_args.kwargs["include_public"] is True
    assert fetch.await_count == 1


@pytest.mark.asyncio
async def test_selection_preserves_school_and_seen_exclusions(harness):
    runtime, session, node, repo = harness
    school_id, shown_id = uuid4(), uuid4()
    session.state["system"]["school_id"] = str(school_id)
    session.state["temp"] = {"shown": [str(shown_id)]}
    node("next", NodeType.MESSAGE, {"messages": []})
    question = node("empty", NodeType.QUESTION, optional_content())
    with patch(
        "app.services.chat_runtime.CMSRepositoryImpl.get_random_content",
        new=AsyncMock(return_value=[]),
    ) as fetch:
        await runtime.process_node(AsyncMock(), question, session)
    assert fetch.await_args.kwargs["school_id"] == school_id
    assert fetch.await_args.kwargs["exclude_ids"] == [shown_id]
    assert fetch.await_args.kwargs["tags"] == ["synthetic"]
    assert fetch.await_count == 1


@pytest.mark.asyncio
async def test_database_error_is_not_treated_as_empty_content(harness):
    runtime, session, node, repo = harness
    question = node("empty", NodeType.QUESTION, optional_content())
    with patch(
        "app.services.chat_runtime.CMSRepositoryImpl.get_random_content",
        new=AsyncMock(side_effect=RuntimeError("database unavailable")),
    ):
        with pytest.raises(RuntimeError, match="database unavailable"):
            await runtime.process_node(AsyncMock(), question, session)
    repo.add_interaction_history.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_required_question_is_rejected_before_history(harness):
    runtime, session, node, repo = harness
    content = optional_content()
    content.pop("on_empty")
    question = node("required", NodeType.QUESTION, content)
    with patch.object(
        QuestionNodeProcessor, "_fetch_random_content", new=AsyncMock(return_value=None)
    ):
        with pytest.raises(NodeProcessingError):
            await runtime.process_node(AsyncMock(), question, session)
    repo.add_interaction_history.assert_not_awaited()


@pytest.mark.asyncio
async def test_initial_empty_question_advances_to_valid_question(harness):
    runtime, session, node, repo = harness
    node("start", NodeType.QUESTION, optional_content())
    node(
        "next",
        NodeType.QUESTION,
        {
            "question": {"text": "Continue?"},
            "input_type": "choice",
            "options": [{"label": "Continue", "value": "yes"}],
        },
    )
    with (
        patch(
            "app.services.chat_runtime.crud.flow.aget",
            new=AsyncMock(return_value=SimpleNamespace(entry_node_id="start")),
        ),
        patch.object(
            QuestionNodeProcessor,
            "_fetch_random_content",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = await runtime.get_initial_node(AsyncMock(), session.flow_id, session)
    assert result["next_node"]["question"]["text"] == "Continue?"
    assert session.current_node_id == "next"
    assert session.state["system"]["_current_options"] == [
        {"label": "Continue", "value": "yes"}
    ]


@pytest.mark.asyncio
async def test_interaction_skips_empty_question_and_finishes(harness):
    runtime, session, node, repo = harness
    node(
        "start",
        NodeType.QUESTION,
        {"question": {"text": "Start?"}, "input_type": "text"},
    )
    empty = node("empty", NodeType.QUESTION, optional_content())
    node(
        "next", NodeType.MESSAGE, {"messages": [{"type": "text", "text": "Moving on"}]}
    )
    with (
        patch.object(
            QuestionNodeProcessor,
            "process_response",
            new=AsyncMock(return_value={"next_node": empty}),
        ),
        patch.object(
            QuestionNodeProcessor,
            "_fetch_random_content",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = await runtime.process_interaction(AsyncMock(), session, "yes")
    assert result["session_ended"]
    assert "input_request" not in result
    assert any(
        message.get("content", {}).get("text") == "Moving on"
        for message in result["messages"]
    )
    assert session.current_node_id == "next"


@pytest.mark.asyncio
async def test_skip_to_acknowledgment_does_not_end_session(harness):
    runtime, session, node, repo = harness
    node(
        "start",
        NodeType.QUESTION,
        {"question": {"text": "Start?"}, "input_type": "text"},
    )
    empty = node("empty", NodeType.QUESTION, optional_content())
    node("next", NodeType.MESSAGE, {"text": "Moving on", "wait_for_ack": True})
    with (
        patch.object(
            QuestionNodeProcessor,
            "process_response",
            new=AsyncMock(return_value={"next_node": empty}),
        ),
        patch.object(
            QuestionNodeProcessor,
            "_fetch_random_content",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = await runtime.process_interaction(AsyncMock(), session, "yes")
    assert result["wait_for_acknowledgment"] is True
    assert result["session_ended"] is False
    repo.end_session.assert_not_awaited()
