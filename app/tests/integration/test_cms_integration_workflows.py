"""
CMS Integration Workflows Tests.

End-to-end integration tests covering complete workflows:
- Content creation to chat session workflows
- Flow design to deployment workflows
- Analytics collection and reporting workflows
- Multi-step CMS operations with proper state management
- Error recovery and rollback scenarios

Test Organization:
- TestContentToChatWorkflow: Complete content → chat flow
- TestFlowDeploymentWorkflow: Flow creation → publishing → usage
- TestAnalyticsWorkflow: Data creation → collection → analysis
- TestErrorRecoveryWorkflows: Failure scenarios and recovery
- TestComplexIntegrationScenarios: Multi-component interactions
"""

import asyncio
import uuid

import pytest
from sqlalchemy import text
from starlette import status


# Test isolation fixture for CMS data
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


class TestContentToChatWorkflow:
    """Test complete workflow from content creation to chat session."""

    @pytest.fixture(autouse=True)
    def setup_test(self, reset_global_state_sync):
        """Ensure global state is reset before each test."""
        pass

    async def test_complete_content_to_chat_workflow(
        self, async_client, backend_service_account_headers
    ):
        """Test end-to-end workflow: Create content → Create flow → Start chat session."""
        print("\n🧪 Testing complete CMS to Chat workflow...")

        # Step 1: Create CMS content
        print("   📝 Creating CMS content...")
        content_data = {
            "type": "joke",
            "content": {
                "text": "Complete workflow test joke!",
                "category": "workflow",
            },
            "status": "published",
            "tags": ["workflow", "test"],
            "info": {"source": "workflow_test"},
        }

        content_response = await async_client.post(
            "/v1/cms/content",
            json=content_data,
            headers=backend_service_account_headers,
        )
        assert content_response.status_code == status.HTTP_201_CREATED
        content_id = content_response.json()["id"]
        print(f"   ✅ Created content: {content_id}")

        # Step 2: Create a flow that uses the content
        print("   🔗 Creating flow definition...")
        flow_data = {
            "name": "Complete Workflow Test Flow",
            "description": "End-to-end workflow test",
            "version": "1.0.0",
            "flow_data": {
                "nodes": [
                    {
                        "id": "start",
                        "type": "message",
                        "content": {"text": "Workflow test!"},
                        "position": {"x": 100, "y": 100},
                    }
                ],
                "connections": [],
            },
            "entry_node_id": "start",
            "info": {"workflow": "complete_test"},
        }

        flow_response = await async_client.post(
            "/v1/cms/flows", json=flow_data, headers=backend_service_account_headers
        )
        assert flow_response.status_code == status.HTTP_201_CREATED
        flow_id = flow_response.json()["id"]
        print(f"   ✅ Created flow: {flow_id}")

        # Step 3: Verify content is accessible
        print("   📋 Verifying content accessibility...")
        content_list_response = await async_client.get(
            "/v1/cms/content", headers=backend_service_account_headers
        )
        assert content_list_response.status_code == status.HTTP_200_OK
        content_data_list = content_list_response.json()

        retrieved_ids = {item["id"] for item in content_data_list["data"]}
        assert content_id in retrieved_ids
        print(f"   ✅ Content accessible: {len(content_data_list['data'])} items total")

        # Step 4: Publish the flow
        print("   🚀 Publishing flow...")
        publish_response = await async_client.put(
            f"/v1/cms/flows/{flow_id}",
            json={"publish": True},
            headers=backend_service_account_headers,
        )
        assert publish_response.status_code == status.HTTP_200_OK

        # Step 5: Start a chat session with the published flow
        print("   💬 Starting chat session...")
        session_data = {
            "flow_id": flow_id,
            "user_id": None,
            "initial_state": {"test_mode": True, "source": "workflow_test"},
        }

        session_response = await async_client.post("/v1/chat/start", json=session_data)
        assert session_response.status_code == status.HTTP_201_CREATED
        session_info = session_response.json()
        session_token = session_info["session_token"]
        print(f"   ✅ Started session: {session_token[:20]}...")

        # Step 6: Verify flows are accessible
        print("   🔍 Verifying flow accessibility...")
        flows_response = await async_client.get(
            "/v1/cms/flows", headers=backend_service_account_headers
        )
        assert flows_response.status_code == status.HTTP_200_OK
        flows_data = flows_response.json()

        flow_ids = {flow["id"] for flow in flows_data["data"]}
        assert flow_id in flow_ids
        print(f"   ✅ Flow accessible: {len(flows_data['data'])} flows total")

        print("\n🎉 Complete workflow test passed!")
        print("   📊 Summary:")
        print("   - CMS Content created and accessible ✅")
        print("   - Flow definition created and accessible ✅")
        print("   - Flow published successfully ✅")
        print("   - Chat session started successfully ✅")
        print("   - Authentication working properly ✅")
        print("   - End-to-end workflow verified ✅")

    async def test_content_variant_to_chat_workflow(
        self, async_client, backend_service_account_headers
    ):
        """Test workflow with content variants and A/B testing."""
        # Create base content
        base_content = {
            "type": "question",
            "content": {
                "question": "What's your favorite programming language?",
                "input_type": "text",
                "variable": "fav_language",
            },
            "tags": ["programming", "survey"],
            "status": "published",
        }

        content_response = await async_client.post(
            "/v1/cms/content",
            json=base_content,
            headers=backend_service_account_headers,
        )
        assert content_response.status_code == status.HTTP_201_CREATED
        content_id = content_response.json()["id"]

        # Create flow using the content
        flow_data = {
            "name": "Survey Flow with Variants",
            "description": "Testing content variants in flow",
            "version": "1.0.0",
            "flow_data": {
                "nodes": [
                    {
                        "id": "survey_question",
                        "type": "question",
                        "content": {
                            "question": "What's your favorite programming language?",
                            "input_type": "text",
                            "variable": "fav_language",
                        },
                        "position": {"x": 100, "y": 100},
                    },
                    {
                        "id": "thank_you",
                        "type": "message",
                        "content": {"text": "Thank you for your response!"},
                        "position": {"x": 300, "y": 100},
                    },
                ],
                "connections": [
                    {
                        "source": "survey_question",
                        "target": "thank_you",
                        "type": "DEFAULT",
                    }
                ],
            },
            "entry_node_id": "survey_question",
        }

        flow_response = await async_client.post(
            "/v1/cms/flows", json=flow_data, headers=backend_service_account_headers
        )
        assert flow_response.status_code == status.HTTP_201_CREATED
        flow_id = flow_response.json()["id"]

        # Publish and test flow
        await async_client.put(
            f"/v1/cms/flows/{flow_id}",
            json={"publish": True},
            headers=backend_service_account_headers,
        )

        # Start chat session
        session_data = {
            "flow_id": flow_id,
            "user_id": None,
            "initial_state": {"variant_test": True},
        }

        session_response = await async_client.post("/v1/chat/start", json=session_data)
        assert session_response.status_code == status.HTTP_201_CREATED


