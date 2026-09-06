from __future__ import annotations

import json
from uuid import UUID

import pytest
from sqlalchemy import delete

from app.models.cms import (
    ChatTheme,
    ConnectionType,
    ConversationSession,
    FlowConnection,
    FlowDefinition,
    FlowNode,
    NodeType,
)


@pytest.fixture()
def cleanup_flow(session):
    created_flows: list[UUID] = []
    created_themes: list[UUID] = []

    yield created_flows, created_themes

    for flow_id in created_flows:
        session.execute(
            delete(ConversationSession).where(ConversationSession.flow_id == flow_id)
        )
        session.execute(delete(FlowConnection).where(FlowConnection.flow_id == flow_id))
        session.execute(delete(FlowNode).where(FlowNode.flow_id == flow_id))
        session.execute(delete(FlowDefinition).where(FlowDefinition.id == flow_id))
    for theme_id in created_themes:
        session.execute(delete(ChatTheme).where(ChatTheme.id == theme_id))
    session.commit()


def _create_flow(
    session,
    *,
    name: str,
    entry_node_id: str,
    flow_data: dict,
    nodes: list[dict],
    connections: list[dict],
) -> FlowDefinition:
    flow = FlowDefinition(
        name=name,
        description=None,
        version="1",
        flow_data=flow_data,
        entry_node_id=entry_node_id,
        is_published=True,
        is_active=True,
    )
    session.add(flow)
    session.flush()

    for node in nodes:
        session.add(
            FlowNode(
                flow_id=flow.id,
                node_id=node["node_id"],
                node_type=node["node_type"],
                content=node.get("content", {}),
                position=node.get("position", {"x": 0, "y": 0}),
                info=node.get("info", {}),
            )
        )

    for conn in connections:
        session.add(
            FlowConnection(
                flow_id=flow.id,
                source_node_id=conn["source"],
                target_node_id=conn["target"],
                connection_type=conn.get("connection_type", ConnectionType.DEFAULT),
                conditions=conn.get("conditions", {}),
                info=conn.get("info", {}),
            )
        )

    session.commit()
    return flow


def test_chat_start_includes_theme_and_flow_name(client, session, cleanup_flow):
    created_flows, created_themes = cleanup_flow

    theme = ChatTheme(
        name="Test Theme",
        description=None,
        config={"colors": {"primary": "#111111"}},
        is_active=True,
    )
    session.add(theme)
    session.flush()
    created_themes.append(theme.id)

    flow = _create_flow(
        session,
        name="Theme Flow",
        entry_node_id="welcome",
        flow_data={"theme_id": str(theme.id), "nodes": [], "connections": []},
        nodes=[
            {
                "node_id": "welcome",
                "node_type": NodeType.MESSAGE,
                "content": {"text": "Welcome!"},
            }
        ],
        connections=[],
    )
    created_flows.append(flow.id)

    response = client.post("/v1/chat/start", json={"flow_id": str(flow.id)})
    assert response.status_code == 201

    payload = response.json()
    assert payload["flow_name"] == "Theme Flow"
    assert payload["theme_id"] == str(theme.id)
    assert payload["theme"]["id"] == str(theme.id)
    assert payload["theme"]["config"]["colors"]["primary"] == "#111111"
    assert payload["next_node"]["type"] == "messages"


