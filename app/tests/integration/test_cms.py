import uuid

import pytest
from sqlalchemy import text
from starlette import status


@pytest.fixture(autouse=True)
async def cleanup_cms_data(async_session):
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

    await async_session.rollback()

    # Clean up before test runs
    for table in cms_tables:
        try:
            await async_session.execute(
                text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE")
            )
        except Exception:
            # Table might not exist, skip it
            pass
    await async_session.commit()

    yield

    await async_session.rollback()

    # Clean up after test runs
    for table in cms_tables:
        try:
            await async_session.execute(
                text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE")
            )
        except Exception:
            # Table might not exist, skip it
            pass
    await async_session.commit()


def test_backend_service_account_can_list_joke_content(
    client, backend_service_account_headers
):
    response = client.get(
        "v1/cms/content?content_type=joke", headers=backend_service_account_headers
    )
    assert response.status_code == status.HTTP_200_OK


def test_backend_service_account_can_list_question_content(
    client, backend_service_account_headers
):
    response = client.get(
        "v1/cms/content?content_type=question", headers=backend_service_account_headers
    )
    assert response.status_code == status.HTTP_200_OK


# Content CRUD Operations Tests


def test_create_content(client, backend_service_account_headers):
    """Test creating new CMS content."""
    content_data = {
        "type": "joke",
        "content": {
            "text": "Why don't scientists trust atoms? Because they make up everything!",
            "category": "science",
        },
        "tags": ["science", "chemistry"],
        "status": "draft",
    }

    response = client.post(
        "v1/cms/content", json=content_data, headers=backend_service_account_headers
    )

    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["type"] == "joke"
    assert data["content"]["text"] == content_data["content"]["text"]
    assert data["tags"] == content_data["tags"]
    assert data["status"] == "draft"
    assert data["version"] == 1
    assert data["is_active"] is True
    assert "id" in data
    assert "created_at" in data


