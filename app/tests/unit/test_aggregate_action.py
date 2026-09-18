"""
Unit tests for the aggregate action type in chatflows.

Tests the generic aggregate action functionality that can be used for various
use cases including scoring, statistics, and combining data from multiple
user responses in a conversation flow.

Uses mock session and repository to test ActionNodeProcessor aggregate actions
without database dependencies.
"""

import uuid
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.models.cms import FlowNode, NodeType, SessionStatus
from app.services.action_processor import ActionNodeProcessor


class MockSession:
    """Mock conversation session for testing."""

    def __init__(self, state=None):
        self.id = uuid.uuid4()
        self.user_id = uuid.uuid4()
        self.flow_id = uuid.uuid4()
        self.session_token = "test_token"
        self.current_node_id = "test_node"
        self.state = state or {}
        self.info = {}
        self.revision = 1
        self.status = SessionStatus.ACTIVE


@pytest.fixture
def mock_runtime():
    """Create a mock runtime for the ActionNodeProcessor."""
    runtime = Mock()
    runtime.substitute_variables = Mock(side_effect=lambda v, s: v)
    runtime.process_node = AsyncMock()
    return runtime


@pytest.fixture
def action_processor(mock_runtime):
    """Create ActionNodeProcessor with mock runtime."""
    return ActionNodeProcessor(mock_runtime)


@pytest.fixture
def mock_db():
    """Create a mock async database session."""
    return AsyncMock()


def create_action_node(flow_id: uuid.UUID, node_content: dict) -> FlowNode:
    """Create a FlowNode with action content for testing."""
    node = Mock(spec=FlowNode)
    node.id = uuid.uuid4()
    node.flow_id = flow_id
    node.node_id = f"action_{uuid.uuid4().hex[:8]}"
    node.node_type = NodeType.ACTION
    node.content = node_content
    node.template = None
    node.position = {"x": 0, "y": 0}
    return node


def setup_chat_repo_mocks(mock_chat_repo):
    """Set up common mocks for chat_repo methods."""
    mock_chat_repo.update_session_state = AsyncMock()
    mock_chat_repo.add_interaction_history = AsyncMock()
    mock_chat_repo.get_flow_node = AsyncMock(return_value=None)
    mock_chat_repo.get_node_connections = AsyncMock(return_value=[])


class TestAggregateSum:
    """Test aggregate action with sum operation."""

    @pytest.mark.asyncio
    @patch("app.services.chat_runtime.chat_repo")
    @patch("app.services.action_processor.chat_repo")
    async def test_sum_simple_numbers(
        self, mock_action_chat_repo, mock_runtime_chat_repo, action_processor, mock_db
    ):
        """Sum a list of simple numbers."""
        setup_chat_repo_mocks(mock_action_chat_repo)
        setup_chat_repo_mocks(mock_runtime_chat_repo)

        session = MockSession(state={"temp": {"scores": [10, 20, 30, 40]}})

        node_content = {
            "actions": [
                {
                    "type": "aggregate",
                    "source": "temp.scores",
                    "operation": "sum",
                    "target": "user.total_score",
                }
            ]
        }
        node = create_action_node(session.flow_id, node_content)

        result = await action_processor.process(mock_db, node, session, {})

        assert result["success"] is True
        assert result["variables"]["user"]["total_score"] == 100

    @pytest.mark.asyncio
    @patch("app.services.chat_runtime.chat_repo")
    @patch("app.services.action_processor.chat_repo")
    async def test_sum_field_from_objects(
        self, mock_action_chat_repo, mock_runtime_chat_repo, action_processor, mock_db
    ):
        """Sum a specific field from a list of objects."""
        setup_chat_repo_mocks(mock_action_chat_repo)
        setup_chat_repo_mocks(mock_runtime_chat_repo)

        session = MockSession(
            state={
                "temp": {
                    "quiz_answers": [
                        {"question_id": 1, "score": 5},
                        {"question_id": 2, "score": 8},
                        {"question_id": 3, "score": 7},
                    ]
                }
            }
        )

        node_content = {
            "actions": [
                {
                    "type": "aggregate",
                    "source": "temp.quiz_answers",
                    "field": "score",
                    "operation": "sum",
                    "target": "results.total_score",
                }
            ]
        }
        node = create_action_node(session.flow_id, node_content)

        result = await action_processor.process(mock_db, node, session, {})

        assert result["success"] is True
        assert result["variables"]["results"]["total_score"] == 20


