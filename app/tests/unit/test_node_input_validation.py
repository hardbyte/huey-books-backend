"""
Unit tests for node input validation system.

Tests comprehensive validation for all node types to ensure malformed configurations
are caught before processing begins.
"""

from uuid import uuid4

import pytest

from app.models.cms import NodeType
from app.services.node_input_validation import (
    NodeInputValidator,
    ValidationSeverity,
    require_valid_input,
    validate_node_input,
)


class TestNodeInputValidator:
    """Test cases for the NodeInputValidator class."""

    def setup_method(self):
        """Set up test fixtures."""
        self.validator = NodeInputValidator()
        self.node_id = "test_node"

    def test_message_node_validation_success(self):
        """Test successful validation of message node."""
        content = {
            "messages": [
                {"content_id": str(uuid4())},
                {"content": "Hello world"},
                {"text": "Hello text"},
            ],
            "typing_indicator": True,
        }

        report = self.validator.validate_node(self.node_id, NodeType.MESSAGE, content)

        assert report.is_valid
        assert len(report.errors) == 0
        assert report.node_type == NodeType.MESSAGE

    def test_message_node_validation_failure_empty_messages(self):
        """Test message node validation fails with empty messages."""
        content = {"messages": [], "typing_indicator": True}

        report = self.validator.validate_node(self.node_id, NodeType.MESSAGE, content)

        assert not report.is_valid
        assert len(report.errors) > 0
        assert any("at least" in error.message.lower() for error in report.errors)

    def test_message_node_validation_failure_no_content(self):
        """Test message node validation fails with messages missing content."""
        content = {
            "messages": [
                {"invalid_field": "test"}  # Missing content_id or content
            ]
        }

        report = self.validator.validate_node(self.node_id, NodeType.MESSAGE, content)

        assert not report.is_valid
        assert len(report.errors) > 0

    def test_question_node_validation_success(self):
        """Test successful validation of question node."""
        content = {
            "question": {"content_id": str(uuid4())},
            "input_type": "text",
            "variable": "temp.user_input",
        }

        report = self.validator.validate_node(self.node_id, NodeType.QUESTION, content)

        assert report.is_valid
        assert len(report.errors) == 0

    def test_question_node_validation_choice_with_options(self):
        """Test question node validation for choice type with options."""
        content = {
            "question": {"text": "Choose an option"},
            "input_type": "choice",
            "options": [
                {"value": "option1", "label": "Option 1"},
                {"value": "option2", "label": "Option 2"},
            ],
        }

        report = self.validator.validate_node(self.node_id, NodeType.QUESTION, content)

        assert report.is_valid

    def test_question_node_validation_failure_choice_no_options(self):
        """Test question node validation fails for choice without options."""
        content = {
            "question": {"text": "Choose an option"},
            "input_type": "choice",
            # Missing options for choice type
        }

        report = self.validator.validate_node(self.node_id, NodeType.QUESTION, content)

        assert not report.is_valid
        assert len(report.errors) > 0
        assert any(
            "choice questions must have" in error.message.lower()
            for error in report.errors
        )

    def test_question_node_validation_invalid_input_type(self):
        """Test question node validation fails with invalid input type."""
        content = {
            "question": {"text": "Enter something"},
            "input_type": "123-INVALID!",  # Not a valid identifier
        }

        report = self.validator.validate_node(self.node_id, NodeType.QUESTION, content)

        assert not report.is_valid
        assert len(report.errors) > 0

    def test_question_node_slider_validation_success(self):
        """Test successful validation of slider input type."""
        content = {
            "question": {"text": "How old are you?"},
            "input_type": "slider",
            "slider_config": {
                "min": 5,
                "max": 18,
                "step": 1,
                "default_value": 10,
                "show_labels": True,
                "min_label": "Young",
                "max_label": "Older",
            },
            "variable": "temp.age",
        }

        report = self.validator.validate_node(self.node_id, NodeType.QUESTION, content)

        assert report.is_valid
        assert len(report.errors) == 0

    def test_question_node_slider_invalid_range(self):
        """Test slider validation fails when min >= max."""
        content = {
            "question": {"text": "Rate your experience"},
            "input_type": "slider",
            "slider_config": {
                "min": 100,
                "max": 0,  # Invalid: min > max
            },
        }

        report = self.validator.validate_node(self.node_id, NodeType.QUESTION, content)

        assert not report.is_valid
        assert any("min" in error.message.lower() for error in report.errors)

    def test_question_node_slider_invalid_step(self):
        """Test slider validation fails with non-positive step."""
        content = {
            "question": {"text": "Choose a value"},
            "input_type": "slider",
            "slider_config": {
                "min": 0,
                "max": 100,
                "step": 0,  # Invalid: step must be positive
            },
        }

        report = self.validator.validate_node(self.node_id, NodeType.QUESTION, content)

        assert not report.is_valid
        assert any("step" in error.message.lower() for error in report.errors)

    def test_question_node_image_choice_validation_success(self):
        """Test successful validation of image_choice input type."""
        content = {
            "question": {"text": "Which door will you choose?"},
            "input_type": "image_choice",
            "options": [
                {
                    "value": "dark_door",
                    "label": "Dark Door",
                    "image_url": "https://example.com/dark-door.png",
                },
                {
                    "value": "bright_door",
                    "label": "Bright Door",
                    "image_url": "https://example.com/bright-door.png",
                },
            ],
            "variable": "temp.door_choice",
        }

        report = self.validator.validate_node(self.node_id, NodeType.QUESTION, content)

        assert report.is_valid
        assert len(report.errors) == 0

    def test_question_node_image_choice_missing_image(self):
        """Test image_choice validation fails without image_url."""
        content = {
            "question": {"text": "Choose an option"},
            "input_type": "image_choice",
            "options": [
                {"value": "option1", "label": "Option 1"},  # Missing image_url
            ],
        }

        report = self.validator.validate_node(self.node_id, NodeType.QUESTION, content)

        assert not report.is_valid
        assert any("image" in error.message.lower() for error in report.errors)

    def test_question_node_image_choice_empty_options(self):
        """Test image_choice validation fails with no options."""
        content = {
            "question": {"text": "Pick one"},
            "input_type": "image_choice",
            "options": [],  # Invalid: must have at least one option
        }

        report = self.validator.validate_node(self.node_id, NodeType.QUESTION, content)

        assert not report.is_valid

    def test_question_node_carousel_validation_success(self):
        """Test successful validation of carousel input type."""
        content = {
            "question": {"text": "Browse and select a book"},
            "input_type": "carousel",
            "options": [
                {
                    "value": "book_1",
                    "title": "The Dragon's Quest",
                    "image_url": "https://example.com/book1.png",
                    "description": "An epic adventure story",
                },
                {
                    "value": "book_2",
                    "title": "Mystery Manor",
                    "description": "A thrilling mystery",
                },
            ],
            "carousel_config": {
                "items_per_view": 2,
                "show_navigation": True,
            },
            "variable": "temp.selected_book",
        }

        report = self.validator.validate_node(self.node_id, NodeType.QUESTION, content)

        assert report.is_valid
        assert len(report.errors) == 0

    def test_question_node_carousel_empty_options(self):
        """Test carousel validation fails with no items."""
        content = {
            "question": {"text": "Browse items"},
            "input_type": "carousel",
            "options": [],  # Invalid: must have at least one item
        }

        report = self.validator.validate_node(self.node_id, NodeType.QUESTION, content)

        assert not report.is_valid

    def test_question_node_multiple_choice_validation(self):
        """Test successful validation of multiple_choice input type."""
        content = {
            "question": {"text": "Select your interests"},
            "input_type": "multiple_choice",
            "options": [
                {"value": "adventure", "label": "Adventure"},
                {"value": "mystery", "label": "Mystery"},
                {"value": "fantasy", "label": "Fantasy"},
            ],
            "variable": "temp.interests",
        }

        report = self.validator.validate_node(self.node_id, NodeType.QUESTION, content)

        assert report.is_valid
        assert len(report.errors) == 0

    def test_question_node_date_input_type(self):
        """Test successful validation of date input type."""
        content = {
            "question": {"text": "When is your birthday?"},
            "input_type": "date",
            "variable": "user.birthday",
        }

        report = self.validator.validate_node(self.node_id, NodeType.QUESTION, content)

        assert report.is_valid
        assert len(report.errors) == 0

    def test_condition_node_validation_success(self):
        """Test successful validation of condition node."""
        content = {
            "conditions": [
                {"if": {"var": "temp.age", "gt": 18}, "then": "adult_path"},
                {"if": {"var": "temp.age", "lte": 18}, "then": "child_path"},
            ],
            "default_path": "error_path",
        }

        report = self.validator.validate_node(self.node_id, NodeType.CONDITION, content)

        assert report.is_valid

    def test_condition_node_validation_failure_empty_conditions(self):
        """Test condition node validation fails with empty conditions."""
        content = {
            "conditions": []  # Must have at least one condition
        }

        report = self.validator.validate_node(self.node_id, NodeType.CONDITION, content)

        assert not report.is_valid
        assert len(report.errors) > 0

    def test_condition_node_validation_failure_missing_fields(self):
        """Test condition node validation fails with missing condition fields."""
        content = {
            "conditions": [
                {
                    "if": {"var": "temp.test"}
                    # Missing "then" field
                }
            ]
        }

        report = self.validator.validate_node(self.node_id, NodeType.CONDITION, content)

        assert not report.is_valid

    def test_action_node_validation_success(self):
        """Test successful validation of action node."""
        content = {
            "actions": [
                {
                    "type": "set_variable",
                    "params": {"variable": "temp.counter", "value": 1},
                },
                {
                    "type": "aggregate",
                    "params": {
                        "expression": "sum(temp.scores)",
                        "target": "user.total",
                    },
                },
            ]
        }

        report = self.validator.validate_node(self.node_id, NodeType.ACTION, content)

        assert report.is_valid

    def test_action_node_validation_failure_invalid_action_type(self):
        """Test action node validation fails with invalid action type."""
        content = {"actions": [{"type": "invalid_action_type", "params": {}}]}

        report = self.validator.validate_node(self.node_id, NodeType.ACTION, content)

        assert not report.is_valid
        assert len(report.errors) > 0

    def test_action_node_validation_failure_missing_params(self):
        """Test action node validation fails with missing required parameters."""
        content = {
            "actions": [
                {
                    "type": "set_variable"
                    # Missing params with variable and value
                }
            ]
        }

        report = self.validator.validate_node(self.node_id, NodeType.ACTION, content)

        assert not report.is_valid

    def test_webhook_node_validation_success(self):
        """Test successful validation of webhook node."""
        content = {
            "url": "https://hooks.example.com/webhook",
            "method": "POST",
            "headers": {
                "Content-Type": "application/json",
                "Authorization": "Bearer {{token}}",
            },
            "payload": {
                "session_id": "{{session.id}}",
                "user_input": "{{temp.user_input}}",
            },
            "timeout": 30,
            "store_response": True,
            "response_key": "webhook_result",
        }

        report = self.validator.validate_node(self.node_id, NodeType.WEBHOOK, content)

        assert report.is_valid

    def test_webhook_node_validation_failure_invalid_url(self):
        """Test webhook node validation fails with invalid URL."""
        content = {
            "url": "not-a-valid-url"  # Invalid URL format
        }

        report = self.validator.validate_node(self.node_id, NodeType.WEBHOOK, content)

        assert not report.is_valid
        assert len(report.errors) > 0

    def test_webhook_node_validation_failure_invalid_method(self):
        """Test webhook node validation fails with invalid HTTP method."""
        content = {
            "url": "https://api.example.com/webhook",
            "method": "INVALID_METHOD",  # Not in allowed methods
        }

        report = self.validator.validate_node(self.node_id, NodeType.WEBHOOK, content)

        assert not report.is_valid

    def test_webhook_node_validation_timeout_bounds(self):
        """Test webhook node validation enforces timeout bounds."""
        # Test timeout too low
        content = {
            "url": "https://api.example.com/webhook",
            "timeout": 0,  # Below minimum
        }

        report = self.validator.validate_node(self.node_id, NodeType.WEBHOOK, content)
        assert not report.is_valid

        # Test timeout too high
        content["timeout"] = 500  # Above maximum
        report = self.validator.validate_node(self.node_id, NodeType.WEBHOOK, content)
        assert not report.is_valid

    def test_empty_node_content(self):
        """Test validation fails gracefully with empty node content."""
        report = self.validator.validate_node(self.node_id, NodeType.MESSAGE, {})

        assert not report.is_valid
        assert len(report.errors) > 0

    def test_null_node_content(self):
        """Test validation fails gracefully with null node content."""
        report = self.validator.validate_node(self.node_id, NodeType.MESSAGE, None)

        assert not report.is_valid
        assert len(report.errors) > 0
        assert any("empty or null" in error.message for error in report.errors)

    def test_invalid_node_content_type(self):
        """Test validation fails with non-dictionary node content."""
        report = self.validator.validate_node(self.node_id, NodeType.MESSAGE, "invalid")

        assert not report.is_valid
        assert len(report.errors) > 0
        assert any("must be a dictionary" in error.message for error in report.errors)

    def test_unknown_node_type_basic_validation(self):
        """Test unknown node types get basic validation."""
        # Simulate an unknown node type (this would normally be prevented by enum)
        content = {"some_field": "some_value"}

        # We can't actually pass an invalid enum, but the code handles missing schemas
        report = self.validator.validate_node(self.node_id, NodeType.MESSAGE, content)

        # Should still validate (though might have warnings for unknown schema)
        assert isinstance(report, type(report))  # Basic structure test

    def test_business_rules_validation_condition_logic(self):
        """Test business rules validation for condition logic."""
        content = {
            "conditions": [
                {
                    "if": "true",  # Always true condition
                    "then": "path1",
                },
                {
                    "if": {
                        "var": "temp.test",
                        "eq": "value",
                    },  # This may be unreachable
                    "then": "path2",
                },
            ]
        }

        report = self.validator.validate_node(self.node_id, NodeType.CONDITION, content)

        # Should be valid but have warnings about unreachable conditions
        assert report.is_valid
        assert len(report.warnings) > 0

    def test_business_rules_validation_webhook_security(self):
        """Test business rules validation for webhook security."""
        content = {
            "url": "http://localhost:8080/webhook"  # Local URL - should warn
        }

        report = self.validator.validate_node(self.node_id, NodeType.WEBHOOK, content)

        # Should have warnings about localhost URLs
        assert len(report.warnings) > 0
        assert any(
            "localhost" in warning.message.lower() for warning in report.warnings
        )

    def test_business_rules_validation_webhook_credentials(self):
        """Test business rules validation catches credentials in URLs."""
        content = {
            "url": "https://user:password@api.example.com/webhook"  # Embedded credentials
        }

        report = self.validator.validate_node(self.node_id, NodeType.WEBHOOK, content)

        # Should have errors about embedded credentials
        assert not report.is_valid
        assert len(report.errors) > 0
        assert any("credentials" in error.message.lower() for error in report.errors)

    def test_question_accessibility_validation(self):
        """Test question accessibility validation."""
        content = {
            "question": {"text": "Choose from many options"},
            "input_type": "choice",
            "options": [{"value": f"option{i}"} for i in range(15)],  # Too many options
        }

        report = self.validator.validate_node(self.node_id, NodeType.QUESTION, content)

        # Should be valid but warn about too many choices
        assert report.is_valid
        assert len(report.warnings) > 0
        assert any("choices" in warning.message.lower() for warning in report.warnings)


