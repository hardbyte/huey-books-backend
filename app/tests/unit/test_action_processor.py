"""
Unit tests for action_processor helpers and API call handling.

Tests _strip_unresolved_templates (pure function), the internal handler
fallback_response mechanism, and emit_event action type without database
dependencies.
"""

import uuid
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.models.cms import FlowNode, NodeType, SessionStatus
from app.services.action_processor import (
    ActionNodeProcessor,
    _strip_unresolved_templates,
)

# ---------------------------------------------------------------------------
# Fixtures (shared with test_aggregate_action.py pattern)
# ---------------------------------------------------------------------------


class MockSession:
    def __init__(self, state=None):
        self.id = uuid.uuid4()
        self.user_id = uuid.uuid4()
        self.flow_id = uuid.uuid4()
        self.session_token = "test_token"
        self.current_node_id = "test_node"
        self.state = state or {}
        self.revision = 1
        self.status = SessionStatus.ACTIVE


@pytest.fixture
def mock_runtime():
    runtime = Mock()
    runtime.substitute_variables = Mock(side_effect=lambda v, s: v)
    runtime.substitute_object = Mock(side_effect=lambda v, s: v)
    runtime.process_node = AsyncMock()
    return runtime


@pytest.fixture
def action_processor(mock_runtime):
    return ActionNodeProcessor(mock_runtime)


@pytest.fixture
def mock_db():
    return AsyncMock()


def _make_action_node(flow_id, content):
    node = Mock(spec=FlowNode)
    node.id = uuid.uuid4()
    node.flow_id = flow_id
    node.node_id = f"action_{uuid.uuid4().hex[:8]}"
    node.node_type = NodeType.ACTION
    node.content = content
    node.template = None
    node.position = {"x": 0, "y": 0}
    return node


def _setup_chat_repo(mock_repo):
    mock_repo.update_session_state = AsyncMock()
    mock_repo.add_interaction_history = AsyncMock()
    mock_repo.get_flow_node = AsyncMock(return_value=None)
    mock_repo.get_node_connections = AsyncMock(return_value=[])


# ---------------------------------------------------------------------------
# _strip_unresolved_templates
# ---------------------------------------------------------------------------


class TestStripUnresolvedTemplates:
    """Test the _strip_unresolved_templates pure function."""

    def test_resolves_simple_template_to_none(self):
        assert _strip_unresolved_templates("{{user.name}}") is None

    def test_resolves_template_with_surrounding_text(self):
        assert _strip_unresolved_templates("Hello {{user.name}}!") is None

    def test_preserves_plain_string(self):
        assert _strip_unresolved_templates("hello world") == "hello world"

    def test_preserves_empty_string(self):
        assert _strip_unresolved_templates("") == ""

    def test_preserves_non_string_types(self):
        assert _strip_unresolved_templates(42) == 42
        assert _strip_unresolved_templates(3.14) == 3.14
        assert _strip_unresolved_templates(True) is True
        assert _strip_unresolved_templates(None) is None

    def test_strips_in_nested_dict(self):
        result = _strip_unresolved_templates(
            {"name": "Brian", "school_id": "{{context.school_wriveted_id}}"}
        )
        assert result == {"name": "Brian", "school_id": None}

    def test_strips_in_nested_list(self):
        result = _strip_unresolved_templates(["ok", "{{temp.x}}", 123])
        assert result == ["ok", None, 123]

    def test_strips_in_deeply_nested_structure(self):
        result = _strip_unresolved_templates(
            {"outer": {"inner": [{"val": "{{user.id}}"}]}}
        )
        assert result == {"outer": {"inner": [{"val": None}]}}

    def test_preserves_dict_with_no_templates(self):
        data = {"a": 1, "b": "hello", "c": [1, 2]}
        assert _strip_unresolved_templates(data) == data

    def test_stray_braces_not_matching_template_pattern(self):
        """Strings with {{ or }} alone (not forming a template) are preserved."""
        assert _strip_unresolved_templates("has }} only") == "has }} only"
        assert _strip_unresolved_templates("has {{ only") == "has {{ only"

    def test_empty_template_braces(self):
        """{{}} is technically a template pattern and should be stripped."""
        assert _strip_unresolved_templates("{{}}") is None

    def test_multiple_templates_in_one_string(self):
        assert _strip_unresolved_templates("{{a}} and {{b}}") is None