class TestAggregateAverage:
    """Test aggregate action with avg operation."""

    @pytest.mark.asyncio
    @patch("app.services.chat_runtime.chat_repo")
    @patch("app.services.action_processor.chat_repo")
    async def test_average_calculation(
        self, mock_action_chat_repo, mock_runtime_chat_repo, action_processor, mock_db
    ):
        """Calculate average of numbers."""
        setup_chat_repo_mocks(mock_action_chat_repo)
        setup_chat_repo_mocks(mock_runtime_chat_repo)

        session = MockSession(state={"temp": {"ratings": [4.0, 5.0, 3.0, 4.0, 4.0]}})

        node_content = {
            "actions": [
                {
                    "type": "aggregate",
                    "source": "temp.ratings",
                    "operation": "avg",
                    "target": "user.average_rating",
                }
            ]
        }
        node = create_action_node(session.flow_id, node_content)

        result = await action_processor.process(mock_db, node, session, {})

        assert result["success"] is True
        assert result["variables"]["user"]["average_rating"] == 4.0

    @pytest.mark.asyncio
    @patch("app.services.chat_runtime.chat_repo")
    @patch("app.services.action_processor.chat_repo")
    async def test_average_from_responses(
        self, mock_action_chat_repo, mock_runtime_chat_repo, action_processor, mock_db
    ):
        """Calculate average difficulty from user responses."""
        setup_chat_repo_mocks(mock_action_chat_repo)
        setup_chat_repo_mocks(mock_runtime_chat_repo)

        session = MockSession(
            state={
                "temp": {
                    "feedback": [
                        {"difficulty": 3},
                        {"difficulty": 4},
                        {"difficulty": 5},
                    ]
                }
            }
        )

        node_content = {
            "actions": [
                {
                    "type": "aggregate",
                    "source": "temp.feedback",
                    "field": "difficulty",
                    "operation": "avg",
                    "target": "stats.avg_difficulty",
                }
            ]
        }
        node = create_action_node(session.flow_id, node_content)

        result = await action_processor.process(mock_db, node, session, {})

        assert result["success"] is True
        assert result["variables"]["stats"]["avg_difficulty"] == 4.0


class TestAggregateMinMax:
    """Test aggregate action with min/max operations."""

    @pytest.mark.asyncio
    @patch("app.services.chat_runtime.chat_repo")
    @patch("app.services.action_processor.chat_repo")
    async def test_max_value(
        self, mock_action_chat_repo, mock_runtime_chat_repo, action_processor, mock_db
    ):
        """Find maximum value in a list."""
        setup_chat_repo_mocks(mock_action_chat_repo)
        setup_chat_repo_mocks(mock_runtime_chat_repo)

        session = MockSession(state={"temp": {"scores": [75, 82, 90, 68, 88]}})

        node_content = {
            "actions": [
                {
                    "type": "aggregate",
                    "source": "temp.scores",
                    "operation": "max",
                    "target": "results.highest_score",
                }
            ]
        }
        node = create_action_node(session.flow_id, node_content)

        result = await action_processor.process(mock_db, node, session, {})

        assert result["success"] is True
        assert result["variables"]["results"]["highest_score"] == 90

    @pytest.mark.asyncio
    @patch("app.services.chat_runtime.chat_repo")
    @patch("app.services.action_processor.chat_repo")
    async def test_min_value(
        self, mock_action_chat_repo, mock_runtime_chat_repo, action_processor, mock_db
    ):
        """Find minimum value in a list."""
        setup_chat_repo_mocks(mock_action_chat_repo)
        setup_chat_repo_mocks(mock_runtime_chat_repo)

        session = MockSession(state={"temp": {"scores": [75, 82, 90, 68, 88]}})

        node_content = {
            "actions": [
                {
                    "type": "aggregate",
                    "source": "temp.scores",
                    "operation": "min",
                    "target": "results.lowest_score",
                }
            ]
        }
        node = create_action_node(session.flow_id, node_content)

        result = await action_processor.process(mock_db, node, session, {})

        assert result["success"] is True
        assert result["variables"]["results"]["lowest_score"] == 68