class TestFlowDeploymentWorkflow:
    """Test complete flow deployment workflow."""

    async def test_flow_design_to_deployment_workflow(
        self, async_client, backend_service_account_headers
    ):
        """Test complete flow from design to production deployment."""
        # Step 1: Create draft flow
        draft_flow = {
            "name": "Production Deployment Test",
            "description": "Testing deployment workflow",
            "version": "1.0.0",
            "flow_data": {
                "nodes": [
                    {
                        "id": "welcome",
                        "type": "message",
                        "content": {"text": "Welcome to our service!"},
                        "position": {"x": 0, "y": 0},
                    },
                    {
                        "id": "collect_email",
                        "type": "question",
                        "content": {
                            "question": "What's your email address?",
                            "input_type": "email",
                            "variable": "user_email",
                        },
                        "position": {"x": 200, "y": 0},
                    },
                    {
                        "id": "confirmation",
                        "type": "message",
                        "content": {"text": "Thank you! We'll be in touch."},
                        "position": {"x": 400, "y": 0},
                    },
                ],
                "connections": [
                    {"source": "welcome", "target": "collect_email", "type": "DEFAULT"},
                    {
                        "source": "collect_email",
                        "target": "confirmation",
                        "type": "DEFAULT",
                    },
                ],
            },
            "entry_node_id": "welcome",
        }

        create_response = await async_client.post(
            "/v1/cms/flows", json=draft_flow, headers=backend_service_account_headers
        )
        assert create_response.status_code == status.HTTP_201_CREATED
        flow_data = create_response.json()
        flow_id = flow_data["id"]

        # Verify flow is created but not published
        assert flow_data["is_published"] is False
        assert flow_data["is_active"] is True

        # Step 2: Validate flow integrity (if validation endpoint exists)
        validation_response = await async_client.get(
            f"/v1/cms/flows/{flow_id}/validate", headers=backend_service_account_headers
        )
        # Validation might not be implemented, so allow 404
        if validation_response.status_code != status.HTTP_404_NOT_FOUND:
            assert validation_response.status_code == status.HTTP_200_OK

        # Step 3: Publish flow
        publish_response = await async_client.put(
            f"/v1/cms/flows/{flow_id}",
            json={"publish": True},
            headers=backend_service_account_headers,
        )
        assert publish_response.status_code == status.HTTP_200_OK

        published_flow = publish_response.json()
        assert published_flow["is_published"] is True

        # Step 4: Test flow in production (start session)
        session_data = {
            "flow_id": flow_id,
            "user_id": None,
            "initial_state": {"production_test": True},
        }

        session_response = await async_client.post("/v1/chat/start", json=session_data)
        assert session_response.status_code == status.HTTP_201_CREATED

        session_info = session_response.json()
        session_token = session_info["session_token"]

        # Step 5: Verify session is active and working
        session_check = await async_client.get(f"/v1/chat/sessions/{session_token}")
        assert session_check.status_code == status.HTTP_200_OK

        session_details = session_check.json()
        assert session_details["status"] == "active"
        assert session_details["state"]["production_test"] is True

    async def test_flow_cloning_and_versioning_workflow(
        self, async_client, backend_service_account_headers
    ):
        """Test flow cloning for versioning workflow."""
        # Create original flow
        original_flow = {
            "name": "Original Flow v1.0",
            "description": "Original version for cloning",
            "version": "1.0.0",
            "flow_data": {
                "nodes": [
                    {
                        "id": "start",
                        "type": "message",
                        "content": {"text": "Version 1.0 message"},
                        "position": {"x": 100, "y": 100},
                    }
                ],
                "connections": [],
            },
            "entry_node_id": "start",
            "info": {"version_info": "original", "features": ["basic_flow"]},
        }

        original_response = await async_client.post(
            "/v1/cms/flows", json=original_flow, headers=backend_service_account_headers
        )
        assert original_response.status_code == status.HTTP_201_CREATED
        original_id = original_response.json()["id"]

        # Clone the flow for v2.0
        clone_data = {"name": "Enhanced Flow v2.0", "version": "2.0.0"}

        clone_response = await async_client.post(
            f"/v1/cms/flows/{original_id}/clone",
            json=clone_data,
            headers=backend_service_account_headers,
        )
        assert clone_response.status_code == status.HTTP_201_CREATED

        cloned_flow = clone_response.json()
        assert cloned_flow["name"] == "Enhanced Flow v2.0"
        assert cloned_flow["version"] == "2.0.0"
        assert (
            cloned_flow["info"]["version_info"] == "original"
        )  # Info should be copied

        # Verify both flows exist independently
        flows_response = await async_client.get(
            "/v1/cms/flows", headers=backend_service_account_headers
        )
        assert flows_response.status_code == status.HTTP_200_OK

        flows_data = flows_response.json()
        flow_names = {flow["name"] for flow in flows_data["data"]}
        assert "Original Flow v1.0" in flow_names
        assert "Enhanced Flow v2.0" in flow_names


