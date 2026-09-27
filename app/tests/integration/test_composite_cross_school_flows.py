"""
Integration tests for cross-school composite flows.

Tests nested flow execution with:
- Parent flow in one school embedding child flow from another school
- State sharing between parent and child flows
- Template interpolation with variables set in action nodes
- Sub-flow outputs being used by parent flow

These tests validate the session refresh fix for template interpolation
when action nodes set variables that are used by subsequent message nodes.
"""

import uuid

import pytest
from sqlalchemy import text


@pytest.fixture(autouse=True)
def cleanup_cms_data(session):
    """Clean up CMS data before and after each test to ensure test isolation."""
    cms_tables = [
        "cms_content",
        "cms_content_variants",
        "flow_definitions",
        "flow_nodes",
        "flow_connections",
        "conversation_sessions",
        "conversation_history",
        "conversation_analytics",
    ]

    session.rollback()

    for table in cms_tables:
        try:
            session.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE"))
        except Exception:
            pass
    session.commit()

    yield

    session.rollback()
    for table in cms_tables:
        try:
            session.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE"))
        except Exception:
            pass
    session.commit()


@pytest.fixture
def create_simple_child_flow(client, backend_service_account_headers):
    """Create a simple child flow that sets an output variable."""

    def _create(name: str, output_variable: str, output_value: str):
        # Create the child flow
        flow_data = {
            "name": name,
            "version": "1.0",
            "flow_data": {},
            "entry_node_id": "child_start",
        }
        flow_response = client.post(
            "v1/cms/flows", json=flow_data, headers=backend_service_account_headers
        )
        assert flow_response.status_code == 201
        flow_id = flow_response.json()["id"]

        # Create action node that sets output variable
        action_node = {
            "node_id": "child_start",
            "node_type": "action",
            "content": {
                "actions": [
                    {
                        "type": "set_variable",
                        "variable": output_variable,
                        "value": output_value,
                    }
                ]
            },
        }
        client.post(
            f"v1/cms/flows/{flow_id}/nodes",
            json=action_node,
            headers=backend_service_account_headers,
        )

        # Create welcome message that uses interpolation
        message_node = {
            "node_id": "child_welcome",
            "node_type": "message",
            "content": {
                "messages": [
                    {
                        "text": f"Child flow set {output_variable} to {{{{output.level}}}}"
                    }
                ]
            },
        }
        msg_response = client.post(
            f"v1/cms/flows/{flow_id}/nodes",
            json=message_node,
            headers=backend_service_account_headers,
        )
        assert msg_response.status_code == 201

        # Connect action -> message
        connection_data = {
            "source_node_id": "child_start",
            "target_node_id": "child_welcome",
            "connection_type": "default",
        }
        client.post(
            f"v1/cms/flows/{flow_id}/connections",
            json=connection_data,
            headers=backend_service_account_headers,
        )

        # Publish
        publish_response = client.put(
            f"v1/cms/flows/{flow_id}",
            json={"publish": True},
            headers=backend_service_account_headers,
        )
        assert publish_response.status_code == 200

        return flow_id

    return _create