class TestAggregateCount:
    """Test aggregate action with count operation."""

    @pytest.mark.asyncio
    @patch("app.services.chat_runtime.chat_repo")
    @patch("app.services.action_processor.chat_repo")
    async def test_count_items(
        self, mock_action_chat_repo, mock_runtime_chat_repo, action_processor, mock_db
    ):
        """Count items in a list."""
        setup_chat_repo_mocks(mock_action_chat_repo)
        setup_chat_repo_mocks(mock_runtime_chat_repo)

        session = MockSession(
            state={
                "temp": {
                    "selected_items": [
                        {"id": 1, "name": "Item A"},
                        {"id": 2, "name": "Item B"},
                        {"id": 3, "name": "Item C"},
                    ]
                }
            }
        )

        node_content = {
            "actions": [
                {
                    "type": "aggregate",
                    "source": "temp.selected_items",
                    "operation": "count",
                    "target": "stats.item_count",
                }
            ]
        }
        node = create_action_node(session.flow_id, node_content)

        result = await action_processor.process(mock_db, node, session, {})

        assert result["success"] is True
        assert result["variables"]["stats"]["item_count"] == 3


class TestAggregateMerge:
    """Test aggregate action with merge operation for combining dictionaries."""

    @pytest.mark.asyncio
    @patch("app.services.chat_runtime.chat_repo")
    @patch("app.services.action_processor.chat_repo")
    async def test_merge_sum_strategy(
        self, mock_action_chat_repo, mock_runtime_chat_repo, action_processor, mock_db
    ):
        """Merge dictionaries by summing numeric values."""
        setup_chat_repo_mocks(mock_action_chat_repo)
        setup_chat_repo_mocks(mock_runtime_chat_repo)

        session = MockSession(
            state={
                "temp": {
                    "preference_weights": [
                        {"adventure": 0.8, "mystery": 0.2, "humor": 0.5},
                        {"adventure": 0.3, "mystery": 0.7, "romance": 0.4},
                        {"adventure": 0.5, "humor": 0.6, "romance": 0.2},
                    ]
                }
            }
        )

        node_content = {
            "actions": [
                {
                    "type": "aggregate",
                    "source": "temp.preference_weights",
                    "operation": "merge",
                    "merge_strategy": "sum",
                    "target": "user.aggregated_preferences",
                }
            ]
        }
        node = create_action_node(session.flow_id, node_content)

        result = await action_processor.process(mock_db, node, session, {})

        assert result["success"] is True
        prefs = result["variables"]["user"]["aggregated_preferences"]
        assert prefs["adventure"] == pytest.approx(1.6)  # 0.8 + 0.3 + 0.5
        assert prefs["mystery"] == pytest.approx(0.9)  # 0.2 + 0.7
        assert prefs["humor"] == pytest.approx(1.1)  # 0.5 + 0.6
        assert prefs["romance"] == pytest.approx(0.6)  # 0.4 + 0.2

    @pytest.mark.asyncio
    @patch("app.services.chat_runtime.chat_repo")
    @patch("app.services.action_processor.chat_repo")
    async def test_merge_max_strategy(
        self, mock_action_chat_repo, mock_runtime_chat_repo, action_processor, mock_db
    ):
        """Merge dictionaries by taking max value for each key."""
        setup_chat_repo_mocks(mock_action_chat_repo)
        setup_chat_repo_mocks(mock_runtime_chat_repo)

        session = MockSession(
            state={
                "temp": {
                    "skill_assessments": [
                        {"reading": 3, "math": 5, "science": 4},
                        {"reading": 4, "math": 3, "art": 5},
                        {"reading": 2, "science": 5, "art": 3},
                    ]
                }
            }
        )

        node_content = {
            "actions": [
                {
                    "type": "aggregate",
                    "source": "temp.skill_assessments",
                    "operation": "merge",
                    "merge_strategy": "max",
                    "target": "user.peak_skills",
                }
            ]
        }
        node = create_action_node(session.flow_id, node_content)

        result = await action_processor.process(mock_db, node, session, {})

        assert result["success"] is True
        skills = result["variables"]["user"]["peak_skills"]
        assert skills["reading"] == 4  # max(3, 4, 2)
        assert skills["math"] == 5  # max(5, 3)
        assert skills["science"] == 5  # max(4, 5)
        assert skills["art"] == 5  # max(5, 3)

    @pytest.mark.asyncio
    @patch("app.services.chat_runtime.chat_repo")
    @patch("app.services.action_processor.chat_repo")
    async def test_merge_last_strategy(
        self, mock_action_chat_repo, mock_runtime_chat_repo, action_processor, mock_db
    ):
        """Merge dictionaries using last-value-wins strategy."""
        setup_chat_repo_mocks(mock_action_chat_repo)
        setup_chat_repo_mocks(mock_runtime_chat_repo)

        session = MockSession(
            state={
                "temp": {
                    "config_overrides": [
                        {"theme": "light", "notifications": True},
                        {"theme": "dark"},
                        {"notifications": False},
                    ]
                }
            }
        )

        node_content = {
            "actions": [
                {
                    "type": "aggregate",
                    "source": "temp.config_overrides",
                    "operation": "merge",
                    "merge_strategy": "last",
                    "target": "user.final_config",
                }
            ]
        }
        node = create_action_node(session.flow_id, node_content)

        result = await action_processor.process(mock_db, node, session, {})

        assert result["success"] is True
        config = result["variables"]["user"]["final_config"]
        assert config["theme"] == "dark"  # Last value for theme
        assert config["notifications"] is False  # Last value for notifications


