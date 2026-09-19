"""Unit tests for CMS Workflow Service."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services.cms_workflow import CMSWorkflowService
from app.services.exceptions import (
    ContentWorkflowError,
    FlowNotFoundError,
    FlowValidationError,
)


def _make_mock_node(node_id: str, content: dict = None, node_type: str = "message"):
    node = MagicMock()
    node.node_id = node_id
    node.node_type = node_type
    node.content = content or {}
    node.created_at = MagicMock()
    return node


def _make_mock_connection(source: str, target: str):
    conn = MagicMock()
    conn.source_node_id = source
    conn.target_node_id = target
    return conn


def _make_mock_flow(
    flow_id=None,
    name="Test Flow",
    version="1.0.0",
    entry_node_id="start",
    nodes=None,
    connections=None,
    is_published=False,
):
    flow = MagicMock()
    flow.id = flow_id or uuid4()
    flow.name = name
    flow.version = version
    flow.entry_node_id = entry_node_id
    flow.nodes = nodes or [_make_mock_node("start")]
    flow.connections = connections or []
    flow.description = "Test"
    flow.flow_data = {}
    flow.info = {}
    flow.is_published = is_published
    flow.is_active = True
    flow.created_at = MagicMock(isoformat=MagicMock(return_value="2025-01-01T00:00:00"))
    flow.updated_at = MagicMock(isoformat=MagicMock(return_value="2025-01-01T00:00:00"))
    flow.published_at = None
    flow.published_by = None
    return flow


def _make_mock_content(
    content_id=None,
    content_type="joke",
    content_body=None,
    status="draft",
    is_active=True,
):
    content = MagicMock()
    content.id = content_id or uuid4()
    content.type = MagicMock(value=content_type)
    content.content = content_body or {"text": "Hello"}
    content.info = {}
    content.tags = ["test"]
    content.is_active = is_active
    content.status = MagicMock(value=status)
    content.visibility = MagicMock(value="wriveted")
    content.school_id = None
    content.version = 1
    content.created_at = MagicMock(isoformat=MagicMock(return_value="2025-01-01"))
    content.updated_at = MagicMock(isoformat=MagicMock(return_value="2025-01-01"))
    return content


class TestIncrementVersion:
    """Tests for semantic version incrementing (pure logic, no mocks needed)."""

    def setup_method(self):
        self.service = CMSWorkflowService(
            cms_repo=MagicMock(), event_outbox=MagicMock()
        )

    def test_patch_increment(self):
        assert self.service._increment_version("1.0.0", "patch") == "1.0.1"

    def test_minor_increment(self):
        assert self.service._increment_version("1.2.3", "minor") == "1.3.0"

    def test_major_increment(self):
        assert self.service._increment_version("1.2.3", "major") == "2.0.0"

    def test_major_resets_minor_and_patch(self):
        result = self.service._increment_version("3.5.7", "major")
        assert result == "4.0.0"

    def test_minor_resets_patch(self):
        result = self.service._increment_version("1.2.9", "minor")
        assert result == "1.3.0"

    def test_non_semantic_version_gets_suffix(self):
        assert self.service._increment_version("v2", "patch") == "v2.1"

    def test_two_part_version_gets_suffix(self):
        assert self.service._increment_version("1.0", "patch") == "1.0.1"

    def test_non_numeric_parts_get_suffix(self):
        assert self.service._increment_version("a.b.c", "patch") == "a.b.c.1"

    def test_empty_version(self):
        assert self.service._increment_version("", "patch") == ".1"

    def test_default_version_type_is_patch(self):
        # "else" branch handles anything that isn't "major" or "minor"
        assert self.service._increment_version("1.0.0", "unknown") == "1.0.1"


class TestValidateFlowStructure:
    """Tests for flow structure validation logic."""

    def setup_method(self):
        self.repo = AsyncMock()
        self.service = CMSWorkflowService(cms_repo=self.repo, event_outbox=AsyncMock())

    @pytest.mark.asyncio
    async def test_valid_flow(self):
        nodes = [_make_mock_node("start"), _make_mock_node("end")]
        connections = [_make_mock_connection("start", "end")]
        flow = _make_mock_flow(
            entry_node_id="start", nodes=nodes, connections=connections
        )

        result = await self.service._validate_flow_structure(AsyncMock(), flow)

        assert result["is_valid"] is True
        assert result["validation_errors"] == []
        assert result["nodes_count"] == 2
        assert result["connections_count"] == 1

    @pytest.mark.asyncio
    async def test_missing_entry_node(self):
        flow = _make_mock_flow(
            entry_node_id="nonexistent", nodes=[_make_mock_node("start")]
        )

        result = await self.service._validate_flow_structure(AsyncMock(), flow)

        assert result["is_valid"] is False
        assert any("nonexistent" in err for err in result["validation_errors"])

    @pytest.mark.asyncio
    async def test_orphaned_node_warning(self):
        nodes = [
            _make_mock_node("start"),
            _make_mock_node("connected"),
            _make_mock_node("orphan"),
        ]
        connections = [_make_mock_connection("start", "connected")]
        flow = _make_mock_flow(
            entry_node_id="start", nodes=nodes, connections=connections
        )

        result = await self.service._validate_flow_structure(AsyncMock(), flow)

        assert result["is_valid"] is True
        assert any("orphan" in w for w in result["validation_warnings"])

    @pytest.mark.asyncio
    async def test_entry_node_not_flagged_as_orphan(self):
        nodes = [_make_mock_node("start"), _make_mock_node("end")]
        connections = [_make_mock_connection("start", "end")]
        flow = _make_mock_flow(
            entry_node_id="start", nodes=nodes, connections=connections
        )

        result = await self.service._validate_flow_structure(AsyncMock(), flow)

        # Entry node has no inbound connections but should NOT be flagged
        assert not any("start" in w for w in result["validation_warnings"])

    @pytest.mark.asyncio
    async def test_invalid_variable_scope_warning(self):
        node = _make_mock_node("msg", content={"text": "Hello {{ badscope.name }}"})
        flow = _make_mock_flow(entry_node_id="msg", nodes=[node], connections=[])

        result = await self.service._validate_flow_structure(AsyncMock(), flow)

        assert any("badscope" in w for w in result["validation_warnings"])

    @pytest.mark.asyncio
    async def test_valid_variable_scopes_no_warning(self):
        node = _make_mock_node(
            "msg", content={"text": "Hi {{ user.name }}, you have {{ temp.count }}"}
        )
        flow = _make_mock_flow(entry_node_id="msg", nodes=[node], connections=[])

        result = await self.service._validate_flow_structure(AsyncMock(), flow)

        assert result["validation_warnings"] == []

    @pytest.mark.asyncio
    async def test_unscoped_variable_warning(self):
        node = _make_mock_node("msg", content={"text": "Hi {{ name }}"})
        flow = _make_mock_flow(entry_node_id="msg", nodes=[node], connections=[])

        result = await self.service._validate_flow_structure(AsyncMock(), flow)

        assert any("missing scope" in w.lower() for w in result["validation_warnings"])

    @pytest.mark.asyncio
    async def test_secret_reference_not_flagged(self):
        node = _make_mock_node("msg", content={"text": "Key is {{ secret:api_key }}"})
        flow = _make_mock_flow(entry_node_id="msg", nodes=[node], connections=[])

        result = await self.service._validate_flow_structure(AsyncMock(), flow)

        assert result["validation_warnings"] == []

    @pytest.mark.asyncio
    async def test_node_with_no_content_skipped(self):
        node = _make_mock_node("empty", content=None)
        flow = _make_mock_flow(entry_node_id="empty", nodes=[node], connections=[])

        result = await self.service._validate_flow_structure(AsyncMock(), flow)

        assert result["is_valid"] is True


class TestPublishFlowWithValidation:
    """Tests for the publish flow workflow."""

    def setup_method(self):
        self.repo = AsyncMock()
        self.outbox = AsyncMock()
        self.service = CMSWorkflowService(cms_repo=self.repo, event_outbox=self.outbox)
        self.db = AsyncMock()
        self.flow_id = uuid4()
        self.user_id = uuid4()

    @pytest.mark.asyncio
    async def test_publish_flow_not_found_raises(self):
        self.repo.get_flow_with_nodes.return_value = None

        with pytest.raises(FlowNotFoundError):
            await self.service.publish_flow_with_validation(
                self.db, self.flow_id, self.user_id
            )

    @pytest.mark.asyncio
    async def test_publish_valid_flow(self):
        flow = _make_mock_flow(
            flow_id=self.flow_id,
            entry_node_id="start",
            nodes=[_make_mock_node("start")],
        )
        self.repo.get_flow_with_nodes.return_value = flow
        self.repo.publish_flow.return_value = flow

        await self.service.publish_flow_with_validation(
            self.db, self.flow_id, self.user_id
        )

        self.repo.publish_flow.assert_awaited_once_with(
            self.db, self.flow_id, self.user_id, new_version=None
        )
        self.outbox.publish_event.assert_awaited_once()
        call_kwargs = self.outbox.publish_event.call_args
        assert (
            call_kwargs[1]["event_type"] == "flow.published"
            or call_kwargs[0][1] == "flow.published"
        )

    @pytest.mark.asyncio
    async def test_publish_invalid_flow_raises_validation_error(self):
        flow = _make_mock_flow(
            entry_node_id="nonexistent", nodes=[_make_mock_node("start")]
        )
        self.repo.get_flow_with_nodes.return_value = flow

        with pytest.raises(FlowValidationError):
            await self.service.publish_flow_with_validation(
                self.db, self.flow_id, self.user_id
            )

    @pytest.mark.asyncio
    async def test_publish_with_version_increment(self):
        from app.schemas.cms import FlowPublishRequest

        flow = _make_mock_flow(
            flow_id=self.flow_id,
            version="1.2.3",
            entry_node_id="start",
            nodes=[_make_mock_node("start")],
        )
        self.repo.get_flow_with_nodes.return_value = flow
        self.repo.publish_flow.return_value = flow

        request = FlowPublishRequest(
            publish=True, increment_version=True, version_type="minor"
        )
        await self.service.publish_flow_with_validation(
            self.db, self.flow_id, self.user_id, publish_request=request
        )

        self.repo.publish_flow.assert_awaited_once_with(
            self.db, self.flow_id, self.user_id, new_version="1.3.0"
        )

    @pytest.mark.asyncio
    async def test_unpublish_flow(self):
        from app.schemas.cms import FlowPublishRequest

        flow = _make_mock_flow(flow_id=self.flow_id, is_published=True)
        self.repo.get_flow_with_nodes.return_value = flow
        self.repo.unpublish_flow.return_value = flow

        request = FlowPublishRequest(publish=False)
        await self.service.publish_flow_with_validation(
            self.db, self.flow_id, self.user_id, publish_request=request
        )

        self.repo.unpublish_flow.assert_awaited_once_with(self.db, self.flow_id)
        call_args = self.outbox.publish_event.call_args
        assert "unpublished" in str(call_args)


class TestValidateFlowComprehensive:
    """Tests for the comprehensive validation entry point."""

    def setup_method(self):
        self.repo = AsyncMock()
        self.service = CMSWorkflowService(cms_repo=self.repo, event_outbox=AsyncMock())

    @pytest.mark.asyncio
    async def test_flow_not_found_raises(self):
        self.repo.get_flow_with_nodes.return_value = None

        with pytest.raises(FlowNotFoundError):
            await self.service.validate_flow_comprehensive(AsyncMock(), uuid4())

    @pytest.mark.asyncio
    async def test_delegates_to_validate_structure(self):
        flow = _make_mock_flow(entry_node_id="start", nodes=[_make_mock_node("start")])
        self.repo.get_flow_with_nodes.return_value = flow

        result = await self.service.validate_flow_comprehensive(AsyncMock(), uuid4())

        assert "is_valid" in result
        assert "nodes_count" in result


class TestGetRandomContent:
    """Tests for random content retrieval."""

    def setup_method(self):
        self.repo = AsyncMock()
        self.service = CMSWorkflowService(cms_repo=self.repo, event_outbox=AsyncMock())

    @pytest.mark.asyncio
    async def test_invalid_content_type_raises(self):
        with pytest.raises(ContentWorkflowError, match="Invalid content type"):
            await self.service.get_random_content(AsyncMock(), "nonexistent_type")

    @pytest.mark.asyncio
    async def test_valid_content_type_returns_dict(self):
        content = _make_mock_content(content_type="joke")
        self.repo.get_random_content_of_type.return_value = content

        result = await self.service.get_random_content(AsyncMock(), "joke")

        assert result is not None
        assert result["type"] == "joke"

    @pytest.mark.asyncio
    async def test_no_content_returns_none(self):
        self.repo.get_random_content_of_type.return_value = None

        result = await self.service.get_random_content(AsyncMock(), "joke")

        assert result is None


class TestCreateContentWithValidation:
    """Tests for content creation workflow."""

    def setup_method(self):
        self.repo = AsyncMock()
        self.outbox = AsyncMock()
        self.db = AsyncMock()
        self.service = CMSWorkflowService(cms_repo=self.repo, event_outbox=self.outbox)

    @pytest.mark.asyncio
    async def test_missing_type_raises(self):
        with pytest.raises(ContentWorkflowError, match="type is required"):
            await self.service.create_content_with_validation(
                self.db, {"content": {"text": "hi"}}
            )

    @pytest.mark.asyncio
    async def test_missing_content_body_raises(self):
        with pytest.raises(ContentWorkflowError, match="Content body is required"):
            await self.service.create_content_with_validation(self.db, {"type": "joke"})

    @pytest.mark.asyncio
    async def test_successful_creation(self):
        content = _make_mock_content()
        self.repo.create_content.return_value = content

        result = await self.service.create_content_with_validation(
            self.db, {"type": "joke", "content": {"text": "Why?"}}
        )

        assert result["id"] == str(content.id)
        self.outbox.publish_event.assert_awaited_once()
        self.db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_repo_error_rolls_back(self):
        self.repo.create_content.side_effect = RuntimeError("DB error")

        with pytest.raises(ContentWorkflowError, match="Failed to create"):
            await self.service.create_content_with_validation(
                self.db, {"type": "joke", "content": {"text": "hi"}}
            )

        self.db.rollback.assert_awaited_once()


class TestUpdateContentWithValidation:
    """Tests for content update workflow."""

    def setup_method(self):
        self.repo = AsyncMock()
        self.outbox = AsyncMock()
        self.db = AsyncMock()
        self.service = CMSWorkflowService(cms_repo=self.repo, event_outbox=self.outbox)

    @pytest.mark.asyncio
    async def test_content_not_found_raises(self):
        self.repo.get_content_by_id.return_value = None

        with pytest.raises(ContentWorkflowError, match="not found"):
            await self.service.update_content_with_validation(
                self.db, uuid4(), {"tags": ["new"]}
            )

    @pytest.mark.asyncio
    async def test_filters_none_values(self):
        content = _make_mock_content()
        self.repo.get_content_by_id.return_value = content
        self.repo.update_content.return_value = content

        await self.service.update_content_with_validation(
            self.db, content.id, {"tags": ["new"], "description": None}
        )

        call_args = self.repo.update_content.call_args
        update_data = call_args[0][2]
        assert "description" not in update_data
        assert "tags" in update_data

    @pytest.mark.asyncio
    async def test_publishes_event_with_change_list(self):
        content = _make_mock_content()
        self.repo.get_content_by_id.return_value = content
        self.repo.update_content.return_value = content

        await self.service.update_content_with_validation(
            self.db, content.id, {"tags": ["new"], "status": "published"}
        )

        call_args = self.outbox.publish_event.call_args
        payload = (
            call_args[0][3]
            if len(call_args[0]) > 3
            else call_args[1].get("payload", {})
        )
        assert set(payload.get("changes", [])) == {"tags", "status"}


class TestUpdateContentStatus:
    """Tests for content status workflow transitions."""

    def setup_method(self):
        self.repo = AsyncMock()
        self.outbox = AsyncMock()
        self.db = AsyncMock()
        self.service = CMSWorkflowService(cms_repo=self.repo, event_outbox=self.outbox)

    @pytest.mark.asyncio
    async def test_content_not_found_raises(self):
        self.repo.get_content_by_id.return_value = None

        with pytest.raises(ContentWorkflowError, match="not found"):
            await self.service.update_content_status_with_validation(
                self.db, uuid4(), "published"
            )

    @pytest.mark.asyncio
    async def test_invalid_status_raises(self):
        self.repo.get_content_by_id.return_value = _make_mock_content()

        with pytest.raises(ContentWorkflowError, match="Invalid status"):
            await self.service.update_content_status_with_validation(
                self.db, uuid4(), "nonexistent_status"
            )

    @pytest.mark.asyncio
    async def test_published_status_increments_version(self):
        content = _make_mock_content()
        content.version = 3
        self.repo.get_content_by_id.return_value = content
        self.repo.update_content.return_value = content

        await self.service.update_content_status_with_validation(
            self.db, content.id, "published"
        )

        call_args = self.repo.update_content.call_args
        update_data = call_args[0][2]
        assert update_data["version"] == 4

    @pytest.mark.asyncio
    async def test_draft_status_does_not_increment_version(self):
        content = _make_mock_content()
        content.version = 3
        self.repo.get_content_by_id.return_value = content
        self.repo.update_content.return_value = content

        await self.service.update_content_status_with_validation(
            self.db, content.id, "draft"
        )

        call_args = self.repo.update_content.call_args
        update_data = call_args[0][2]
        assert "version" not in update_data


class TestBulkOperations:
    """Tests for bulk update and delete workflows."""

    def setup_method(self):
        self.repo = AsyncMock()
        self.db = AsyncMock()
        self.service = CMSWorkflowService(cms_repo=self.repo, event_outbox=AsyncMock())

    @pytest.mark.asyncio
    async def test_bulk_update_all_succeed(self):
        ids = [uuid4(), uuid4()]
        self.repo.get_content_by_id.return_value = _make_mock_content()

        count, errors = await self.service.bulk_update_content_with_validation(
            self.db, ids, {"tags": ["updated"]}
        )

        assert count == 2
        assert errors == []
        self.db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_bulk_update_partial_failure(self):
        id1, id2 = uuid4(), uuid4()
        self.repo.get_content_by_id.side_effect = [
            _make_mock_content(),
            None,
        ]

        count, errors = await self.service.bulk_update_content_with_validation(
            self.db, [id1, id2], {"tags": ["updated"]}
        )

        assert count == 1
        assert len(errors) == 1
        assert "not found" in errors[0]["error"]

    @pytest.mark.asyncio
    async def test_bulk_update_filters_none_values(self):
        content = _make_mock_content()
        self.repo.get_content_by_id.return_value = content

        await self.service.bulk_update_content_with_validation(
            self.db, [uuid4()], {"tags": ["new"], "description": None}
        )

        call_args = self.repo.update_content.call_args
        update_data = call_args[0][2]
        assert "description" not in update_data

    @pytest.mark.asyncio
    async def test_bulk_delete_all_succeed(self):
        ids = [uuid4(), uuid4()]
        self.repo.delete_content.return_value = True

        count, errors = await self.service.bulk_delete_content_with_validation(
            self.db, ids
        )

        assert count == 2
        assert errors == []
        self.db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_bulk_delete_partial_failure(self):
        self.repo.delete_content.side_effect = [True, False]

        count, errors = await self.service.bulk_delete_content_with_validation(
            self.db, [uuid4(), uuid4()]
        )

        assert count == 1
        assert len(errors) == 1

    @pytest.mark.asyncio
    async def test_bulk_delete_exception_captured(self):
        self.repo.delete_content.side_effect = RuntimeError("DB error")

        count, errors = await self.service.bulk_delete_content_with_validation(
            self.db, [uuid4()]
        )

        assert count == 0
        assert len(errors) == 1
        assert "DB error" in errors[0]["error"]


class TestDeleteContentWithValidation:
    """Tests for single content deletion workflow."""

    def setup_method(self):
        self.repo = AsyncMock()
        self.outbox = AsyncMock()
        self.db = AsyncMock()
        self.service = CMSWorkflowService(cms_repo=self.repo, event_outbox=self.outbox)

    @pytest.mark.asyncio
    async def test_content_not_found_raises(self):
        self.repo.get_content_by_id.return_value = None

        with pytest.raises(ContentWorkflowError, match="not found"):
            await self.service.delete_content_with_validation(self.db, uuid4())

    @pytest.mark.asyncio
    async def test_successful_delete_publishes_event(self):
        content = _make_mock_content()
        self.repo.get_content_by_id.return_value = content
        self.repo.delete_content.return_value = True

        result = await self.service.delete_content_with_validation(self.db, content.id)

        assert result is True
        self.outbox.publish_event.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_failed_delete_no_event(self):
        content = _make_mock_content()
        self.repo.get_content_by_id.return_value = content
        self.repo.delete_content.return_value = False

        result = await self.service.delete_content_with_validation(self.db, content.id)

        assert result is False
        self.outbox.publish_event.assert_not_awaited()


class TestListContentWithBusinessLogic:
    """Tests for content listing with business logic."""

    def setup_method(self):
        self.repo = AsyncMock()
        self.db = AsyncMock()
        self.service = CMSWorkflowService(cms_repo=self.repo, event_outbox=AsyncMock())

    @pytest.mark.asyncio
    async def test_invalid_content_type_raises(self):
        with pytest.raises(ContentWorkflowError, match="Invalid content type"):
            await self.service.list_content_with_business_logic(
                self.db, content_type="nonexistent"
            )

    @pytest.mark.asyncio
    async def test_returns_content_list_and_count(self):
        content = _make_mock_content()
        self.repo.list_content_with_filters.return_value = [content]
        self.repo.count_content_with_filters.return_value = 1

        items, total = await self.service.list_content_with_business_logic(
            self.db, content_type="joke"
        )

        assert len(items) == 1
        assert total == 1

    @pytest.mark.asyncio
    async def test_passes_filters_to_repo(self):
        self.repo.list_content_with_filters.return_value = []
        self.repo.count_content_with_filters.return_value = 0

        await self.service.list_content_with_business_logic(
            self.db,
            content_type="joke",
            tags=["science"],
            search="atoms",
            active=True,
            status="published",
            skip=10,
            limit=5,
        )

        call_args = self.repo.list_content_with_filters.call_args[0]
        assert call_args[2] == ["science"]  # tags
        assert call_args[3] == "atoms"  # search
        assert call_args[4] is True  # active
        assert call_args[5] == "published"  # status
        assert call_args[6] == 10  # skip
        assert call_args[7] == 5  # limit


class TestConvertContentToDict:
    """Tests for content-to-dict conversion edge cases."""

    def setup_method(self):
        self.service = CMSWorkflowService(
            cms_repo=MagicMock(), event_outbox=MagicMock()
        )

    def test_basic_conversion(self):
        content = _make_mock_content(content_type="fact")
        result = self.service._convert_content_to_dict(content)

        assert result["type"] == "fact"
        assert result["is_active"] is True
        assert result["tags"] == ["test"]

    def test_none_type_handled(self):
        content = _make_mock_content()
        content.type = None
        result = self.service._convert_content_to_dict(content)

        assert result["type"] == "unknown"

    def test_none_status_handled(self):
        content = _make_mock_content()
        content.status = None
        result = self.service._convert_content_to_dict(content)

        assert result["status"] == "draft"

    def test_school_id_serialized(self):
        content = _make_mock_content()
        school_id = uuid4()
        content.school_id = school_id
        result = self.service._convert_content_to_dict(content)

        assert result["school_id"] == str(school_id)

    def test_info_dict_conversion(self):
        content = _make_mock_content()
        content.info = {"key": "value", 123: "numeric_key"}
        result = self.service._convert_content_to_dict(content)

        assert result["info"]["key"] == "value"
        assert result["info"]["123"] == "numeric_key"

    def test_info_non_dict_handled(self):
        content = _make_mock_content()
        content.info = "not a dict"
        result = self.service._convert_content_to_dict(content)

        assert result["info"] == {}


class TestGetContentWithValidation:
    """Tests for single content retrieval."""

    def setup_method(self):
        self.repo = AsyncMock()
        self.service = CMSWorkflowService(cms_repo=self.repo, event_outbox=AsyncMock())

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self):
        self.repo.get_content_by_id.return_value = None

        result = await self.service.get_content_with_validation(AsyncMock(), uuid4())

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_dict_when_found(self):
        content = _make_mock_content()
        self.repo.get_content_by_id.return_value = content

        result = await self.service.get_content_with_validation(AsyncMock(), content.id)

        assert result is not None
        assert result["id"] == str(content.id)

    @pytest.mark.asyncio
    async def test_repo_error_wraps_in_workflow_error(self):
        self.repo.get_content_by_id.side_effect = RuntimeError("DB down")

        with pytest.raises(ContentWorkflowError, match="Failed to get content"):
            await self.service.get_content_with_validation(AsyncMock(), uuid4())


class TestContentVariantOperations:
    """Tests for content variant CRUD workflows.

    Variant methods import crud at call time via ``from app import crud``.
    Because ``app.crud`` is a real sub-package, the import resolves through
    ``sys.modules`` -- so we patch ``app.crud.content_variant`` directly.
    """

    def setup_method(self):
        self.repo = AsyncMock()
        self.outbox = AsyncMock()
        self.db = AsyncMock()
        self.service = CMSWorkflowService(cms_repo=self.repo, event_outbox=self.outbox)
        self.content_id = uuid4()
        self.variant_id = uuid4()

    @pytest.mark.asyncio
    async def test_create_variant_content_not_found_raises(self):
        self.repo.get_content_by_id.return_value = None

        with pytest.raises(ContentWorkflowError, match="Content not found"):
            await self.service.create_content_variant(
                self.db, self.content_id, {"variant_key": "a", "content": {}}
            )

    @pytest.mark.asyncio
    async def test_update_variant_not_found_raises(self):
        with patch("app.crud.content_variant") as mock_cv:
            mock_cv.aget = AsyncMock(return_value=None)

            with pytest.raises(ContentWorkflowError, match="Variant not found"):
                await self.service.update_content_variant(
                    self.db, self.content_id, self.variant_id, {"weight": 50}
                )

    @pytest.mark.asyncio
    async def test_update_variant_wrong_content_id_raises(self):
        variant = MagicMock()
        variant.content_id = uuid4()  # Different from self.content_id

        with patch("app.crud.content_variant") as mock_cv:
            mock_cv.aget = AsyncMock(return_value=variant)

            with pytest.raises(ContentWorkflowError, match="Variant not found"):
                await self.service.update_content_variant(
                    self.db, self.content_id, self.variant_id, {"weight": 50}
                )

    @pytest.mark.asyncio
    async def test_delete_variant_not_found_returns_false(self):
        with patch("app.crud.content_variant") as mock_cv:
            mock_cv.aget = AsyncMock(return_value=None)

            result = await self.service.delete_content_variant(
                self.db, self.content_id, self.variant_id
            )

            assert result is False
            self.outbox.publish_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_patch_variant_merges_performance_data(self):
        variant = MagicMock()
        variant.content_id = self.content_id
        variant.performance_data = {"clicks": 10}

        with patch("app.crud.content_variant") as mock_cv:
            mock_cv.aget = AsyncMock(return_value=variant)
            mock_cv.aupdate = AsyncMock(return_value=variant)

            await self.service.patch_content_variant(
                self.db,
                self.content_id,
                self.variant_id,
                {"performance_data": {"views": 20}},
            )

            self.outbox.publish_event.assert_awaited_once()