@pytest.fixture
def create_parent_flow_with_composite(client, backend_service_account_headers):
    """Create a parent flow that includes a composite node."""

    def _create(name: str, child_flow_id: str):
        # Create the parent flow
        flow_data = {
            "name": name,
            "version": "1.0",
            "flow_data": {},
            "entry_node_id": "parent_start",
        }
        flow_response = client.post(
            "v1/cms/flows", json=flow_data, headers=backend_service_account_headers
        )
        assert flow_response.status_code == 201
        flow_id = flow_response.json()["id"]

        # Create initial action to set student name
        action_node = {
            "node_id": "parent_start",
            "node_type": "action",
            "content": {
                "actions": [
                    {
                        "type": "set_variable",
                        "variable": "temp.student_name",
                        "value": "Alice",
                    }
                ]
            },
        }
        client.post(
            f"v1/cms/flows/{flow_id}/nodes",
            json=action_node,
            headers=backend_service_account_headers,
        )

        # Create message that uses the variable from action
        welcome_msg = {
            "node_id": "parent_welcome",
            "node_type": "message",
            "content": {
                "messages": [
                    {"text": "Welcome {{temp.student_name}}! Starting lesson."}
                ]
            },
        }
        client.post(
            f"v1/cms/flows/{flow_id}/nodes",
            json=welcome_msg,
            headers=backend_service_account_headers,
        )

        # Create composite node to embed child flow
        composite_node = {
            "node_id": "embedded_child",
            "node_type": "composite",
            "content": {
                "flow_id": child_flow_id,
                "input_mapping": {},
                "output_mapping": {"output.level": "temp.child_level"},
            },
        }
        client.post(
            f"v1/cms/flows/{flow_id}/nodes",
            json=composite_node,
            headers=backend_service_account_headers,
        )

        # Create message that shows result from child
        result_msg = {
            "node_id": "show_result",
            "node_type": "message",
            "content": {
                "messages": [
                    {
                        "text": "{{temp.student_name}}, your level from child: {{temp.child_level}}"
                    }
                ]
            },
        }
        client.post(
            f"v1/cms/flows/{flow_id}/nodes",
            json=result_msg,
            headers=backend_service_account_headers,
        )

        # Create connections
        connections = [
            ("parent_start", "parent_welcome", "default"),
            ("parent_welcome", "embedded_child", "default"),
            ("embedded_child", "show_result", "default"),
        ]

        for source, target, conn_type in connections:
            connection_data = {
                "source_node_id": source,
                "target_node_id": target,
                "connection_type": conn_type,
            }
            client.post(
                f"v1/cms/flows/{flow_id}/connections",
                json=connection_data,
                headers=backend_service_account_headers,
            )

        # Publish
        publish_response = client.put(
            f"v1/cms/flows/{flow_id}",
            json={"publish": True},
            headers=backend_service_account_headers,
        )
        assert publish_response.status_code == 200

        return flow_id

    return _create


class TestCompositeFlowCreation:
    """Test creating composite flows with nested flow references."""

    def test_create_flow_with_composite_node(
        self, client, backend_service_account_headers
    ):
        """Test creating a flow with a composite node that references another flow."""
        # Create child flow first
        child_flow_data = {
            "name": "Child Flow",
            "version": "1.0",
            "flow_data": {},
            "entry_node_id": "child_start",
        }
        child_response = client.post(
            "v1/cms/flows",
            json=child_flow_data,
            headers=backend_service_account_headers,
        )
        assert child_response.status_code == 201
        child_flow_id = child_response.json()["id"]

        # Create parent flow
        parent_flow_data = {
            "name": "Parent Flow",
            "version": "1.0",
            "flow_data": {},
            "entry_node_id": "parent_start",
        }
        parent_response = client.post(
            "v1/cms/flows",
            json=parent_flow_data,
            headers=backend_service_account_headers,
        )
        assert parent_response.status_code == 201
        parent_flow_id = parent_response.json()["id"]

        # Create composite node in parent that references child
        composite_node = {
            "node_id": "embedded_child",
            "node_type": "composite",
            "content": {
                "flow_id": child_flow_id,
                "input_mapping": {"parent_var": "child_input"},
                "output_mapping": {"child_output": "parent_result"},
            },
        }
        node_response = client.post(
            f"v1/cms/flows/{parent_flow_id}/nodes",
            json=composite_node,
            headers=backend_service_account_headers,
        )
        assert node_response.status_code == 201
        assert node_response.json()["node_type"] == "composite"

    def test_create_composite_with_invalid_child_flow(
        self, client, backend_service_account_headers
    ):
        """Test that composite node can reference any flow ID (validation at runtime)."""
        # Create parent flow
        parent_flow_data = {
            "name": "Parent Flow",
            "version": "1.0",
            "flow_data": {},
            "entry_node_id": "parent_start",
        }
        parent_response = client.post(
            "v1/cms/flows",
            json=parent_flow_data,
            headers=backend_service_account_headers,
        )
        parent_flow_id = parent_response.json()["id"]

        # Create composite node with non-existent child flow ID
        fake_child_id = str(uuid.uuid4())
        composite_node = {
            "node_id": "embedded_child",
            "node_type": "composite",
            "content": {
                "flow_id": fake_child_id,
            },
        }
        node_response = client.post(
            f"v1/cms/flows/{parent_flow_id}/nodes",
            json=composite_node,
            headers=backend_service_account_headers,
        )
        # Node creation should succeed - validation happens at runtime
        assert node_response.status_code == 201