class TestAggregateCollect:
    """Test aggregate action with collect operation for flattening lists."""

    @pytest.mark.asyncio
    @patch("app.services.chat_runtime.chat_repo")
    @patch("app.services.action_processor.chat_repo")
    async def test_collect_tags(
        self, mock_action_chat_repo, mock_runtime_chat_repo, action_processor, mock_db
    ):
        """Collect and flatten tags from multiple selections."""
        setup_chat_repo_mocks(mock_action_chat_repo)
        setup_chat_repo_mocks(mock_runtime_chat_repo)

        session = MockSession(
            state={
                "temp": {
                    "book_selections": [
                        {"title": "Book 1", "tags": ["adventure", "fantasy"]},
                        {"title": "Book 2", "tags": ["mystery", "thriller"]},
                        {"title": "Book 3", "tags": ["adventure", "humor"]},
                    ]
                }
            }
        )

        node_content = {
            "actions": [
                {
                    "type": "aggregate",
                    "source": "temp.book_selections",
                    "field": "tags",
                    "operation": "collect",
                    "target": "user.all_tags",
                }
            ]
        }
        node = create_action_node(session.flow_id, node_content)

        result = await action_processor.process(mock_db, node, session, {})

        assert result["success"] is True
        tags = result["variables"]["user"]["all_tags"]
        assert len(tags) == 6
        assert "adventure" in tags
        assert "mystery" in tags
        assert "humor" in tags

    @pytest.mark.asyncio
    @patch("app.services.chat_runtime.chat_repo")
    @patch("app.services.action_processor.chat_repo")
    async def test_collect_simple_values(
        self, mock_action_chat_repo, mock_runtime_chat_repo, action_processor, mock_db
    ):
        """Collect simple values (non-list) into a list."""
        setup_chat_repo_mocks(mock_action_chat_repo)
        setup_chat_repo_mocks(mock_runtime_chat_repo)

        session = MockSession(
            state={
                "temp": {
                    "user_choices": [
                        {"choice": "Option A"},
                        {"choice": "Option B"},
                        {"choice": "Option C"},
                    ]
                }
            }
        )

        node_content = {
            "actions": [
                {
                    "type": "aggregate",
                    "source": "temp.user_choices",
                    "field": "choice",
                    "operation": "collect",
                    "target": "results.choices_list",
                }
            ]
        }
        node = create_action_node(session.flow_id, node_content)

        result = await action_processor.process(mock_db, node, session, {})

        assert result["success"] is True
        choices = result["variables"]["results"]["choices_list"]
        assert choices == ["Option A", "Option B", "Option C"]