# ---------------------------------------------------------------------------
# Internal handler fallback_response
# ---------------------------------------------------------------------------


class TestInternalHandlerFallback:
    """Test the fallback_response mechanism in _handle_api_call."""

    @pytest.mark.asyncio
    @patch("app.services.chat_runtime.chat_repo")
    @patch("app.services.action_processor.chat_repo")
    @patch("app.services.internal_api_handlers.INTERNAL_HANDLERS", new_callable=dict)
    async def test_fallback_used_when_handler_raises(
        self,
        mock_handlers,
        mock_action_chat_repo,
        mock_runtime_chat_repo,
        action_processor,
        mock_db,
    ):
        """When an internal handler raises and fallback_response is defined, use it."""
        _setup_chat_repo(mock_action_chat_repo)
        _setup_chat_repo(mock_runtime_chat_repo)

        mock_handlers["/v1/recommend"] = AsyncMock(
            side_effect=ValueError("bad UUID")
        )

        session = MockSession()
        node = _make_action_node(
            session.flow_id,
            {
                "actions": [
                    {
                        "type": "api_call",
                        "config": {
                            "endpoint": "/v1/recommend",
                            "auth_type": "internal",
                            "body": {},
                            "fallback_response": {"books": [], "count": 0},
                            "response_mapping": {
                                "count": "temp.book_count",
                            },
                        },
                    }
                ]
            },
        )

        result = await action_processor.process(
            mock_db, node, session, {"db": mock_db}
        )

        assert result["success"] is True
        assert result["variables"]["temp"]["book_count"] == 0

    @pytest.mark.asyncio
    @patch("app.services.chat_runtime.chat_repo")
    @patch("app.services.action_processor.chat_repo")
    @patch("app.services.internal_api_handlers.INTERNAL_HANDLERS", new_callable=dict)
    async def test_exception_propagates_without_fallback(
        self,
        mock_handlers,
        mock_action_chat_repo,
        mock_runtime_chat_repo,
        action_processor,
        mock_db,
    ):
        """When an internal handler raises and no fallback_response, exception propagates."""
        _setup_chat_repo(mock_action_chat_repo)
        _setup_chat_repo(mock_runtime_chat_repo)

        mock_handlers["/v1/recommend"] = AsyncMock(
            side_effect=ValueError("bad UUID")
        )

        session = MockSession()
        node = _make_action_node(
            session.flow_id,
            {
                "actions": [
                    {
                        "type": "api_call",
                        "config": {
                            "endpoint": "/v1/recommend",
                            "auth_type": "internal",
                            "body": {},
                            "response_mapping": {},
                        },
                    }
                ]
            },
        )

        result = await action_processor.process(
            mock_db, node, session, {"db": mock_db}
        )
        # The outer _execute_actions_sync catches the exception and sets success=False
        assert result["success"] is False

    @pytest.mark.asyncio
    @patch("app.services.chat_runtime.chat_repo")
    @patch("app.services.action_processor.chat_repo")
    @patch("app.services.internal_api_handlers.INTERNAL_HANDLERS", new_callable=dict)
    async def test_template_stripping_applied_to_body_and_params(
        self,
        mock_handlers,
        mock_action_chat_repo,
        mock_runtime_chat_repo,
        action_processor,
        mock_db,
    ):
        """Unresolved templates in body and query_params are stripped to None."""
        _setup_chat_repo(mock_action_chat_repo)
        _setup_chat_repo(mock_runtime_chat_repo)

        captured_args = {}

        async def capture_handler(db, body, params):
            captured_args["body"] = body
            captured_args["params"] = params
            return {"result": "ok"}

        mock_handlers["/v1/test"] = capture_handler

        session = MockSession()
        node = _make_action_node(
            session.flow_id,
            {
                "actions": [
                    {
                        "type": "api_call",
                        "config": {
                            "endpoint": "/v1/test",
                            "auth_type": "internal",
                            "body": {
                                "name": "resolved",
                                "school_id": "{{context.school_wriveted_id}}",
                            },
                            "query_params": {
                                "limit": 10,
                                "filter": "{{context.missing}}",
                            },
                            "response_mapping": {},
                        },
                    }
                ]
            },
        )

        result = await action_processor.process(
            mock_db, node, session, {"db": mock_db}
        )

        assert result["success"] is True
        assert captured_args["body"] == {"name": "resolved", "school_id": None}
        assert captured_args["params"] == {"limit": 10, "filter": None}