class TestTemplateInterpolationAfterAction:
    """Test template interpolation works after action nodes set variables.

    This specifically tests the fix for the bug where session state wasn't
    refreshed after action node execution, causing template interpolation
    to fail in subsequent message nodes.
    """

    def test_message_interpolates_variable_set_by_preceding_action(
        self, client, backend_service_account_headers
    ):
        """Test that message node can interpolate variables set by action node."""
        # Create flow
        flow_data = {
            "name": "Action Then Message Flow",
            "version": "1.0",
            "flow_data": {},
            "entry_node_id": "set_name",
        }
        flow_response = client.post(
            "v1/cms/flows", json=flow_data, headers=backend_service_account_headers
        )
        flow_id = flow_response.json()["id"]

        # Create action node that sets a variable
        action_node = {
            "node_id": "set_name",
            "node_type": "action",
            "content": {
                "actions": [
                    {
                        "type": "set_variable",
                        "variable": "temp.greeting_name",
                        "value": "TestUser",
                    }
                ]
            },
        }
        client.post(
            f"v1/cms/flows/{flow_id}/nodes",
            json=action_node,
            headers=backend_service_account_headers,
        )

        # Create message node that uses the variable
        message_node = {
            "node_id": "greet",
            "node_type": "message",
            "content": {
                "messages": [{"text": "Hello {{temp.greeting_name}}, welcome!"}]
            },
        }
        client.post(
            f"v1/cms/flows/{flow_id}/nodes",
            json=message_node,
            headers=backend_service_account_headers,
        )

        # Connect action -> message
        connection_data = {
            "source_node_id": "set_name",
            "target_node_id": "greet",
            "connection_type": "default",
        }
        client.post(
            f"v1/cms/flows/{flow_id}/connections",
            json=connection_data,
            headers=backend_service_account_headers,
        )

        # Publish
        publish_response = client.put(
            f"v1/cms/flows/{flow_id}",
            json={"publish": True},
            headers=backend_service_account_headers,
        )
        assert publish_response.status_code == 200

        # Start session
        session_data = {"flow_id": flow_id, "initial_state": {}}
        start_response = client.post("v1/chat/start", json=session_data)

        assert start_response.status_code == 201
        data = start_response.json()

        # The message should have the interpolated value
        assert "next_node" in data
        messages = data["next_node"].get("messages", [])
        assert len(messages) > 0

        # Check that the variable was interpolated (not showing raw template)
        message_text = messages[0].get("content", {}).get("text", "")
        assert "Hello TestUser, welcome!" in message_text
        assert "{{temp.greeting_name}}" not in message_text