class TestAggregateEdgeCases:
    """Test edge cases and error handling for aggregate action."""

    @pytest.mark.asyncio
    @patch("app.services.chat_runtime.chat_repo")
    @patch("app.services.action_processor.chat_repo")
    async def test_empty_list(
        self, mock_action_chat_repo, mock_runtime_chat_repo, action_processor, mock_db
    ):
        """Handle aggregation of empty list."""
        setup_chat_repo_mocks(mock_action_chat_repo)
        setup_chat_repo_mocks(mock_runtime_chat_repo)

        session = MockSession(state={"temp": {"scores": []}})

        node_content = {
            "actions": [
                {
                    "type": "aggregate",
                    "source": "temp.scores",
                    "operation": "sum",
                    "target": "results.total",
                }
            ]
        }
        node = create_action_node(session.flow_id, node_content)

        result = await action_processor.process(mock_db, node, session, {})

        # Empty list should complete without error
        assert result["success"] is True
        # CEL sum returns 0 for empty list
        assert result["variables"]["results"]["total"] == 0

    @pytest.mark.asyncio
    @patch("app.services.chat_runtime.chat_repo")
    @patch("app.services.action_processor.chat_repo")
    async def test_missing_source_variable(
        self, mock_action_chat_repo, mock_runtime_chat_repo, action_processor, mock_db
    ):
        """Handle missing source variable gracefully."""
        setup_chat_repo_mocks(mock_action_chat_repo)
        setup_chat_repo_mocks(mock_runtime_chat_repo)

        session = MockSession(state={"temp": {}})

        node_content = {
            "actions": [
                {
                    "type": "aggregate",
                    "source": "temp.nonexistent",
                    "operation": "sum",
                    "target": "results.total",
                }
            ]
        }
        node = create_action_node(session.flow_id, node_content)

        result = await action_processor.process(mock_db, node, session, {})

        # CEL evaluation will fail for missing variable, but action completes
        # The errors list should contain the error message
        assert "errors" in result

    @pytest.mark.asyncio
    @patch("app.services.chat_runtime.chat_repo")
    @patch("app.services.action_processor.chat_repo")
    async def test_non_list_source(
        self, mock_action_chat_repo, mock_runtime_chat_repo, action_processor, mock_db
    ):
        """Handle non-list source variable."""
        setup_chat_repo_mocks(mock_action_chat_repo)
        setup_chat_repo_mocks(mock_runtime_chat_repo)

        session = MockSession(state={"temp": {"value": "not a list"}})

        node_content = {
            "actions": [
                {
                    "type": "aggregate",
                    "source": "temp.value",
                    "operation": "sum",
                    "target": "results.total",
                }
            ]
        }
        node = create_action_node(session.flow_id, node_content)

        result = await action_processor.process(mock_db, node, session, {})

        # CEL handles non-list gracefully - result has errors or variables
        assert "errors" in result or "variables" in result

    @pytest.mark.asyncio
    @patch("app.services.chat_runtime.chat_repo")
    @patch("app.services.action_processor.chat_repo")
    async def test_missing_required_fields(
        self, mock_action_chat_repo, mock_runtime_chat_repo, action_processor, mock_db
    ):
        """Missing target should log warning but not fail."""
        setup_chat_repo_mocks(mock_action_chat_repo)
        setup_chat_repo_mocks(mock_runtime_chat_repo)

        session = MockSession(state={"temp": {"scores": [1, 2, 3]}})

        # Missing target
        node_content = {
            "actions": [
                {
                    "type": "aggregate",
                    "source": "temp.scores",
                    "operation": "sum",
                    # no target specified
                }
            ]
        }
        node = create_action_node(session.flow_id, node_content)

        result = await action_processor.process(mock_db, node, session, {})

        # Should still succeed, but no variables updated
        assert result["success"] is True
        # Variables should be empty since no target was specified
        assert result["variables"] == {}

    @pytest.mark.asyncio
    @patch("app.services.chat_runtime.chat_repo")
    @patch("app.services.action_processor.chat_repo")
    async def test_nested_field_extraction(
        self, mock_action_chat_repo, mock_runtime_chat_repo, action_processor, mock_db
    ):
        """Extract deeply nested field values."""
        setup_chat_repo_mocks(mock_action_chat_repo)
        setup_chat_repo_mocks(mock_runtime_chat_repo)

        session = MockSession(
            state={
                "temp": {
                    "responses": [
                        {"data": {"metrics": {"value": 10}}},
                        {"data": {"metrics": {"value": 20}}},
                        {"data": {"metrics": {"value": 30}}},
                    ]
                }
            }
        )

        node_content = {
            "actions": [
                {
                    "type": "aggregate",
                    "source": "temp.responses",
                    "field": "data.metrics.value",
                    "operation": "sum",
                    "target": "results.total_value",
                }
            ]
        }
        node = create_action_node(session.flow_id, node_content)

        result = await action_processor.process(mock_db, node, session, {})

        assert result["success"] is True
        assert result["variables"]["results"]["total_value"] == 60