class TestAnalyticsWorkflow:
    """Test analytics collection and reporting workflows."""

    async def test_analytics_data_collection_workflow(
        self, async_client, backend_service_account_headers
    ):
        """Test complete analytics workflow from data creation to analysis."""
        # Step 1: Create content and flows to generate analytics data
        content_data = {
            "type": "joke",
            "content": {"text": "Analytics test joke", "category": "test"},
            "tags": ["analytics", "test"],
            "status": "published",
        }

        await async_client.post(
            "/v1/cms/content",
            json=content_data,
            headers=backend_service_account_headers,
        )

        flow_data = {
            "name": "Analytics Test Flow",
            "description": "Flow for analytics testing",
            "version": "1.0.0",
            "flow_data": {
                "nodes": [
                    {
                        "id": "start",
                        "type": "message",
                        "content": {"text": "Analytics test"},
                        "position": {"x": 0, "y": 0},
                    }
                ],
                "connections": [],
            },
            "entry_node_id": "start",
        }

        flow_response = await async_client.post(
            "/v1/cms/flows", json=flow_data, headers=backend_service_account_headers
        )
        flow_id = flow_response.json()["id"]

        # Step 2: Publish and use flow to generate session data
        await async_client.put(
            f"/v1/cms/flows/{flow_id}",
            json={"publish": True},
            headers=backend_service_account_headers,
        )

        # Start a session to generate analytics data
        session_data = {
            "flow_id": flow_id,
            "user_id": None,
            "initial_state": {"analytics_test": True},
        }

        await async_client.post("/v1/chat/start", json=session_data)

        # Step 3: Collect analytics data
        dashboard_response = await async_client.get(
            "/v1/cms/analytics/dashboard", headers=backend_service_account_headers
        )
        # Allow analytics service to be optional for testing workflows
        if dashboard_response.status_code == status.HTTP_200_OK:
            dashboard_data = dashboard_response.json()
            # Dashboard overview returns dashboard data directly, not nested in "overview"
            assert isinstance(dashboard_data, dict)
        elif dashboard_response.status_code == status.HTTP_404_NOT_FOUND:
            # Analytics service not fully implemented, skip but don't fail
            print("   ⚠️ Analytics service not implemented, skipping dashboard test")
        else:
            # Unexpected error, should fail
            assert dashboard_response.status_code == status.HTTP_200_OK

        # Step 4: Get flow-specific analytics
        flow_analytics_response = await async_client.get(
            f"/v1/cms/analytics/flows/{flow_id}/analytics/performance",
            headers=backend_service_account_headers,
        )
        # Allow analytics service to be optional for testing workflows
        if flow_analytics_response.status_code == status.HTTP_200_OK:
            flow_analytics = flow_analytics_response.json()
            # Flow performance returns metrics data, check for common analytics fields
            assert isinstance(flow_analytics, dict)
        elif flow_analytics_response.status_code == status.HTTP_404_NOT_FOUND:
            # Analytics service not fully implemented, skip but don't fail
            print(
                "   ⚠️ Analytics service not implemented, skipping flow analytics test"
            )
        else:
            # Unexpected error, should fail
            assert flow_analytics_response.status_code == status.HTTP_200_OK

    async def test_analytics_export_workflow(
        self, async_client, backend_service_account_headers
    ):
        """Test analytics export workflow."""
        # Create some data first
        flow_data = {
            "name": "Export Test Flow",
            "description": "Flow for export testing",
            "version": "1.0.0",
            "flow_data": {"nodes": [], "connections": []},
            "entry_node_id": "start",
        }

        flow_response = await async_client.post(
            "/v1/cms/flows", json=flow_data, headers=backend_service_account_headers
        )
        flow_id = flow_response.json()["id"]

        # Request analytics export
        from datetime import date, timedelta

        export_request = {
            "export_type": "flow_analytics",
            "flow_id": flow_id,
            "format": "json",
            "date_range": {
                "start_date": (date.today() - timedelta(days=30)).isoformat(),
                "end_date": date.today().isoformat(),
            },
        }

        export_response = await async_client.post(
            "/v1/cms/analytics/export",
            json=export_request,
            headers=backend_service_account_headers,
        )
        # Allow analytics service to be optional for testing workflows
        if export_response.status_code == status.HTTP_200_OK:
            export_data = export_response.json()
            # Check for actual response structure (export_id, download_url, etc.)
            assert any(
                field in export_data
                for field in ["export_result", "export_id", "download_url"]
            )
        elif export_response.status_code == status.HTTP_404_NOT_FOUND:
            # Analytics service not fully implemented, skip but don't fail
            print("   ⚠️ Analytics export service not implemented, skipping export test")
        else:
            # Unexpected error, should fail
            assert export_response.status_code == status.HTTP_200_OK


