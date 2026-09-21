import importlib.util
import json
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.cms import ConnectionType, FlowDefinition, FlowNode, NodeType
from app.tests.integration.test_chat_runtime import _create_flow
from app.tests.integration.test_chat_runtime import (  # noqa: F401
    cleanup_flow as cleanup_flow,
)


@pytest.mark.parametrize("via_message", [False, True])
def test_empty_question_routes_to_complete_question(
    client, session, cleanup_flow, via_message
):
    flow = _create_flow(
        session,
        name="Synthetic optional questions",
        entry_node_id="start",
        flow_data={"nodes": [], "connections": []},
        nodes=[
            {
                "node_id": "start",
                "node_type": NodeType.QUESTION,
                "content": {"question": {"text": "Begin?"}, "input_type": "text"},
            },
            {
                "node_id": "bridge",
                "node_type": NodeType.MESSAGE,
                "content": {"text": "Continuing"},
            },
            {
                "node_id": "optional",
                "node_type": NodeType.QUESTION,
                "content": {
                    "source": "random",
                    "source_config": {
                        "type": "question",
                        "tags": [str(uuid4())],
                        "info_filters": {"min_age": 8, "max_age": 8},
                    },
                    "input_type": "choice",
                    "on_empty": "next",
                },
            },
            {
                "node_id": "next",
                "node_type": NodeType.QUESTION,
                "content": {
                    "question": {"text": "Continue?"},
                    "input_type": "choice",
                    "options": [{"label": "Yes", "value": "yes"}],
                },
            },
        ],
        connections=[
            {
                "source": "start",
                "target": "bridge" if via_message else "optional",
                "connection_type": ConnectionType.DEFAULT,
            },
            {
                "source": "bridge",
                "target": "optional",
                "connection_type": ConnectionType.DEFAULT,
            },
        ],
    )
    cleanup_flow[0].append(flow.id)
    response = client.post("/v1/chat/start", json={"flow_id": str(flow.id)})
    assert response.status_code == 201, response.text
    start = response.json()
    response = client.post(
        f"/v1/chat/sessions/{start['session_token']}/interact",
        json={"input": "yes", "input_type": "text"},
        headers={"X-CSRF-Token": start["csrf_token"]},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["current_node_id"] == "next"
    assert result["input_request"]["question"]["text"] == "Continue?"
    assert result["input_request"]["options"] == [{"label": "Yes", "value": "yes"}]
    assert result["session_ended"] is False


@pytest.mark.parametrize("seed", ["huey-jokes", "huey-preferences"])
def test_optional_question_migration_keeps_filters_and_matches_seed(session, seed):
    path = Path(__file__).parents[3] / "scripts" / "fixtures" / f"{seed}-flow.json"
    fixture = json.loads(path.read_text())
    data = fixture["flow_data"]
    data["nodes"] = [
        node
        for node in data["nodes"]
        if node["id"] != "jokes_exhausted" and not node["id"].startswith("skip_pref_")
    ]
    data["connections"] = [
        edge
        for edge in data["connections"]
        if not edge["source"].startswith("skip_pref_")
    ]
    filters = {}
    for node in data["nodes"]:
        node["content"].pop("on_empty", None)
        if node["content"].get("source") == "random":
            filters[node["id"]] = node["content"]["source_config"].copy()
    flow = FlowDefinition(
        name="Synthetic migration fixture",
        version="1",
        info={"seed_key": seed},
        flow_data=data,
    )
    session.add(flow)
    session.flush()
    for node in data["nodes"]:
        session.add(
            FlowNode(
                flow_id=flow.id,
                node_id=node["id"],
                node_type=NodeType(node["type"]),
                content=node["content"],
            )
        )
    session.flush()
    migration_path = (
        Path(__file__).parents[3]
        / "alembic"
        / "versions"
        / "a572dc96de34_route_empty_optional_questions.py"
    )
    spec = importlib.util.spec_from_file_location(
        "optional_question_migration", migration_path
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    connection = session.connection()
    with (
        patch.object(migration.op, "get_bind", return_value=connection),
        patch.object(
            migration.op,
            "execute",
            side_effect=lambda sql: connection.exec_driver_sql(sql),
        ),
    ):
        migration.upgrade()
        migration.upgrade()
    session.expire_all()
    nodes = {
        node.node_id: node
        for node in session.scalars(select(FlowNode).where(FlowNode.flow_id == flow.id))
    }
    for source, original in filters.items():
        assert nodes[source].content["source_config"] == original
        target = nodes[source].content["on_empty"]
        assert target in nodes
        if seed == "huey-preferences":
            assert nodes[target].content["actions"][0]["type"] == "delete_variable"
    serialized = {node["id"]: node["content"] for node in flow.flow_data["nodes"]}
    assert serialized == {name: node.content for name, node in nodes.items()}
    session.rollback()


@pytest.mark.parametrize("entry_message", [False, True])
@pytest.mark.parametrize("initial_activity", [False, True])
def test_exhausted_subflow_returns_to_parent_without_reusing_answer(
    client, session, cleanup_flow, entry_message, initial_activity
):
    from app.models.cms import ConversationSession

    child = _create_flow(
        session,
        name="Synthetic optional subflow",
        entry_node_id="intro" if entry_message else "optional",
        flow_data={"nodes": [], "connections": []},
        nodes=[
            {
                "node_id": "intro",
                "node_type": NodeType.MESSAGE,
                "content": {"text": "Optional activity"},
            },
            {
                "node_id": "optional",
                "node_type": NodeType.QUESTION,
                "content": {
                    "source": "random",
                    "source_config": {"type": "question", "tags": [str(uuid4())]},
                    "input_type": "choice",
                    "on_empty": "skip",
                },
            },
            {
                "node_id": "skip",
                "node_type": NodeType.ACTION,
                "content": {
                    "actions": [
                        {"type": "delete_variable", "variable": "temp.pref_three_hue"}
                    ]
                },
            },
        ],
        connections=[
            {
                "source": "intro",
                "target": "optional",
                "connection_type": ConnectionType.DEFAULT,
            }
        ],
    )
    cleanup_flow[0].append(child.id)
    parent = _create_flow(
        session,
        name="Synthetic parent flow",
        entry_node_id="activity" if initial_activity else "start",
        flow_data={"nodes": [], "connections": []},
        nodes=[
            {
                "node_id": "start",
                "node_type": NodeType.QUESTION,
                "content": {"question": {"text": "Begin?"}, "input_type": "text"},
            },
            {
                "node_id": "activity",
                "node_type": NodeType.COMPOSITE,
                "content": {"composite_flow_id": str(child.id)},
            },
            {
                "node_id": "continue",
                "node_type": NodeType.QUESTION,
                "content": {
                    "question": {"text": "Continue?"},
                    "input_type": "choice",
                    "options": [{"label": "Yes", "value": "yes"}],
                },
            },
        ],
        connections=[
            {
                "source": "start",
                "target": "activity",
                "connection_type": ConnectionType.DEFAULT,
            },
            {
                "source": "activity",
                "target": "continue",
                "connection_type": ConnectionType.DEFAULT,
            },
        ],
    )
    cleanup_flow[0].append(parent.id)
    response = client.post(
        "/v1/chat/start",
        json={
            "flow_id": str(parent.id),
            "initial_state": {
                "temp": {
                    "current_answer": {"hue_map": {"adventure": 10}},
                    "pref_three_hue": {"adventure": 10},
                }
            },
        },
    )
    assert response.status_code == 201, response.text
    start = response.json()
    if initial_activity:
        question = start["next_node"]["next_node"]
        assert question["question"]["text"] == "Continue?"
        assert question["options"] == [{"label": "Yes", "value": "yes"}]
    else:
        response = client.post(
            f"/v1/chat/sessions/{start['session_token']}/interact",
            json={"input": "yes", "input_type": "text"},
            headers={"X-CSRF-Token": start["csrf_token"]},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["current_node_id"] == "continue"
        assert payload["input_request"]["question"]["text"] == "Continue?"
        assert payload["input_request"]["options"] == [{"label": "Yes", "value": "yes"}]
    session.expire_all()
    conversation = session.scalar(
        select(ConversationSession).where(
            ConversationSession.session_token == start["session_token"]
        )
    )
    assert conversation.state["temp"]["pref_three_hue"] is None
    assert (conversation.current_flow_id or conversation.flow_id) == parent.id
    assert conversation.info.get("flow_stack") == []


def test_seen_content_is_not_repeated_when_pool_is_exhausted(
    client, session, cleanup_flow
):
    from app.models.cms import CMSContent, ContentType, ContentVisibility

    tag = str(uuid4())
    item = CMSContent(
        type=ContentType.QUESTION,
        visibility=ContentVisibility.WRIVETED,
        is_active=True,
        tags=[tag],
        info={"min_age": 7, "max_age": 9},
        content={
            "question_text": "Pick one",
            "answers": [{"text": "First", "value": "first"}],
        },
    )
    session.add(item)
    session.commit()
    try:
        flow = _create_flow(
            session,
            name="Synthetic finite content pool",
            entry_node_id="optional",
            flow_data={"nodes": [], "connections": []},
            nodes=[
                {
                    "node_id": "optional",
                    "node_type": NodeType.QUESTION,
                    "content": {
                        "source": "random",
                        "source_config": {
                            "type": "question",
                            "tags": [tag],
                            "info_filters": {"min_age": 8, "max_age": 8},
                            "exclude_from": "temp.shown",
                        },
                        "track_shown_in": "temp.shown",
                        "input_type": "choice",
                        "on_empty": "next",
                    },
                },
                {
                    "node_id": "next",
                    "node_type": NodeType.QUESTION,
                    "content": {
                        "question": {"text": "Continue?"},
                        "input_type": "text",
                    },
                },
            ],
            connections=[
                {
                    "source": "optional",
                    "target": "optional",
                    "connection_type": ConnectionType.DEFAULT,
                }
            ],
        )
        cleanup_flow[0].append(flow.id)
        response = client.post("/v1/chat/start", json={"flow_id": str(flow.id)})
        assert response.status_code == 201, response.text
        start = response.json()
        assert start["next_node"]["question"]["text"] == "Pick one"
        assert start["next_node"]["options"][0]["value"] == "first"
        response = client.post(
            f"/v1/chat/sessions/{start['session_token']}/interact",
            json={"input": "first", "input_type": "choice"},
            headers={"X-CSRF-Token": start["csrf_token"]},
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["current_node_id"] == "next"
        assert result["input_request"]["question"]["text"] == "Continue?"
    finally:
        session.delete(item)
        session.commit()