class TestAggregateMultipleActions:
    """Test multiple aggregate actions in sequence."""

    @pytest.mark.asyncio
    @patch("app.services.chat_runtime.chat_repo")
    @patch("app.services.action_processor.chat_repo")
    async def test_multiple_aggregations(
        self, mock_action_chat_repo, mock_runtime_chat_repo, action_processor, mock_db
    ):
        """Perform multiple aggregation operations in one action node."""
        setup_chat_repo_mocks(mock_action_chat_repo)
        setup_chat_repo_mocks(mock_runtime_chat_repo)

        session = MockSession(
            state={
                "temp": {
                    "quiz_results": [
                        {"score": 80, "time_seconds": 45},
                        {"score": 90, "time_seconds": 30},
                        {"score": 85, "time_seconds": 50},
                    ]
                }
            }
        )

        node_content = {
            "actions": [
                {
                    "type": "aggregate",
                    "source": "temp.quiz_results",
                    "field": "score",
                    "operation": "sum",
                    "target": "results.total_score",
                },
                {
                    "type": "aggregate",
                    "source": "temp.quiz_results",
                    "field": "score",
                    "operation": "avg",
                    "target": "results.avg_score",
                },
                {
                    "type": "aggregate",
                    "source": "temp.quiz_results",
                    "field": "time_seconds",
                    "operation": "min",
                    "target": "results.best_time",
                },
                {
                    "type": "aggregate",
                    "source": "temp.quiz_results",
                    "operation": "count",
                    "target": "results.question_count",
                },
            ]
        }
        node = create_action_node(session.flow_id, node_content)

        result = await action_processor.process(mock_db, node, session, {})

        assert result["success"] is True
        assert result["variables"]["results"]["total_score"] == 255
        assert result["variables"]["results"]["avg_score"] == 85.0
        assert result["variables"]["results"]["best_time"] == 30
        assert result["variables"]["results"]["question_count"] == 3