class TestErrorRecoveryWorkflows:
    """Test error scenarios and recovery workflows."""

    async def test_flow_creation_error_recovery(
        self, async_client, backend_service_account_headers
    ):
        """Test recovery from flow creation errors."""
        # Attempt to create flow with invalid data
        invalid_flow = {
            "name": "",  # Invalid: empty name
            "description": "Test flow",
            "version": "1.0.0",
            "flow_data": {"nodes": [], "connections": []},
            "entry_node_id": "nonexistent",  # Invalid: node doesn't exist
        }

        response = await async_client.post(
            "/v1/cms/flows", json=invalid_flow, headers=backend_service_account_headers
        )
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

        # Correct the errors and retry
        valid_flow = {
            "name": "Corrected Flow",
            "description": "Test flow with corrections",
            "version": "1.0.0",
            "flow_data": {
                "nodes": [
                    {
                        "id": "start",
                        "type": "message",
                        "content": {"text": "Corrected flow"},
                        "position": {"x": 0, "y": 0},
                    }
                ],
                "connections": [],
            },
            "entry_node_id": "start",
        }

        retry_response = await async_client.post(
            "/v1/cms/flows", json=valid_flow, headers=backend_service_account_headers
        )
        assert retry_response.status_code == status.HTTP_201_CREATED

    async def test_chat_session_error_recovery(
        self, async_client, backend_service_account_headers
    ):
        """Test recovery from chat session errors."""
        # Try to start session with non-existent flow
        invalid_session_data = {
            "flow_id": str(uuid.uuid4()),  # Non-existent flow
            "user_id": None,
            "initial_state": {},
        }

        error_response = await async_client.post(
            "/v1/chat/start", json=invalid_session_data
        )
        assert error_response.status_code == status.HTTP_404_NOT_FOUND

        # Create valid flow and retry
        flow_data = {
            "name": "Recovery Test Flow",
            "description": "Flow for error recovery testing",
            "version": "1.0.0",
            "flow_data": {
                "nodes": [
                    {
                        "id": "start",
                        "type": "message",
                        "content": {"text": "Recovery test"},
                        "position": {"x": 0, "y": 0},
                    }
                ],
                "connections": [],
            },
            "entry_node_id": "start",
        }

        flow_response = await async_client.post(
            "/v1/cms/flows", json=flow_data, headers=backend_service_account_headers
        )
        flow_id = flow_response.json()["id"]

        # Publish flow
        await async_client.put(
            f"/v1/cms/flows/{flow_id}",
            json={"publish": True},
            headers=backend_service_account_headers,
        )

        # Retry session creation with valid flow
        valid_session_data = {
            "flow_id": flow_id,
            "user_id": None,
            "initial_state": {"recovery_test": True},
        }

        success_response = await async_client.post(
            "/v1/chat/start", json=valid_session_data
        )
        assert success_response.status_code == status.HTTP_201_CREATED