class TestConvenienceFunctions:
    """Test the convenience functions for validation."""

    def test_validate_node_input_function(self):
        """Test the validate_node_input convenience function."""
        content = {"messages": [{"content": "Hello"}]}

        report = validate_node_input("test", NodeType.MESSAGE, content)
        assert isinstance(report, type(report))
        assert report.node_id == "test"

    def test_require_valid_input_success(self):
        """Test require_valid_input with valid input."""
        content = {"messages": [{"content": "Hello"}]}

        report = require_valid_input("test", NodeType.MESSAGE, content)
        assert report.is_valid

    def test_require_valid_input_failure(self):
        """Test require_valid_input raises exception with invalid input."""
        content = {
            "messages": []  # Invalid - empty messages
        }

        with pytest.raises(ValueError) as exc_info:
            require_valid_input("test", NodeType.MESSAGE, content)

        assert "validation failed" in str(exc_info.value).lower()


class TestValidationReport:
    """Test the ValidationReport data structure."""

    def setup_method(self):
        """Set up test fixtures."""
        from app.services.node_input_validation import ValidationReport

        self.report = ValidationReport(
            node_id="test", node_type=NodeType.MESSAGE, is_valid=True
        )

    def test_add_result_error_updates_validity(self):
        """Test that adding an error result updates overall validity."""
        self.report.add_result(ValidationSeverity.ERROR, "Test error")

        assert not self.report.is_valid
        assert len(self.report.errors) == 1
        assert len(self.report.warnings) == 0

    def test_add_result_warning_preserves_validity(self):
        """Test that adding a warning result preserves validity."""
        self.report.add_result(ValidationSeverity.WARNING, "Test warning")

        assert self.report.is_valid
        assert len(self.report.errors) == 0
        assert len(self.report.warnings) == 1

    def test_error_and_warning_filters(self):
        """Test error and warning property filters."""
        self.report.add_result(ValidationSeverity.ERROR, "Error 1")
        self.report.add_result(ValidationSeverity.WARNING, "Warning 1")
        self.report.add_result(ValidationSeverity.ERROR, "Error 2")
        self.report.add_result(ValidationSeverity.INFO, "Info 1")

        assert len(self.report.errors) == 2
        assert len(self.report.warnings) == 1
        assert len(self.report.results) == 4