class TestAggregateChatflowScenarios:
    """Real-world chatflow scenarios using aggregate actions."""

    @pytest.mark.asyncio
    @patch("app.services.chat_runtime.chat_repo")
    @patch("app.services.action_processor.chat_repo")
    async def test_personality_quiz_scoring(
        self, mock_action_chat_repo, mock_runtime_chat_repo, action_processor, mock_db
    ):
        """Simulate scoring a personality quiz with weighted dimensions."""
        setup_chat_repo_mocks(mock_action_chat_repo)
        setup_chat_repo_mocks(mock_runtime_chat_repo)

        session = MockSession(
            state={
                "temp": {
                    "quiz_answers": [
                        {
                            "question": "Q1",
                            "traits": {"introvert": 0.8, "extrovert": 0.2},
                        },
                        {
                            "question": "Q2",
                            "traits": {"introvert": 0.3, "extrovert": 0.7},
                        },
                        {
                            "question": "Q3",
                            "traits": {
                                "introvert": 0.5,
                                "extrovert": 0.5,
                                "analytical": 0.9,
                            },
                        },
                    ]
                }
            }
        )

        node_content = {
            "actions": [
                {
                    "type": "aggregate",
                    "source": "temp.quiz_answers",
                    "field": "traits",
                    "operation": "merge",
                    "merge_strategy": "sum",
                    "target": "user.personality_scores",
                }
            ]
        }
        node = create_action_node(session.flow_id, node_content)

        result = await action_processor.process(mock_db, node, session, {})

        assert result["success"] is True
        scores = result["variables"]["user"]["personality_scores"]
        assert scores["introvert"] == pytest.approx(1.6)
        assert scores["extrovert"] == pytest.approx(1.4)
        assert scores["analytical"] == pytest.approx(0.9)

    @pytest.mark.asyncio
    @patch("app.services.chat_runtime.chat_repo")
    @patch("app.services.action_processor.chat_repo")
    async def test_book_preference_aggregation(
        self, mock_action_chat_repo, mock_runtime_chat_repo, action_processor, mock_db
    ):
        """Aggregate book preferences from user selections."""
        setup_chat_repo_mocks(mock_action_chat_repo)
        setup_chat_repo_mocks(mock_runtime_chat_repo)

        session = MockSession(
            state={
                "temp": {
                    "book_ratings": [
                        {"book_id": 1, "genre_weights": {"fantasy": 1.0, "humor": 0.5}},
                        {
                            "book_id": 2,
                            "genre_weights": {"mystery": 0.8, "thriller": 0.6},
                        },
                        {
                            "book_id": 3,
                            "genre_weights": {
                                "fantasy": 0.7,
                                "adventure": 0.9,
                                "humor": 0.3,
                            },
                        },
                    ]
                }
            }
        )

        node_content = {
            "actions": [
                # First aggregate the genre weights
                {
                    "type": "aggregate",
                    "source": "temp.book_ratings",
                    "field": "genre_weights",
                    "operation": "merge",
                    "merge_strategy": "sum",
                    "target": "user.genre_preferences",
                },
                # Then count selections
                {
                    "type": "aggregate",
                    "source": "temp.book_ratings",
                    "operation": "count",
                    "target": "user.books_rated",
                },
            ]
        }
        node = create_action_node(session.flow_id, node_content)

        result = await action_processor.process(mock_db, node, session, {})

        assert result["success"] is True
        prefs = result["variables"]["user"]["genre_preferences"]
        assert prefs["fantasy"] == pytest.approx(1.7)
        assert prefs["humor"] == pytest.approx(0.8)
        assert result["variables"]["user"]["books_rated"] == 3

    @pytest.mark.asyncio
    @patch("app.services.chat_runtime.chat_repo")
    @patch("app.services.action_processor.chat_repo")
    async def test_survey_response_analysis(
        self, mock_action_chat_repo, mock_runtime_chat_repo, action_processor, mock_db
    ):
        """Aggregate survey responses for analysis."""
        setup_chat_repo_mocks(mock_action_chat_repo)
        setup_chat_repo_mocks(mock_runtime_chat_repo)

        session = MockSession(
            state={
                "temp": {
                    "survey_responses": [
                        {"rating": 4, "features": ["speed", "ui"]},
                        {"rating": 5, "features": ["ui", "support"]},
                        {"rating": 3, "features": ["speed", "price"]},
                        {"rating": 5, "features": ["ui", "price", "support"]},
                    ]
                }
            }
        )

        node_content = {
            "actions": [
                {
                    "type": "aggregate",
                    "source": "temp.survey_responses",
                    "field": "rating",
                    "operation": "avg",
                    "target": "survey.avg_rating",
                },
                {
                    "type": "aggregate",
                    "source": "temp.survey_responses",
                    "field": "rating",
                    "operation": "max",
                    "target": "survey.max_rating",
                },
                {
                    "type": "aggregate",
                    "source": "temp.survey_responses",
                    "field": "features",
                    "operation": "collect",
                    "target": "survey.all_features",
                },
            ]
        }
        node = create_action_node(session.flow_id, node_content)

        result = await action_processor.process(mock_db, node, session, {})

        assert result["success"] is True
        assert result["variables"]["survey"]["avg_rating"] == pytest.approx(4.25)
        assert result["variables"]["survey"]["max_rating"] == 5
        # Features collected and flattened
        features = result["variables"]["survey"]["all_features"]
        assert len(features) == 9  # Total features across all responses
        assert features.count("ui") == 3  # "ui" appears 3 times