# ---------------------------------------------------------------------------
# emit_event action type
# ---------------------------------------------------------------------------


class TestEmitEvent:
    """Test the emit_event action type."""

    @pytest.mark.asyncio
    @patch("app.services.chat_runtime.chat_repo")
    @patch("app.services.action_processor.chat_repo")
    @patch("app.services.action_processor.event_repository", create=True)
    async def test_emit_event_basic(
        self,
        mock_event_repo,
        mock_action_chat_repo,
        mock_runtime_chat_repo,
        action_processor,
        mock_db,
    ):
        """Basic emit_event creates an event via event_repository.acreate."""
        _setup_chat_repo(mock_action_chat_repo)
        _setup_chat_repo(mock_runtime_chat_repo)

        mock_acreate = AsyncMock()

        session = MockSession()
        node = _make_action_node(
            session.flow_id,
            {
                "actions": [
                    {
                        "type": "emit_event",
                        "title": "Huey: Chat started",
                        "description": "User started a chat",
                        "info": {"chatbot": "Huey"},
                    }
                ]
            },
        )

        with patch(
            "app.repositories.event_repository.event_repository"
        ) as patched_repo:
            patched_repo.acreate = mock_acreate
            result = await action_processor.process(
                mock_db, node, session, {"db": mock_db}
            )

        assert result["success"] is True
        mock_acreate.assert_called_once()
        call_kwargs = mock_acreate.call_args
        assert call_kwargs.kwargs["title"] == "Huey: Chat started"
        assert call_kwargs.kwargs["description"] == "User started a chat"
        assert call_kwargs.kwargs["commit"] is False

    @pytest.mark.asyncio
    @patch("app.services.chat_runtime.chat_repo")
    @patch("app.services.action_processor.chat_repo")
    async def test_emit_event_with_template_resolution(
        self,
        mock_action_chat_repo,
        mock_runtime_chat_repo,
        mock_db,
    ):
        """Verify template substitution is applied to title, description, and info."""
        _setup_chat_repo(mock_action_chat_repo)
        _setup_chat_repo(mock_runtime_chat_repo)

        def sub_vars(template, state):
            if "{{user.age}}" in template:
                return template.replace("{{user.age}}", "9")
            return template

        def sub_obj(obj, state):
            if isinstance(obj, dict):
                return {k: sub_vars(v, state) if isinstance(v, str) else v for k, v in obj.items()}
            if isinstance(obj, str):
                return sub_vars(obj, state)
            return obj

        runtime = Mock()
        runtime.substitute_variables = Mock(side_effect=sub_vars)
        runtime.substitute_object = Mock(side_effect=sub_obj)
        runtime.process_node = AsyncMock()
        processor = ActionNodeProcessor(runtime)

        session = MockSession(state={"user": {"age": 9}})
        node = _make_action_node(
            session.flow_id,
            {
                "actions": [
                    {
                        "type": "emit_event",
                        "title": "Age: {{user.age}}",
                        "info": {"age": "{{user.age}}"},
                    }
                ]
            },
        )

        mock_acreate = AsyncMock()
        with patch(
            "app.repositories.event_repository.event_repository"
        ) as patched_repo:
            patched_repo.acreate = mock_acreate
            result = await processor.process(
                mock_db, node, session, {"db": mock_db}
            )

        assert result["success"] is True
        call_kwargs = mock_acreate.call_args.kwargs
        assert call_kwargs["title"] == "Age: 9"
        assert call_kwargs["info"]["age"] == "9"

    @pytest.mark.asyncio
    @patch("app.services.chat_runtime.chat_repo")
    @patch("app.services.action_processor.chat_repo")
    async def test_emit_event_iterate_over(
        self,
        mock_action_chat_repo,
        mock_runtime_chat_repo,
        action_processor,
        mock_db,
    ):
        """iterate_over creates one event per list item."""
        _setup_chat_repo(mock_action_chat_repo)
        _setup_chat_repo(mock_runtime_chat_repo)

        books = [
            {"isbn": "111", "title": "Book A"},
            {"isbn": "222", "title": "Book B"},
        ]
        session = MockSession(state={"temp": {"book_results": books}})
        node = _make_action_node(
            session.flow_id,
            {
                "actions": [
                    {
                        "type": "emit_event",
                        "title": "Huey: Book reviewed",
                        "iterate_over": "temp.book_results",
                        "item_alias": "book",
                        "info": {"isbn": "{{temp.book.isbn}}"},
                    }
                ]
            },
        )

        mock_acreate = AsyncMock()
        with patch(
            "app.repositories.event_repository.event_repository"
        ) as patched_repo:
            patched_repo.acreate = mock_acreate
            result = await action_processor.process(
                mock_db, node, session, {"db": mock_db}
            )

        assert result["success"] is True
        assert mock_acreate.call_count == 2

    @pytest.mark.asyncio
    @patch("app.services.chat_runtime.chat_repo")
    @patch("app.services.action_processor.chat_repo")
    async def test_emit_event_fire_and_forget(
        self,
        mock_action_chat_repo,
        mock_runtime_chat_repo,
        action_processor,
        mock_db,
    ):
        """Errors in emit_event are logged but don't fail the action node."""
        _setup_chat_repo(mock_action_chat_repo)
        _setup_chat_repo(mock_runtime_chat_repo)

        session = MockSession()
        node = _make_action_node(
            session.flow_id,
            {
                "actions": [
                    {
                        "type": "emit_event",
                        "title": "Huey: Chat started",
                        "info": {},
                    }
                ]
            },
        )

        mock_acreate = AsyncMock(side_effect=RuntimeError("DB unavailable"))
        with patch(
            "app.repositories.event_repository.event_repository"
        ) as patched_repo:
            patched_repo.acreate = mock_acreate
            result = await action_processor.process(
                mock_db, node, session, {"db": mock_db}
            )

        # emit_event errors are caught — action should still succeed
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_emit_event_missing_db(
        self,
        action_processor,
    ):
        """emit_event gracefully skips when no db session is in context."""
        mock_acreate = AsyncMock()
        with patch(
            "app.repositories.event_repository.event_repository"
        ) as patched_repo:
            patched_repo.acreate = mock_acreate
            # Call _handle_emit_event directly with no db in context
            await action_processor._handle_emit_event(
                {"type": "emit_event", "title": "Test", "info": {}},
                {},  # state
                {},  # updates
                {},  # context — no "db" key
            )

        mock_acreate.assert_not_called()

    @pytest.mark.asyncio
    @patch("app.services.chat_runtime.chat_repo")
    @patch("app.services.action_processor.chat_repo")
    async def test_emit_event_school_resolution(
        self,
        mock_action_chat_repo,
        mock_runtime_chat_repo,
        action_processor,
        mock_db,
    ):
        """_resolve_school looks up a School by wriveted_identifier."""
        _setup_chat_repo(mock_action_chat_repo)
        _setup_chat_repo(mock_runtime_chat_repo)

        school_uuid = "84a5ade6-7f75-4155-831a-1d84c6256fc3"
        mock_school = Mock()
        mock_school.id = uuid.UUID(school_uuid)

        session = MockSession(
            state={"context": {"school_wriveted_id": school_uuid}}
        )
        node = _make_action_node(
            session.flow_id,
            {
                "actions": [
                    {
                        "type": "emit_event",
                        "title": "Huey: Test",
                        "info": {},
                    }
                ]
            },
        )

        mock_acreate = AsyncMock()
        mock_result = Mock()
        mock_result.scalars.return_value.first.return_value = mock_school
        mock_db.execute = AsyncMock(return_value=mock_result)

        with patch(
            "app.repositories.event_repository.event_repository"
        ) as patched_repo:
            patched_repo.acreate = mock_acreate
            result = await action_processor.process(
                mock_db, node, session, {"db": mock_db}
            )

        assert result["success"] is True
        call_kwargs = mock_acreate.call_args.kwargs
        assert call_kwargs["school"] is mock_school
