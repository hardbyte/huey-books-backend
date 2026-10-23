import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.models.cms import ConnectionType, ContentType, NodeType
from app.services.chat_exceptions import NodeProcessingError
from app.tests.unit.test_question_completeness import harness as harness

FLOW = json.loads(
    (
        Path(__file__).resolve().parents[3] / "scripts/fixtures/huey-spelling-flow.json"
    ).read_text()
)["flow_data"]


@pytest.fixture
def spelling(harness):
    runtime, session, make_node, repo = harness
    session.user_id = None
    nodes = {
        n["id"]: make_node(n["id"], NodeType(n["type"]), n["content"])
        for n in FLOW["nodes"]
    }
    connections = [
        SimpleNamespace(
            source_node_id=c["source"],
            target_node_id=c["target"],
            connection_type=ConnectionType(c["type"]),
        )
        for c in FLOW["connections"]
    ]
    repo.get_node_connections.side_effect = lambda db, flow_id, source_node_id: [
        c for c in connections if c.source_node_id == source_node_id
    ]
    with (
        patch("app.repositories.chat_repository.chat_repo", repo),
        patch("app.services.action_processor.chat_repo", repo),
    ):
        yield runtime, session, nodes, repo


def content(text="Choose a spelling", answers=None):
    return SimpleNamespace(
        id=uuid4(),
        type=ContentType.QUESTION,
        content={
            "question_text": text,
            "answers": answers
            if answers is not None
            else [
                {"text": "Synthetic", "value": "correct"},
                {"text": "Synthetik", "value": "wrong"},
            ],
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ability,difficulty",
    [
        ("SPOT", "easy"),
        ("CAT_HAT", "easy"),
        ("TREEHOUSE", "medium"),
        ("CHARLIE_CHOCOLATE", "hard"),
        ("HARRY_POTTER", "hard"),
    ],
)
async def test_difficulty_routes_to_matching_content(spelling, ability, difficulty):
    runtime, session, nodes, repo = spelling
    session.state["user"]["reading_ability_key"] = ability
    with patch(
        "app.services.chat_runtime.CMSRepositoryImpl.get_random_content",
        new=AsyncMock(return_value=[content()]),
    ) as fetch:
        result = await runtime.process_node(
            AsyncMock(), nodes["pick_difficulty"], session
        )
    assert result["type"] == "question"
    assert result["question"]["text"] == "Choose a spelling"
    assert len(result["options"]) == 2
    assert session.state["temp"]["shown_spelling_ids"] == [result["content_id"]]
    assert fetch.await_args.kwargs["info_filters"] == {
        "difficulty": f"huey-spelling-{difficulty}"
    }
    assert fetch.await_args.kwargs["tags"] == ["huey-spelling"]
    assert fetch.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "node_id", ["spelling_q_one", "spelling_q_two", "spelling_q_three"]
)
@pytest.mark.parametrize("exhausted", [False, True])
async def test_empty_spelling_round_routes_without_an_answer(
    spelling, node_id, exhausted
):
    runtime, session, nodes, repo = spelling
    school_id, shown_id = uuid4(), uuid4()
    session.state["system"]["school_id"] = str(school_id)
    session.state["temp"] = {
        "spelling_tag": "huey-spelling-easy",
        "shown_spelling_ids": [str(shown_id)] if exhausted else [],
    }
    with patch(
        "app.services.chat_runtime.CMSRepositoryImpl.get_random_content",
        new=AsyncMock(return_value=[]),
    ) as fetch:
        result = await runtime.process_node(AsyncMock(), nodes[node_id], session)
    assert result["question_skipped"]
    assert result["next_node"] is nodes["spelling_unavailable"]
    assert session.state["system"]["_current_options"] == []
    repo.add_interaction_history.assert_not_awaited()
    assert fetch.await_args.kwargs["exclude_ids"] == ([shown_id] if exhausted else None)
    assert fetch.await_args.kwargs["school_id"] == school_id
    assert fetch.await_args.kwargs["include_public"] is True
    assert fetch.await_args.kwargs["info_filters"] == {
        "difficulty": "huey-spelling-easy"
    }
    assert fetch.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("ability", ["", "UNKNOWN"])
async def test_unknown_ability_does_not_silently_select_hard(spelling, ability):
    runtime, session, nodes, repo = spelling
    session.state["user"]["reading_ability_key"] = ability
    with patch(
        "app.services.chat_runtime.CMSRepositoryImpl.get_random_content",
        new=AsyncMock(),
    ) as fetch:
        result = await runtime.process_node(
            AsyncMock(), nodes["pick_difficulty"], session
        )
    assert result["type"] == "messages"
    assert result["node_id"] == "spelling_unavailable"
    fetch.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("selected", [content(text=" "), content(answers=[])])
async def test_malformed_selected_spelling_is_not_skipped(spelling, selected):
    runtime, session, nodes, repo = spelling
    with patch(
        "app.services.chat_runtime.CMSRepositoryImpl.get_random_content",
        new=AsyncMock(return_value=[selected]),
    ):
        with pytest.raises(NodeProcessingError):
            await runtime.process_node(AsyncMock(), nodes["spelling_q_one"], session)
    repo.add_interaction_history.assert_not_awaited()