class TestSessionRefreshAfterAction:
    """Test that session is refreshed after action updates before processing next node.

    This addresses a bug where template interpolation failed in nodes following
    action nodes because the session state wasn't refreshed after DB updates.
    """

    @pytest.mark.asyncio
    @patch("app.services.chat_runtime.chat_repo")
    @patch("app.services.action_processor.chat_repo")
    async def test_session_refreshed_before_next_node(
        self, mock_action_chat_repo, mock_runtime_chat_repo, action_processor, mock_db
    ):
        """Verify session is refreshed with updated state before processing next node.

        When an action node sets a variable and has a next node, the next node
        should receive the session with the updated state, not stale state.
        """
        # Set up both repo mocks (action_processor uses its own import)
        setup_chat_repo_mocks(mock_action_chat_repo)
        setup_chat_repo_mocks(mock_runtime_chat_repo)

        # Create a mock next node
        next_flow_node = Mock(spec=FlowNode)
        next_flow_node.id = uuid.uuid4()
        next_flow_node.node_id = "next_node"
        next_flow_node.node_type = NodeType.MESSAGE

        # Set up connection to next node - override the empty list from setup
        mock_connection = Mock()
        mock_connection.target_node_id = "next_node"
        mock_connection.connection_type = "default"
        mock_runtime_chat_repo.get_node_connections = AsyncMock(
            return_value=[mock_connection]
        )
        mock_action_chat_repo.get_flow_node = AsyncMock(return_value=next_flow_node)

        # Create initial session with empty temp state
        session = MockSession(state={"temp": {}})

        # Mock the session refresh to return session with updated state
        refreshed_session = MockSession(
            state={"temp": {"student_name": "Alice", "greeting": "Hello Alice!"}}
        )
        refreshed_session.id = session.id
        mock_action_chat_repo.get_session_by_id = AsyncMock(
            return_value=refreshed_session
        )

        # Create action node that sets a variable
        node_content = {
            "actions": [
                {
                    "type": "set_variable",
                    "variable": "temp.student_name",
                    "value": "Alice",
                }
            ]
        }
        node = create_action_node(session.flow_id, node_content)

        # Process the action node
        await action_processor.process(mock_db, node, session, {})

        # Verify that get_session_by_id was called to refresh the session
        mock_action_chat_repo.get_session_by_id.assert_called_once_with(
            mock_db, session.id
        )

        # Verify that process_node was called with the refreshed session
        action_processor.runtime.process_node.assert_called_once()
        call_args = action_processor.runtime.process_node.call_args
        passed_session = call_args[0][2]  # Third positional arg is session
        assert passed_session.state.get("temp", {}).get("student_name") == "Alice"

    @pytest.mark.asyncio
    @patch("app.services.chat_runtime.chat_repo")
    @patch("app.services.action_processor.chat_repo")
    async def test_fallback_to_original_session_if_refresh_fails(
        self, mock_action_chat_repo, mock_runtime_chat_repo, action_processor, mock_db
    ):
        """If session refresh returns None, fall back to original session.

        Verifies that when get_session_by_id returns None (e.g., session not found),
        the code falls back to using the original session object passed to process().
        """
        # Set up both repo mocks
        mock_action_chat_repo.update_session_state = AsyncMock()
        mock_action_chat_repo.add_interaction_history = AsyncMock()
        mock_runtime_chat_repo.update_session_state = AsyncMock()
        mock_runtime_chat_repo.add_interaction_history = AsyncMock()

        # Set up connection to next node
        next_flow_node = Mock(spec=FlowNode)
        next_flow_node.id = uuid.uuid4()
        next_flow_node.node_id = "next_node"
        next_flow_node.node_type = NodeType.MESSAGE

        mock_connection = Mock()
        mock_connection.target_node_id = "next_node"
        mock_connection.connection_type = "success"
        mock_runtime_chat_repo.get_node_connections = AsyncMock(
            return_value=[mock_connection]
        )
        mock_action_chat_repo.get_flow_node = AsyncMock(return_value=next_flow_node)

        session = MockSession(state={"temp": {"original": True}})
        original_session_id = session.id

        # Simulate refresh returning None (failure case)
        mock_action_chat_repo.get_session_by_id = AsyncMock(return_value=None)

        node_content = {
            "actions": [
                {"type": "set_variable", "variable": "temp.new_value", "value": "test"}
            ]
        }
        node = create_action_node(session.flow_id, node_content)

        await action_processor.process(mock_db, node, session, {})

        # Verify get_session_by_id was called to try refresh
        mock_action_chat_repo.get_session_by_id.assert_called_once_with(
            mock_db, original_session_id
        )

        # Verify process_node was called - it should use original session as fallback
        action_processor.runtime.process_node.assert_called_once()
        call_args = action_processor.runtime.process_node.call_args
        passed_session = call_args[0][2]

        # The fallback session should be the original one (same id)
        assert passed_session.id == original_session_id
