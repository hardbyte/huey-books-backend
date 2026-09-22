import copy
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.models.cms import ConnectionType, FlowDefinition, FlowNode, NodeType
from app.tests.integration.test_chat_runtime import _create_flow
from app.tests.integration.test_chat_runtime import (  # noqa: F401
    cleanup_flow as cleanup_flow,
)

ROOT = Path(__file__).parents[3]


def bookbot():
    return json.loads((ROOT / "scripts/fixtures/huey-bookbot-flow.json").read_text())


def test_seeded_bookbot_starts_with_usable_greeting(client, session, cleanup_flow):
    fixture = bookbot()
    data = fixture["flow_data"]
    flow = _create_flow(
        session,
        name=fixture["name"],
        entry_node_id=fixture["entry_node_id"],
        flow_data=data,
        nodes=[
            {
                "node_id": n["id"],
                "node_type": NodeType(n["type"]),
                "content": n["content"],
            }
            for n in data["nodes"]
        ],
        connections=[
            {**edge, "connection_type": ConnectionType(edge["type"])}
            for edge in data["connections"]
        ],
    )
    cleanup_flow[0].append(flow.id)
    response = client.post("/v1/chat/start", json={"flow_id": str(flow.id)})
    assert response.status_code == 201, response.text
    result = response.json()["next_node"]
    assert result["messages"], result
    assert result["next_node"]["node_id"] == "greeting_response"
    assert result["input_request"]["question"]["text"].strip()
    assert result["input_request"]["options"] == [
        {"label": "Hello Huey! 👋", "value": "hello"}
    ]


@pytest.mark.parametrize("prompt", ["", "A custom greeting"])
@pytest.mark.parametrize("seed", ["huey-bookbot", "custom"])
def test_greeting_migration_preserves_custom_content(session, prompt, seed):
    content = copy.deepcopy(
        next(
            n["content"]
            for n in bookbot()["flow_data"]["nodes"]
            if n["id"] == "greeting_response"
        )
    )
    content["question"]["text"] = prompt
    original = copy.deepcopy(content)
    flow = FlowDefinition(
        name="Synthetic greeting migration",
        version="1",
        info={"seed_key": seed},
        flow_data={
            "nodes": [
                {"id": "greeting_response", "type": "question", "content": content}
            ]
        },
    )
    session.add(flow)
    session.flush()
    session.add(
        FlowNode(
            flow_id=flow.id,
            node_id="greeting_response",
            node_type=NodeType.QUESTION,
            content=content,
        )
    )
    session.flush()
    spec = importlib.util.spec_from_file_location(
        "greeting_migration",
        ROOT / "alembic/versions/b683ed07ef45_complete_bookbot_greeting.py",
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    connection = session.connection()
    with (
        patch.object(migration.op, "get_bind", return_value=connection),
        patch.object(migration.op, "execute", side_effect=connection.exec_driver_sql),
    ):
        migration.upgrade()
        migration.upgrade()
    session.expire_all()
    node = session.scalar(select(FlowNode).where(FlowNode.flow_id == flow.id))
    expected = copy.deepcopy(original)
    if seed == "huey-bookbot" and not prompt:
        expected["question"]["text"] = "Ready to find your next book?"
    assert node.content == expected
    assert flow.flow_data["nodes"][0]["content"] == expected
    session.rollback()