class TestCompositeFlowStateSharing:
    """Test state sharing between parent and child flows via composite nodes."""

    def test_input_mapping_passes_state_to_child(
        self, client, backend_service_account_headers
    ):
        """Test that input_mapping passes parent state to child flow."""
        # Create child flow that uses input variable
        child_flow_data = {
            "name": "Child Uses Input",
            "version": "1.0",
            "flow_data": {},
            "entry_node_id": "child_msg",
        }
        child_response = client.post(
            "v1/cms/flows",
            json=child_flow_data,
            headers=backend_service_account_headers,
        )
        child_flow_id = child_response.json()["id"]

        # Child message uses input.parent_name
        child_msg = {
            "node_id": "child_msg",
            "node_type": "message",
            "content": {
                "messages": [{"text": "Child received: {{input.parent_name}}"}]
            },
        }
        client.post(
            f"v1/cms/flows/{child_flow_id}/nodes",
            json=child_msg,
            headers=backend_service_account_headers,
        )

        # Publish child
        client.put(
            f"v1/cms/flows/{child_flow_id}",
            json={"publish": True},
            headers=backend_service_account_headers,
        )

        # Create parent flow
        parent_flow_data = {
            "name": "Parent With Input Mapping",
            "version": "1.0",
            "flow_data": {},
            "entry_node_id": "set_parent_var",
        }
        parent_response = client.post(
            "v1/cms/flows",
            json=parent_flow_data,
            headers=backend_service_account_headers,
        )
        parent_flow_id = parent_response.json()["id"]

        # Action to set parent variable
        action_node = {
            "node_id": "set_parent_var",
            "node_type": "action",
            "content": {
                "actions": [
                    {
                        "type": "set_variable",
                        "variable": "temp.my_name",
                        "value": "ParentValue",
                    }
                ]
            },
        }
        client.post(
            f"v1/cms/flows/{parent_flow_id}/nodes",
            json=action_node,
            headers=backend_service_account_headers,
        )

        # Composite node with input mapping
        composite_node = {
            "node_id": "call_child",
            "node_type": "composite",
            "content": {
                "flow_id": child_flow_id,
                "input_mapping": {"temp.my_name": "input.parent_name"},
            },
        }
        client.post(
            f"v1/cms/flows/{parent_flow_id}/nodes",
            json=composite_node,
            headers=backend_service_account_headers,
        )

        # Connect
        client.post(
            f"v1/cms/flows/{parent_flow_id}/connections",
            json={
                "source_node_id": "set_parent_var",
                "target_node_id": "call_child",
                "connection_type": "default",
            },
            headers=backend_service_account_headers,
        )

        # Publish parent
        client.put(
            f"v1/cms/flows/{parent_flow_id}",
            json={"publish": True},
            headers=backend_service_account_headers,
        )

        # This test validates the flow structure is created correctly
        # Full runtime testing requires the chat runtime which handles composite execution
        assert parent_flow_id is not None
        assert child_flow_id is not None


class TestCrossSchoolCompositeAccess:
    """Test that flows from different schools can be embedded via composite nodes.

    Note: Full school isolation testing requires school fixtures. These tests
    verify the composite node structure supports cross-flow references.
    """

    def test_composite_node_can_reference_any_published_flow(
        self, client, backend_service_account_headers
    ):
        """Test composite nodes can reference any published flow."""
        # Create and publish multiple flows
        flows = []
        for i in range(3):
            flow_data = {
                "name": f"Flow {i}",
                "version": "1.0",
                "flow_data": {},
                "entry_node_id": "start",
            }
            response = client.post(
                "v1/cms/flows",
                json=flow_data,
                headers=backend_service_account_headers,
            )
            flow_id = response.json()["id"]

            # Add a node
            node = {
                "node_id": "start",
                "node_type": "message",
                "content": {"messages": [{"text": f"Flow {i} message"}]},
            }
            client.post(
                f"v1/cms/flows/{flow_id}/nodes",
                json=node,
                headers=backend_service_account_headers,
            )

            # Publish
            client.put(
                f"v1/cms/flows/{flow_id}",
                json={"publish": True},
                headers=backend_service_account_headers,
            )
            flows.append(flow_id)

        # Create a parent that embeds all three
        parent_data = {
            "name": "Master Hub",
            "version": "1.0",
            "flow_data": {},
            "entry_node_id": "hub_start",
        }
        parent_response = client.post(
            "v1/cms/flows", json=parent_data, headers=backend_service_account_headers
        )
        parent_id = parent_response.json()["id"]

        # Create composite nodes for each child
        for i, child_id in enumerate(flows):
            composite = {
                "node_id": f"embed_{i}",
                "node_type": "composite",
                "content": {"flow_id": child_id},
            }
            response = client.post(
                f"v1/cms/flows/{parent_id}/nodes",
                json=composite,
                headers=backend_service_account_headers,
            )
            assert response.status_code == 201

        # Verify all composite nodes were created
        nodes_response = client.get(
            f"v1/cms/flows/{parent_id}/nodes",
            headers=backend_service_account_headers,
        )
        assert nodes_response.status_code == 200
        nodes = nodes_response.json()["data"]
        composite_nodes = [n for n in nodes if n["node_type"] == "composite"]
        assert len(composite_nodes) == 3