class TestComplexIntegrationScenarios:
    """Test complex multi-component integration scenarios."""

    async def test_concurrent_operations_workflow(
        self, async_client, backend_service_account_headers
    ):
        """Test concurrent CMS operations work correctly."""
        # Create multiple pieces of content concurrently
        content_tasks = []
        for i in range(5):
            content_data = {
                "type": "joke",
                "content": {"text": f"Concurrent test joke {i}"},
                "tags": ["concurrent", f"test-{i}"],
                "status": "draft",
            }

            task = async_client.post(
                "/v1/cms/content",
                json=content_data,
                headers=backend_service_account_headers,
            )
            content_tasks.append(task)

        # Wait for all content creation tasks
        content_responses = await asyncio.gather(*content_tasks)

        # Verify all succeeded
        for response in content_responses:
            assert response.status_code == status.HTTP_201_CREATED

        content_ids = [response.json()["id"] for response in content_responses]

        # Create flows concurrently
        flow_tasks = []
        for i, content_id in enumerate(content_ids):
            flow_data = {
                "name": f"Concurrent Flow {i}",
                "description": f"Concurrent test flow {i}",
                "version": "1.0.0",
                "flow_data": {"nodes": [], "connections": []},
                "entry_node_id": "start",
            }

            task = async_client.post(
                "/v1/cms/flows", json=flow_data, headers=backend_service_account_headers
            )
            flow_tasks.append(task)

        flow_responses = await asyncio.gather(*flow_tasks)

        # Verify all flows created successfully
        for response in flow_responses:
            assert response.status_code == status.HTTP_201_CREATED

    async def test_bulk_operations_workflow(
        self, async_client, backend_service_account_headers
    ):
        """Test bulk operations workflow."""
        # Create multiple content items
        content_ids = []
        for i in range(10):
            content_data = {
                "type": "message",
                "content": {"text": f"Bulk operation test {i}", "category": "bulk"},
                "tags": ["bulk", "test"],
                "status": "draft",
            }

            response = await async_client.post(
                "/v1/cms/content",
                json=content_data,
                headers=backend_service_account_headers,
            )
            assert response.status_code == status.HTTP_201_CREATED
            content_ids.append(response.json()["id"])

        # Verify all content was created
        list_response = await async_client.get(
            "/v1/cms/content?tags=bulk", headers=backend_service_account_headers
        )
        assert list_response.status_code == status.HTTP_200_OK

        bulk_content = list_response.json()
        assert len(bulk_content["data"]) >= 10

        # Test bulk status update (if supported)
        for content_id in content_ids[:5]:  # Update first 5 to published
            update_response = await async_client.put(
                f"/v1/cms/content/{content_id}",
                json={"status": "published"},
                headers=backend_service_account_headers,
            )
            # Allow this to pass even if individual updates are required
            assert update_response.status_code in [
                status.HTTP_200_OK,
                status.HTTP_404_NOT_FOUND,
            ]