def test_interact_returns_input_request_for_chained_question(
    client, session, cleanup_flow
):
    created_flows, _ = cleanup_flow

    flow = _create_flow(
        session,
        name="Chained Question Flow",
        entry_node_id="q1",
        flow_data={"nodes": [], "connections": []},
        nodes=[
            {
                "node_id": "q1",
                "node_type": NodeType.QUESTION,
                "content": {
                    "question": {"text": "What is your name?"},
                    "variable": "name",
                    "input_type": "text",
                },
            },
            {
                "node_id": "msg",
                "node_type": NodeType.MESSAGE,
                "content": {"text": "Thanks!"},
            },
            {
                "node_id": "q2",
                "node_type": NodeType.QUESTION,
                "content": {
                    "question": {"text": "How old are you?"},
                    "variable": "age",
                    "input_type": "text",
                },
            },
        ],
        connections=[
            {
                "source": "q1",
                "target": "msg",
                "connection_type": ConnectionType.DEFAULT,
            },
            {
                "source": "msg",
                "target": "q2",
                "connection_type": ConnectionType.DEFAULT,
            },
        ],
    )
    created_flows.append(flow.id)

    start_resp = client.post("/v1/chat/start", json={"flow_id": str(flow.id)})
    assert start_resp.status_code == 201
    start_payload = start_resp.json()

    session_token = start_payload["session_token"]
    csrf_token = start_payload["csrf_token"]

    interact_resp = client.post(
        f"/v1/chat/sessions/{session_token}/interact",
        json={"input": "Brian", "input_type": "text"},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert interact_resp.status_code == 200

    payload = interact_resp.json()
    assert payload["messages"]
    assert payload["input_request"]["question"]["text"] == "How old are you?"
    assert payload["input_request"]["input_type"] == "text"
    assert payload["input_request"]["variable"] == "age"


@pytest.mark.parametrize("enter_via_question", [False, True])
@pytest.mark.parametrize("child_intro", [False, True])
def test_terminal_book_feedback_returns_to_parent_question(
    client, session, cleanup_flow, enter_via_question, child_intro
):
    created_flows, _ = cleanup_flow
    books = [
        {"isbn": "9780140328721", "title": "QA Book One"},
        {"isbn": "9780142410318", "title": "QA Book Two"},
    ]
    child = _create_flow(
        session,
        name="Book feedback child",
        entry_node_id="intro" if child_intro else "seed",
        flow_data={},
        nodes=[
            {
                "node_id": "intro",
                "node_type": NodeType.MESSAGE,
                "content": {"messages": [{"text": "Here are your books"}]},
            },
            {
                "node_id": "seed",
                "node_type": NodeType.ACTION,
                "content": {
                    "actions": [
                        {
                            "type": "set_variable",
                            "variable": "temp.books",
                            "value": books,
                        }
                    ]
                },
            },
            {
                "node_id": "books",
                "node_type": NodeType.QUESTION,
                "content": {
                    "question": {"text": "Review these books"},
                    "input_type": "book_feedback",
                    "variable": "temp.feedback",
                    "book_source": "temp.books",
                },
            },
        ],
        connections=[
            {"source": "intro", "target": "seed"},
            {"source": "seed", "target": "books"},
        ],
    )
    created_flows.append(child.id)
    parent = _create_flow(
        session,
        name="Feedback then joke parent",
        entry_node_id="ready" if enter_via_question else "recommendations",
        flow_data={},
        nodes=[
            {
                "node_id": "ready",
                "node_type": NodeType.QUESTION,
                "content": {
                    "question": {"text": "Ready?"},
                    "input_type": "text",
                    "variable": "temp.ready",
                },
            },
            {
                "node_id": "recommendations",
                "node_type": NodeType.COMPOSITE,
                "content": {"composite_flow_id": str(child.id)},
            },
            {
                "node_id": "joke",
                "node_type": NodeType.QUESTION,
                "content": {
                    "question": {"text": "Would you like a joke?"},
                    "input_type": "choice",
                    "variable": "temp.joke",
                    "options": [{"label": "Yes please", "value": "yes"}],
                },
            },
        ],
        connections=[
            {"source": "ready", "target": "recommendations"},
            {"source": "recommendations", "target": "joke"},
        ],
    )
    created_flows.append(parent.id)
    started = client.post("/v1/chat/start", json={"flow_id": str(parent.id)})
    assert started.status_code == 201
    start = started.json()
    if enter_via_question:
        entered = client.post(
            f"/v1/chat/sessions/{start['session_token']}/interact",
            json={"input": "yes", "input_type": "text"},
            headers={"X-CSRF-Token": start["csrf_token"]},
        )
        assert entered.status_code == 200
        question = entered.json()["input_request"]
    else:
        question = (
            start["next_node"]["next_node"] if child_intro else start["next_node"]
        )
    assert question["input_type"] == "book_feedback"
    assert question["books"] == books
    persisted = (
        session.query(ConversationSession)
        .filter_by(session_token=start["session_token"])
        .one()
    )
    assert persisted.current_node_id == "books"
    assert persisted.current_flow_id == child.id
    response = client.post(
        f"/v1/chat/sessions/{start['session_token']}/interact",
        json={
            "input": json.dumps({"liked": [], "disliked": [], "read": []}),
            "input_type": "book_feedback",
        },
        headers={"X-CSRF-Token": start["csrf_token"]},
    )
    assert response.status_code == 200
    result = response.json()
    assert result["input_request"] is not None
    assert result["input_request"]["question"]["text"] == "Would you like a joke?"
    assert result["current_node_id"] == "joke"
    assert result["session_ended"] is False
    session.refresh(persisted)
    assert persisted.current_node_id == "joke"
    assert (persisted.current_flow_id or persisted.flow_id) == parent.id
    assert persisted.info["flow_stack"] == []


def test_trace_recording_failure_does_not_break_interaction(
    client, session, cleanup_flow, monkeypatch
):
    created_flows, _ = cleanup_flow

    flow = _create_flow(
        session,
        name="Trace Failure Flow",
        entry_node_id="welcome",
        flow_data={"nodes": [], "connections": []},
        nodes=[
            {
                "node_id": "welcome",
                "node_type": NodeType.MESSAGE,
                "content": {"text": "Welcome!"},
            }
        ],
        connections=[],
    )
    flow.trace_enabled = True
    flow.trace_sample_rate = 100
    session.commit()
    created_flows.append(flow.id)

    async def _fail_record_step_async(*args, **kwargs):
        raise RuntimeError("trace write failed")

    from app.services import chat_runtime as chat_runtime_module

    monkeypatch.setattr(
        chat_runtime_module.execution_trace_service,
        "record_step_async",
        _fail_record_step_async,
    )

    response = client.post("/v1/chat/start", json={"flow_id": str(flow.id)})
    assert response.status_code == 201
    payload = response.json()
    assert payload["session_token"]
    assert payload["next_node"]
    assert payload["next_node"]["type"] == "messages"


def test_condition_paths_use_dollar_notation(client, session, cleanup_flow):
    created_flows, _ = cleanup_flow

    flow = _create_flow(
        session,
        name="Condition Flow",
        entry_node_id="q1",
        flow_data={"nodes": [], "connections": []},
        nodes=[
            {
                "node_id": "q1",
                "node_type": NodeType.QUESTION,
                "content": {
                    "question": {"text": "Say yes or no"},
                    "variable": "answer",
                    "input_type": "text",
                },
            },
            {
                "node_id": "cond",
                "node_type": NodeType.CONDITION,
                "content": {
                    "conditions": [{"if": "temp.answer == 'yes'", "then": "$0"}],
                    "default_path": "$1",
                },
            },
            {
                "node_id": "yes",
                "node_type": NodeType.MESSAGE,
                "content": {"text": "You said yes"},
            },
            {
                "node_id": "no",
                "node_type": NodeType.MESSAGE,
                "content": {"text": "You said no"},
            },
        ],
        connections=[
            {
                "source": "q1",
                "target": "cond",
                "connection_type": ConnectionType.DEFAULT,
            },
            {
                "source": "cond",
                "target": "yes",
                "connection_type": ConnectionType.OPTION_0,
            },
            {
                "source": "cond",
                "target": "no",
                "connection_type": ConnectionType.OPTION_1,
            },
        ],
    )
    created_flows.append(flow.id)

    start_resp = client.post("/v1/chat/start", json={"flow_id": str(flow.id)})
    assert start_resp.status_code == 201
    start_payload = start_resp.json()
    session_token = start_payload["session_token"]
    csrf_token = start_payload["csrf_token"]

    interact_resp = client.post(
        f"/v1/chat/sessions/{session_token}/interact",
        json={"input": "yes", "input_type": "text"},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert interact_resp.status_code == 200
    payload = interact_resp.json()

    texts = []
    for msg in payload["messages"]:
        if isinstance(msg, dict):
            if isinstance(msg.get("text"), str):
                texts.append(msg["text"])
            content = msg.get("content")
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                texts.append(content["text"])

    assert "You said yes" in texts