def test_get_content_by_id(client, backend_service_account_headers):
    """Test retrieving specific content by ID."""
    # First create content
    content_data = {
        "type": "fact",
        "content": {
            "text": "Octopuses have three hearts.",
            "source": "Marine Biology Facts",
        },
        "tags": ["animals", "ocean"],
    }

    create_response = client.post(
        "v1/cms/content", json=content_data, headers=backend_service_account_headers
    )
    content_id = create_response.json()["id"]

    # Get the content
    response = client.get(
        f"v1/cms/content/{content_id}", headers=backend_service_account_headers
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["id"] == content_id
    assert data["type"] == "fact"
    assert data["content"]["text"] == content_data["content"]["text"]


def test_get_nonexistent_content(client, backend_service_account_headers):
    """Test retrieving non-existent content returns 404."""
    fake_id = str(uuid.uuid4())
    response = client.get(
        f"v1/cms/content/{fake_id}", headers=backend_service_account_headers
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_update_content(client, backend_service_account_headers):
    """Test updating existing content."""
    # Create content first
    content_data = {
        "type": "quote",
        "content": {
            "text": "The only way to do great work is to love what you do.",
            "author": "Steve Jobs",
        },
        "tags": ["motivation"],
    }

    create_response = client.post(
        "v1/cms/content", json=content_data, headers=backend_service_account_headers
    )
    content_id = create_response.json()["id"]

    # Update the content
    update_data = {
        "content": {
            "text": "The only way to do great work is to love what you do.",
            "author": "Steve Jobs",
            "year": "2005",
        },
        "tags": ["motivation", "inspiration"],
    }

    response = client.put(
        f"v1/cms/content/{content_id}",
        json=update_data,
        headers=backend_service_account_headers,
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["content"]["year"] == "2005"
    assert "inspiration" in data["tags"]
    assert "updated_at" in data


def test_delete_content(client, backend_service_account_headers):
    """Test deleting content."""
    # Create content first
    content_data = {
        "type": "message",
        "content": {"text": "Temporary message for deletion test"},
    }

    create_response = client.post(
        "v1/cms/content", json=content_data, headers=backend_service_account_headers
    )
    content_id = create_response.json()["id"]

    # Delete the content
    response = client.delete(
        f"v1/cms/content/{content_id}", headers=backend_service_account_headers
    )

    assert response.status_code == status.HTTP_204_NO_CONTENT

    # Verify it's deleted
    get_response = client.get(
        f"v1/cms/content/{content_id}", headers=backend_service_account_headers
    )
    assert get_response.status_code == status.HTTP_404_NOT_FOUND


def test_update_content_status(client, backend_service_account_headers):
    """Test content status workflow."""
    # Create content
    content_data = {
        "type": "joke",
        "content": {"text": "Status workflow test joke"},
        "status": "draft",
    }

    create_response = client.post(
        "v1/cms/content", json=content_data, headers=backend_service_account_headers
    )
    content_id = create_response.json()["id"]
    original_version = create_response.json()["version"]

    # Update status to published
    status_update = {"status": "published", "comment": "Ready for production"}

    response = client.post(
        f"v1/cms/content/{content_id}/status",
        json=status_update,
        headers=backend_service_account_headers,
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["status"] == "published"
    assert data["version"] == original_version + 1  # Version should increment


# Content Filtering and Pagination Tests


def test_list_content_with_filters(client, backend_service_account_headers):
    """Test content listing with various filters."""
    # Create test content with different types and tags
    test_contents = [
        {"type": "joke", "content": {"text": "Joke 1"}, "tags": ["funny", "kids"]},
        {"type": "fact", "content": {"text": "Fact 1"}, "tags": ["science", "kids"]},
        {"type": "joke", "content": {"text": "Joke 2"}, "tags": ["funny", "adults"]},
    ]

    for content in test_contents:
        client.post(
            "v1/cms/content", json=content, headers=backend_service_account_headers
        )

    # Test filter by content type
    response = client.get(
        "v1/cms/content?content_type=joke", headers=backend_service_account_headers
    )
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len([item for item in data["data"] if item["type"] == "joke"]) >= 2

    # Test filter by tags
    response = client.get(
        "v1/cms/content?tags=kids", headers=backend_service_account_headers
    )
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    # Should find content with 'kids' tag
    kids_content = [item for item in data["data"] if "kids" in item.get("tags", [])]
    assert len(kids_content) >= 2

    # Test pagination
    response = client.get(
        "v1/cms/content?limit=1&skip=0", headers=backend_service_account_headers
    )
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data["data"]) == 1
    assert "pagination" in data
    assert data["pagination"]["limit"] == 1


def test_search_content(client, backend_service_account_headers):
    """Test full-text search functionality."""
    # Create content with searchable text
    content_data = {
        "type": "fact",
        "content": {"text": "Dolphins are highly intelligent marine mammals"},
        "tags": ["marine", "intelligence"],
    }

    client.post(
        "v1/cms/content", json=content_data, headers=backend_service_account_headers
    )

    # Search for content
    response = client.get(
        "v1/cms/content?search=dolphins", headers=backend_service_account_headers
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    # Should find at least the content we just created
    dolphin_content = [
        item
        for item in data["data"]
        if "dolphins" in item["content"].get("text", "").lower()
    ]
    assert len(dolphin_content) >= 1


# Content Variants Tests (A/B Testing)


def test_create_content_variant(client, backend_service_account_headers):
    """Test creating content variants for A/B testing."""
    # Create base content first
    content_data = {
        "type": "joke",
        "content": {"text": "Original joke for A/B testing"},
    }

    create_response = client.post(
        "v1/cms/content", json=content_data, headers=backend_service_account_headers
    )
    content_id = create_response.json()["id"]

    # Create variant
    variant_data = {
        "variant_key": "version_b",
        "variant_data": {"text": "Alternative joke for A/B testing", "tone": "casual"},
        "weight": 50,
        "conditions": {"user_segment": "beta_testers"},
    }

    response = client.post(
        f"v1/cms/content/{content_id}/variants",
        json=variant_data,
        headers=backend_service_account_headers,
    )

    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["variant_key"] == "version_b"
    assert data["variant_data"]["text"] == variant_data["variant_data"]["text"]
    assert data["weight"] == 50
    assert data["conditions"]["user_segment"] == "beta_testers"
    assert data["is_active"] is True


def test_list_content_variants(client, backend_service_account_headers):
    """Test listing variants for content."""
    # Create content and variants
    content_data = {"type": "message", "content": {"text": "Base message"}}

    create_response = client.post(
        "v1/cms/content", json=content_data, headers=backend_service_account_headers
    )
    content_id = create_response.json()["id"]

    # Create multiple variants
    variants = [
        {
            "variant_key": "variant_a",
            "variant_data": {"text": "Variant A"},
            "weight": 30,
        },
        {
            "variant_key": "variant_b",
            "variant_data": {"text": "Variant B"},
            "weight": 70,
        },
    ]

    for variant in variants:
        client.post(
            f"v1/cms/content/{content_id}/variants",
            json=variant,
            headers=backend_service_account_headers,
        )

    # List variants
    response = client.get(
        f"v1/cms/content/{content_id}/variants", headers=backend_service_account_headers
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data["data"]) == 2
    assert "pagination" in data

    variant_keys = [item["variant_key"] for item in data["data"]]
    assert "variant_a" in variant_keys
    assert "variant_b" in variant_keys


def test_update_variant_performance(client, backend_service_account_headers):
    """Test updating variant performance metrics."""
    # Create content and variant
    content_data = {
        "type": "question",
        "content": {"text": "Performance test question"},
    }

    create_response = client.post(
        "v1/cms/content", json=content_data, headers=backend_service_account_headers
    )
    content_id = create_response.json()["id"]

    variant_data = {
        "variant_key": "performance_test",
        "variant_data": {"text": "Variant for performance testing"},
    }

    variant_response = client.post(
        f"v1/cms/content/{content_id}/variants",
        json=variant_data,
        headers=backend_service_account_headers,
    )
    variant_id = variant_response.json()["id"]

    # Update performance data
    performance_data = {
        "impressions": 1000,
        "clicks": 150,
        "conversions": 25,
        "conversion_rate": 0.025,
    }

    response = client.post(
        f"v1/cms/content/{content_id}/variants/{variant_id}/performance",
        json=performance_data,
        headers=backend_service_account_headers,
    )

    assert response.status_code == status.HTTP_200_OK
    assert "message" in response.json()


# Flow Management Tests


def test_create_flow(client, backend_service_account_headers):
    """Test creating a new chatbot flow."""
    flow_data = {
        "name": "Welcome Flow",
        "description": "A simple welcome flow for new users",
        "version": "1.0",
        "flow_data": {
            "variables": {"user_name": {"type": "string", "default": "Guest"}}
        },
        "entry_node_id": "welcome_message",
    }

    response = client.post(
        "v1/cms/flows", json=flow_data, headers=backend_service_account_headers
    )

    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["name"] == "Welcome Flow"
    assert data["version"] == "1.0"
    assert data["entry_node_id"] == "welcome_message"
    assert data["is_published"] is False
    assert data["is_active"] is True
    assert "id" in data
    assert "created_at" in data


def test_list_flows(client, backend_service_account_headers):
    """Test listing flows with filters."""
    # Create test flows - don't include nodes in flow_data to avoid duplicates
    flows = [
        {
            "name": "Published Flow",
            "version": "1.0",
            "flow_data": {},  # Empty flow_data, we'll add nodes via API
            "entry_node_id": "start",
        },
        {
            "name": "Draft Flow",
            "version": "0.1",
            "flow_data": {},
            "entry_node_id": "start",
        },
    ]

    created_flows = []
    for flow in flows:
        response = client.post(
            "v1/cms/flows", json=flow, headers=backend_service_account_headers
        )
        created_flows.append(response.json())

    # Create a proper node for the published flow to pass validation
    flow_id = created_flows[0]["id"]

    # Create the start node that the flow references
    node_data = {
        "node_id": "start",
        "node_type": "message",
        "content": {"messages": [{"content": "Welcome to the flow"}]},
        "position": {"x": 100, "y": 100},
    }
    client.post(
        f"v1/cms/flows/{flow_id}/nodes",
        json=node_data,
        headers=backend_service_account_headers,
    )

    # Now publish the first flow
    publish_response = client.put(
        f"v1/cms/flows/{flow_id}",
        json={"publish": True},
        headers=backend_service_account_headers,
    )

    # Check if publish was successful
    assert publish_response.status_code == 200

    # List all flows
    response = client.get("v1/cms/flows", headers=backend_service_account_headers)

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data["data"]) >= 2

    # Filter by published status
    response = client.get(
        "v1/cms/flows?published=true", headers=backend_service_account_headers
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    published_flows = [f for f in data["data"] if f["is_published"]]
    assert len(published_flows) >= 1


def test_publish_flow(client, backend_service_account_headers):
    """Test publishing and unpublishing flows."""
    # Create flow with proper nodes
    flow_data = {
        "name": "Publish Test Flow",
        "version": "1.0",
        "flow_data": {
            "nodes": [
                {
                    "id": "start",
                    "type": "message",
                    "content": {"text": "Welcome to the test flow"},
                    "data": {
                        "name": "Start Node",
                    },
                    "position": {"x": 100, "y": 100},
                }
            ],
            "connections": [],
        },
        "entry_node_id": "start",
    }

    create_response = client.post(
        "v1/cms/flows", json=flow_data, headers=backend_service_account_headers
    )
    flow_id = create_response.json()["id"]

    # Publish flow
    response = client.put(
        f"v1/cms/flows/{flow_id}",
        json={"publish": True},
        headers=backend_service_account_headers,
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["is_published"] is True

    # Verify it's published
    get_response = client.get(
        f"v1/cms/flows/{flow_id}", headers=backend_service_account_headers
    )
    assert get_response.json()["is_published"] is True

    # Unpublish flow
    response = client.put(
        f"v1/cms/flows/{flow_id}",
        json={"publish": False},
        headers=backend_service_account_headers,
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["is_published"] is False


def test_clone_flow(client, backend_service_account_headers):
    """Test cloning an existing flow."""
    # Create original flow
    flow_data = {
        "name": "Original Flow",
        "version": "1.0",
        "flow_data": {"test": "data"},
        "entry_node_id": "start",
    }

    create_response = client.post(
        "v1/cms/flows", json=flow_data, headers=backend_service_account_headers
    )
    original_flow_id = create_response.json()["id"]

    # Clone flow
    clone_data = {"name": "Cloned Flow", "version": "1.1"}

    response = client.post(
        f"v1/cms/flows/{original_flow_id}/clone",
        json=clone_data,
        headers=backend_service_account_headers,
    )

    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["name"] == "Cloned Flow"
    assert data["version"] == "1.1"
    # flow_data is normalized to always include nodes and connections arrays
    assert data["flow_data"]["test"] == "data"
    assert "nodes" in data["flow_data"]
    assert "connections" in data["flow_data"]
    assert data["id"] != original_flow_id  # Should be different ID


# Flow Node Management Tests


def test_create_flow_node(client, backend_service_account_headers):
    """Test creating nodes in a flow."""
    # Create flow first
    flow_data = {
        "name": "Node Test Flow",
        "version": "1.0",
        "flow_data": {},
        "entry_node_id": "welcome",
    }

    flow_response = client.post(
        "v1/cms/flows", json=flow_data, headers=backend_service_account_headers
    )
    flow_id = flow_response.json()["id"]

    # Create message node
    node_data = {
        "node_id": "welcome",
        "node_type": "message",
        "content": {
            "messages": [
                {
                    "type": "text",
                    "content": "Welcome to our chatbot!",
                    "typing_delay": 1.5,
                }
            ]
        },
        "position": {"x": 100, "y": 100},
    }

    response = client.post(
        f"v1/cms/flows/{flow_id}/nodes",
        json=node_data,
        headers=backend_service_account_headers,
    )

    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["node_id"] == "welcome"
    assert data["node_type"] == "message"
    assert data["content"]["messages"][0]["content"] == "Welcome to our chatbot!"
    assert data["flow_id"] == flow_id


def test_list_flow_nodes(client, backend_service_account_headers):
    """Test listing nodes in a flow."""
    # Create flow and nodes
    flow_data = {
        "name": "Multi-Node Flow",
        "version": "1.0",
        "flow_data": {},
        "entry_node_id": "start",
    }

    flow_response = client.post(
        "v1/cms/flows", json=flow_data, headers=backend_service_account_headers
    )
    flow_id = flow_response.json()["id"]

    # Create multiple nodes
    nodes = [
        {
            "node_id": "start",
            "node_type": "message",
            "content": {"messages": [{"content": "Start message"}]},
        },
        {
            "node_id": "question",
            "node_type": "question",
            "content": {"question": "What's your name?", "variable": "name"},
        },
    ]

    for node in nodes:
        client.post(
            f"v1/cms/flows/{flow_id}/nodes",
            json=node,
            headers=backend_service_account_headers,
        )

    # List nodes
    response = client.get(
        f"v1/cms/flows/{flow_id}/nodes", headers=backend_service_account_headers
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data["data"]) == 2

    node_ids = [node["node_id"] for node in data["data"]]
    assert "start" in node_ids
    assert "question" in node_ids


def test_update_flow_node(client, backend_service_account_headers):
    """Test updating a flow node."""
    # Create flow and node
    flow_data = {
        "name": "Update Test Flow",
        "version": "1.0",
        "flow_data": {},
        "entry_node_id": "test_node",
    }

    flow_response = client.post(
        "v1/cms/flows", json=flow_data, headers=backend_service_account_headers
    )
    flow_id = flow_response.json()["id"]

    node_data = {
        "node_id": "test_node",
        "node_type": "message",
        "content": {"messages": [{"content": "Original message"}]},
    }

    node_response = client.post(
        f"v1/cms/flows/{flow_id}/nodes",
        json=node_data,
        headers=backend_service_account_headers,
    )
    node_db_id = node_response.json()["id"]  # Get the database ID from response

    # Update node using database ID
    update_data = {
        "content": {"messages": [{"content": "Updated message", "typing_delay": 2.0}]}
    }

    response = client.put(
        f"v1/cms/flows/{flow_id}/nodes/{node_db_id}",
        json=update_data,
        headers=backend_service_account_headers,
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["content"]["messages"][0]["content"] == "Updated message"
    assert data["content"]["messages"][0]["typing_delay"] == 2.0


def test_delete_flow_node(client, backend_service_account_headers):
    """Test deleting a flow node."""
    # Create flow and node
    flow_data = {
        "name": "Delete Test Flow",
        "version": "1.0",
        "flow_data": {},
        "entry_node_id": "temp_node",
    }

    flow_response = client.post(
        "v1/cms/flows", json=flow_data, headers=backend_service_account_headers
    )
    flow_id = flow_response.json()["id"]

    node_data = {
        "node_id": "temp_node",
        "node_type": "message",
        "content": {"messages": [{"content": "Temporary node"}]},
    }

    node_response = client.post(
        f"v1/cms/flows/{flow_id}/nodes",
        json=node_data,
        headers=backend_service_account_headers,
    )
    node_db_id = node_response.json()["id"]  # Get the database ID from response

    # Delete node using database ID
    response = client.delete(
        f"v1/cms/flows/{flow_id}/nodes/{node_db_id}",
        headers=backend_service_account_headers,
    )

    assert response.status_code == status.HTTP_204_NO_CONTENT

    # Verify node is deleted
    get_response = client.get(
        f"v1/cms/flows/{flow_id}/nodes/{node_db_id}",
        headers=backend_service_account_headers,
    )
    assert get_response.status_code == status.HTTP_404_NOT_FOUND


# Flow Connection Tests


def test_create_flow_connection(client, backend_service_account_headers):
    """Test creating connections between flow nodes."""
    # Create flow and nodes
    flow_data = {
        "name": "Connection Test Flow",
        "version": "1.0",
        "flow_data": {},
        "entry_node_id": "start",
    }

    flow_response = client.post(
        "v1/cms/flows", json=flow_data, headers=backend_service_account_headers
    )
    flow_id = flow_response.json()["id"]

    # Create nodes
    nodes = [
        {"node_id": "start", "node_type": "message", "content": {"messages": []}},
        {"node_id": "end", "node_type": "message", "content": {"messages": []}},
    ]

    for node in nodes:
        client.post(
            f"v1/cms/flows/{flow_id}/nodes",
            json=node,
            headers=backend_service_account_headers,
        )

    # Create connection
    connection_data = {
        "source_node_id": "start",
        "target_node_id": "end",
        "connection_type": "default",
        "conditions": {},
    }

    response = client.post(
        f"v1/cms/flows/{flow_id}/connections",
        json=connection_data,
        headers=backend_service_account_headers,
    )

    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["source_node_id"] == "start"
    assert data["target_node_id"] == "end"
    assert data["connection_type"] == "default"
    assert data["flow_id"] == flow_id


def test_list_flow_connections(client, backend_service_account_headers):
    """Test listing connections in a flow."""
    # Create flow, nodes, and connections (using helper to avoid repetition)
    flow_data = {
        "name": "Connection List Test",
        "version": "1.0",
        "flow_data": {},
        "entry_node_id": "start",
    }

    flow_response = client.post(
        "v1/cms/flows", json=flow_data, headers=backend_service_account_headers
    )
    flow_id = flow_response.json()["id"]

    # Create nodes and connections
    nodes = ["start", "middle", "end"]
    for node_id in nodes:
        client.post(
            f"v1/cms/flows/{flow_id}/nodes",
            json={
                "node_id": node_id,
                "node_type": "message",
                "content": {"messages": []},
            },
            headers=backend_service_account_headers,
        )

    connections = [
        {
            "source_node_id": "start",
            "target_node_id": "middle",
            "connection_type": "default",
        },
        {
            "source_node_id": "middle",
            "target_node_id": "end",
            "connection_type": "default",
        },
    ]

    for connection in connections:
        client.post(
            f"v1/cms/flows/{flow_id}/connections",
            json=connection,
            headers=backend_service_account_headers,
        )

    # List connections
    response = client.get(
        f"v1/cms/flows/{flow_id}/connections", headers=backend_service_account_headers
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data["data"]) == 2

    # Verify connection details
    source_nodes = [conn["source_node_id"] for conn in data["data"]]
    assert "start" in source_nodes
    assert "middle" in source_nodes


def test_delete_flow_connection(client, backend_service_account_headers):
    """Test deleting a flow connection."""
    # Create flow, nodes, and connection
    flow_data = {
        "name": "Delete Connection Test",
        "version": "1.0",
        "flow_data": {},
        "entry_node_id": "start",
    }

    flow_response = client.post(
        "v1/cms/flows", json=flow_data, headers=backend_service_account_headers
    )
    flow_id = flow_response.json()["id"]

    # Create nodes
    for node_id in ["start", "end"]:
        client.post(
            f"v1/cms/flows/{flow_id}/nodes",
            json={
                "node_id": node_id,
                "node_type": "message",
                "content": {"messages": []},
            },
            headers=backend_service_account_headers,
        )

    # Create connection
    connection_response = client.post(
        f"v1/cms/flows/{flow_id}/connections",
        json={
            "source_node_id": "start",
            "target_node_id": "end",
            "connection_type": "default",
        },
        headers=backend_service_account_headers,
    )
    connection_id = connection_response.json()["id"]

    # Delete connection
    response = client.delete(
        f"v1/cms/flows/{flow_id}/connections/{connection_id}",
        headers=backend_service_account_headers,
    )

    assert response.status_code == status.HTTP_204_NO_CONTENT

    # Verify connection is deleted
    list_response = client.get(
        f"v1/cms/flows/{flow_id}/connections", headers=backend_service_account_headers
    )
    connections = list_response.json()["data"]
    connection_ids = [conn["id"] for conn in connections]
    assert connection_id not in connection_ids


# Authorization Tests


def test_unauthorized_access(client):
    """Test that CMS endpoints require proper authorization."""
    # Test that CMS endpoints return 401 without authorization
    endpoints_and_methods = [
        ("v1/cms/content", "POST"),
        ("v1/cms/content", "GET"),
        ("v1/cms/flows", "POST"),
        ("v1/cms/flows", "GET"),
    ]

    for endpoint, method in endpoints_and_methods:
        if method == "POST":
            response = client.post(endpoint, json={"test": "data"})
        else:
            response = client.get(endpoint)

        assert (
            response.status_code == 401
        ), f"{method} {endpoint} should require authorization"


def test_invalid_content_type(client, backend_service_account_headers):
    """Test validation of content types."""
    invalid_content = {"type": "invalid_type", "content": {"text": "This should fail"}}

    response = client.post(
        "v1/cms/content", json=invalid_content, headers=backend_service_account_headers
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


def test_validate_flow(client, backend_service_account_headers):
    """Test flow validation using CMS Workflow Service."""
    # Create flow with proper structure
    flow_data = {
        "name": "Validation Test Flow",
        "version": "1.0",
        "flow_data": {},
        "entry_node_id": "start",
    }

    flow_response = client.post(
        "v1/cms/flows", json=flow_data, headers=backend_service_account_headers
    )
    flow_id = flow_response.json()["id"]

    # Create the start node
    node_data = {
        "node_id": "start",
        "node_type": "message",
        "content": {"messages": [{"content": "Start message"}]},
        "position": {"x": 100, "y": 100},
    }
    client.post(
        f"v1/cms/flows/{flow_id}/nodes",
        json=node_data,
        headers=backend_service_account_headers,
    )

    # Validate flow using service endpoint
    response = client.get(
        f"v1/cms/flows/{flow_id}/validate", headers=backend_service_account_headers
    )

    assert response.status_code == 200
    data = response.json()

    # Should be valid with proper structure
    assert data["is_valid"] is True
    assert data["validation_errors"] == []
    assert data["nodes_count"] == 1
    assert data["connections_count"] == 0
    assert data["entry_node_id"] == "start"


def test_validate_nonexistent_flow(client, backend_service_account_headers):
    """Test validation of non-existent flow returns 404."""
    fake_id = str(uuid.uuid4())
    response = client.get(
        f"v1/cms/flows/{fake_id}/validate", headers=backend_service_account_headers
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Flow not found"
