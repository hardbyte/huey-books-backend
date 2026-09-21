"""
Unit tests for ChatRuntime sub-flow transition logic.

Tests the _to_uuid helper, _try_return_to_parent_flow method, and
session_flow_id preservation across composite node boundaries.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.cms import FlowNode, NodeType, SessionStatus
from app.services.chat_runtime import ChatRuntime, _to_uuid, sanitize_user_input

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class MockSession:
    """Minimal session mock for sub-flow tests."""

    def __init__(self, flow_id=None, info=None, state=None, revision=1):
        self.id = uuid.uuid4()
        self.user_id = uuid.uuid4()
        self.flow_id = flow_id or uuid.uuid4()
        self.session_token = "tok"
        self.current_node_id = "start"
        self.state = state or {}
        self.info = info if info is not None else {}
        self.revision = revision
        self.status = SessionStatus.ACTIVE


def _make_flow_node(node_id, node_type, flow_id=None, content=None):
    node = MagicMock(spec=FlowNode)
    node.node_id = node_id
    node.node_type = node_type
    node.flow_id = flow_id or uuid.uuid4()
    node.content = content or {}
    return node


# Shared patch paths — _try_return_to_parent_flow calls flag_modified on the
# session to mark info as dirty; we stub it out since MockSession isn't a real
# SA model.
_FLAG_MODIFIED = "sqlalchemy.orm.attributes.flag_modified"


# ---------------------------------------------------------------------------
# _to_uuid
# ---------------------------------------------------------------------------


class TestToUuid:
    def test_none_returns_none(self):
        assert _to_uuid(None) is None

    def test_uuid_passthrough(self):
        uid = uuid.uuid4()
        assert _to_uuid(uid) is uid

    def test_string_converted(self):
        uid = uuid.uuid4()
        assert _to_uuid(str(uid)) == uid

    def test_invalid_string_raises(self):
        with pytest.raises(ValueError):
            _to_uuid("not-a-uuid")


# ---------------------------------------------------------------------------
# sanitize_user_input
# ---------------------------------------------------------------------------


class TestSanitizeUserInput:
    def test_escapes_html(self):
        assert sanitize_user_input("<script>alert(1)</script>") == (
            "&lt;script&gt;alert(1)&lt;/script&gt;"
        )

    def test_preserves_plain_text(self):
        assert sanitize_user_input("hello") == "hello"

    def test_empty_string(self):
        assert sanitize_user_input("") == ""

    def test_none_passthrough(self):
        assert sanitize_user_input(None) is None


# ---------------------------------------------------------------------------
# _try_return_to_parent_flow
# ---------------------------------------------------------------------------


@pytest.fixture
def runtime():
    """Create a ChatRuntime with mocked internals."""
    rt = ChatRuntime.__new__(ChatRuntime)
    rt.logger = MagicMock()
    rt.node_processors = {}
    rt._additional_processors_registered = False
    return rt


class TestTryReturnToParentFlow:
    """Test the _try_return_to_parent_flow logic paths."""

    @pytest.mark.asyncio
    @patch(_FLAG_MODIFIED)
    @patch("app.services.chat_runtime.chat_repo")
    async def test_returns_none_when_no_flow_stack(
        self, mock_chat_repo, _mock_fm, runtime
    ):
        session = MockSession(info={"flow_stack": []})
        mock_chat_repo.get_session_by_id = AsyncMock(return_value=session)

        result = await runtime._try_return_to_parent_flow(
            AsyncMock(), session, {"messages": []}
        )
        assert result is None

    @pytest.mark.asyncio
    @patch(_FLAG_MODIFIED)
    @patch("app.services.chat_runtime.chat_repo")
    async def test_returns_none_when_return_node_not_found(
        self, mock_chat_repo, _mock_fm, runtime
    ):
        parent_flow_id = str(uuid.uuid4())
        session = MockSession(
            info={
                "flow_stack": [
                    {"parent_flow_id": parent_flow_id, "return_node_id": "missing"}
                ]
            }
        )
        mock_chat_repo.get_session_by_id = AsyncMock(return_value=session)
        mock_chat_repo.update_session_state = AsyncMock(return_value=session)
        mock_chat_repo.get_flow_node = AsyncMock(return_value=None)

        result = await runtime._try_return_to_parent_flow(
            AsyncMock(), session, {"messages": []}
        )
        assert result is None

    @pytest.mark.asyncio
    @patch(_FLAG_MODIFIED)
    @patch("app.services.chat_runtime.chat_repo")
    async def test_processes_return_node_message(
        self, mock_chat_repo, _mock_fm, runtime
    ):
        """After popping flow_stack, the return node is processed and its
        messages are included in the result."""
        parent_flow_id = uuid.uuid4()
        return_node = _make_flow_node(
            "end_msg", NodeType.MESSAGE, flow_id=parent_flow_id
        )

        session = MockSession(
            info={
                "flow_stack": [
                    {
                        "parent_flow_id": str(parent_flow_id),
                        "return_node_id": "end_msg",
                    }
                ]
            }
        )
        # After popping, the refreshed session has an empty stack
        popped = MockSession(info={"flow_stack": []})
        popped.id = session.id
        popped.revision = 2

        # First refresh returns session (with stack), subsequent return popped
        mock_chat_repo.get_session_by_id = AsyncMock(
            side_effect=[session, popped, popped]
        )
        mock_chat_repo.update_session_state = AsyncMock(return_value=session)
        mock_chat_repo.get_flow_node = AsyncMock(return_value=return_node)

        runtime.process_node = AsyncMock(
            return_value={"type": "text", "text": "Happy reading!"}
        )

        result = await runtime._try_return_to_parent_flow(
            AsyncMock(), session, {"messages": ["prior_msg"]}
        )

        assert result is not None
        assert result["returned_from_subflow"] is True
        assert "prior_msg" in result["messages"]
        assert {"type": "text", "text": "Happy reading!"} in result["messages"]
        assert result["session_ended"] is True

    @pytest.mark.asyncio
    @patch(_FLAG_MODIFIED)
    @patch("app.services.chat_runtime.chat_repo")
    async def test_question_node_builds_input_request(
        self, mock_chat_repo, _mock_fm, runtime
    ):
        """When the return node chains to a QUESTION FlowNode, an input_request
        is built and awaiting_input is True."""
        parent_flow_id = uuid.uuid4()
        return_node = _make_flow_node(
            "end_msg", NodeType.MESSAGE, flow_id=parent_flow_id
        )
        question_node = _make_flow_node(
            "restart_choice", NodeType.QUESTION, flow_id=parent_flow_id
        )

        session = MockSession(
            info={
                "flow_stack": [
                    {
                        "parent_flow_id": str(parent_flow_id),
                        "return_node_id": "end_msg",
                    }
                ]
            }
        )
        popped = MockSession(info={"flow_stack": []})
        popped.id = session.id
        popped.revision = 2

        mock_chat_repo.get_session_by_id = AsyncMock(
            side_effect=[session, popped, popped, popped]
        )
        mock_chat_repo.update_session_state = AsyncMock(return_value=popped)
        mock_chat_repo.get_flow_node = AsyncMock(return_value=return_node)

        runtime.process_node = AsyncMock(
            return_value={
                "type": "text",
                "text": "Happy reading!",
                "next_node": question_node,
            }
        )

        question_options = [
            {"label": "Find more books!", "value": "restart"},
            {"label": "I'm done, thanks!", "value": "done"},
        ]
        runtime.process_node.side_effect = [
            runtime.process_node.return_value,
            {
                "type": "question",
                "input_type": "choice",
                "options": question_options,
                "question": {"text": "What would you like to do?"},
                "variable": "temp.restart_choice",
            },
        ]
        from itertools import chain, repeat

        mock_chat_repo.get_session_by_id = AsyncMock(
            side_effect=chain([session], repeat(popped))
        )

        result = await runtime._try_return_to_parent_flow(
            AsyncMock(), session, {"messages": []}
        )

        assert result is not None
        assert result["awaiting_input"] is True
        assert result["input_request"]["input_type"] == "choice"
        assert len(result["input_request"]["options"]) == 2
        assert result["current_node_id"] == "restart_choice"

    @pytest.mark.asyncio
    @patch(_FLAG_MODIFIED)
    @patch("app.services.chat_runtime.chat_repo")
    async def test_dict_question_from_composite_sets_flow_id(
        self, mock_chat_repo, _mock_fm, runtime
    ):
        """When the return node is a composite that returns a dict question,
        the sub_flow_id is correctly extracted from source_result."""
        parent_flow_id = uuid.uuid4()
        sub_flow_id = uuid.uuid4()
        return_node = _make_flow_node(
            "profile_composite", NodeType.COMPOSITE, flow_id=parent_flow_id
        )

        session = MockSession(
            info={
                "flow_stack": [
                    {
                        "parent_flow_id": str(parent_flow_id),
                        "return_node_id": "profile_composite",
                    }
                ]
            }
        )
        popped = MockSession(info={"flow_stack": []})
        popped.id = session.id
        popped.revision = 2

        mock_chat_repo.get_session_by_id = AsyncMock(
            side_effect=[session, popped, popped]
        )
        mock_chat_repo.update_session_state = AsyncMock(return_value=popped)
        mock_chat_repo.get_flow_node = AsyncMock(return_value=return_node)

        runtime.process_node = AsyncMock(
            return_value={
                "type": "messages",
                "messages": [{"type": "text", "text": "Let me ask you..."}],
                "next_node": {
                    "type": "question",
                    "node_id": "ask_age",
                    "question": {"text": "How old are you?"},
                    "input_type": "choice",
                    "options": [
                        {"label": "5-7", "value": "5"},
                        {"label": "8-10", "value": "8"},
                    ],
                },
                "sub_flow_id": sub_flow_id,
            }
        )

        result = await runtime._try_return_to_parent_flow(
            AsyncMock(), session, {"messages": []}
        )

        assert result is not None
        assert result["awaiting_input"] is True
        assert result["current_node_id"] == "ask_age"
        assert result["input_request"]["input_type"] == "choice"

        # Verify update_session_state was called with the correct sub_flow_id
        update_calls = mock_chat_repo.update_session_state.call_args_list
        last_update = update_calls[-1]
        assert last_update.kwargs.get("current_flow_id") == sub_flow_id
        assert last_update.kwargs.get("current_node_id") == "ask_age"

    @pytest.mark.asyncio
    @patch(_FLAG_MODIFIED)
    @patch("app.services.chat_runtime.chat_repo")
    async def test_messages_type_result_uses_extend(
        self, mock_chat_repo, _mock_fm, runtime
    ):
        """When the return node result has type 'messages', the messages list
        is extended (not appended as a single item)."""
        parent_flow_id = uuid.uuid4()
        return_node = _make_flow_node(
            "composite_node", NodeType.COMPOSITE, flow_id=parent_flow_id
        )

        session = MockSession(
            info={
                "flow_stack": [
                    {
                        "parent_flow_id": str(parent_flow_id),
                        "return_node_id": "composite_node",
                    }
                ]
            }
        )
        popped = MockSession(info={"flow_stack": []})
        popped.id = session.id
        popped.revision = 2

        mock_chat_repo.get_session_by_id = AsyncMock(
            side_effect=[session, popped, popped]
        )
        mock_chat_repo.update_session_state = AsyncMock(return_value=popped)
        mock_chat_repo.get_flow_node = AsyncMock(return_value=return_node)

        msg1 = {"type": "text", "text": "First"}
        msg2 = {"type": "text", "text": "Second"}
        runtime.process_node = AsyncMock(
            return_value={"type": "messages", "messages": [msg1, msg2]}
        )

        result = await runtime._try_return_to_parent_flow(
            AsyncMock(), session, {"messages": []}
        )

        assert result is not None
        assert msg1 in result["messages"]
        assert msg2 in result["messages"]
        # Ensure it wasn't appended as a single dict containing nested messages
        assert {"type": "messages", "messages": [msg1, msg2]} not in result["messages"]

    @pytest.mark.asyncio
    @patch(_FLAG_MODIFIED)
    @patch("app.services.chat_runtime.chat_repo")
    async def test_source_result_tracks_through_loop(
        self, mock_chat_repo, _mock_fm, runtime
    ):
        """When processing a chain (MESSAGE → COMPOSITE), the sub_flow_id is
        taken from the most recent source_result, not the initial one."""
        parent_flow_id = uuid.uuid4()
        sub_flow_id = uuid.uuid4()

        return_node = _make_flow_node(
            "msg_node", NodeType.MESSAGE, flow_id=parent_flow_id
        )
        composite_node = _make_flow_node(
            "composite_node", NodeType.MESSAGE, flow_id=parent_flow_id
        )

        session = MockSession(
            info={
                "flow_stack": [
                    {
                        "parent_flow_id": str(parent_flow_id),
                        "return_node_id": "msg_node",
                    }
                ]
            }
        )
        popped = MockSession(info={"flow_stack": []})
        popped.id = session.id
        popped.revision = 2

        mock_chat_repo.get_session_by_id = AsyncMock(
            side_effect=[session, popped, popped, popped]
        )
        mock_chat_repo.update_session_state = AsyncMock(return_value=popped)
        mock_chat_repo.get_flow_node = AsyncMock(return_value=return_node)

        call_count = 0

        async def mock_process_node(db, node, sess):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {
                    "type": "text",
                    "text": "Transition",
                    "next_node": composite_node,
                }
            else:
                return {
                    "type": "messages",
                    "messages": [],
                    "next_node": {
                        "type": "question",
                        "node_id": "pref_q_one",
                        "question": {"text": "Which picture?"},
                        "input_type": "choice",
                        "options": [{"label": "A", "value": "a"}],
                    },
                    "sub_flow_id": sub_flow_id,
                }

        runtime.process_node = mock_process_node

        result = await runtime._try_return_to_parent_flow(
            AsyncMock(), session, {"messages": []}
        )

        assert result is not None
        assert result["awaiting_input"] is True
        assert result["current_node_id"] == "pref_q_one"

        # sub_flow_id should come from the second result (composite), not the first
        last_update = mock_chat_repo.update_session_state.call_args_list[-1]
        assert last_update.kwargs.get("current_flow_id") == sub_flow_id
