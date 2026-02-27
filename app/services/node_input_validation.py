"""
Node Input Validation - Rigorous validation for node processor inputs.

This module provides comprehensive validation for all node types to prevent runtime errors
due to malformed configurations. It validates node.content structure and required fields
before processing begins.
"""

import re
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from structlog import get_logger

from app.models.cms import NodeType
from app.schemas.cms import KNOWN_INPUT_TYPES

logger = get_logger()


class ValidationSeverity(str, Enum):
    """Severity levels for validation results."""

    ERROR = "error"  # Critical validation failure - processing cannot continue
    WARNING = "warning"  # Non-critical issue - processing can continue with defaults
    INFO = "info"  # Informational validation message


class ValidationResult(BaseModel):
    """Result of node input validation."""

    is_valid: bool
    severity: ValidationSeverity
    message: str
    field_path: Optional[str] = None
    suggested_fix: Optional[str] = None


class ValidationReport(BaseModel):
    """Complete validation report for a node."""

    node_id: str
    node_type: NodeType
    is_valid: bool
    results: List[ValidationResult] = []

    @property
    def errors(self) -> List[ValidationResult]:
        """Get only error-level validation results."""
        return [r for r in self.results if r.severity == ValidationSeverity.ERROR]

    @property
    def warnings(self) -> List[ValidationResult]:
        """Get only warning-level validation results."""
        return [r for r in self.results if r.severity == ValidationSeverity.WARNING]

    def add_result(
        self,
        severity: ValidationSeverity,
        message: str,
        field_path: Optional[str] = None,
        suggested_fix: Optional[str] = None,
    ):
        """Add a validation result to the report."""
        result = ValidationResult(
            is_valid=(severity != ValidationSeverity.ERROR),
            severity=severity,
            message=message,
            field_path=field_path,
            suggested_fix=suggested_fix,
        )
        self.results.append(result)

        # Update overall validity - if any error exists, node is invalid
        if severity == ValidationSeverity.ERROR:
            self.is_valid = False


# Node Content Validation Schemas


class MessageContentSchema(BaseModel):
    """Validation schema for message node content."""

    messages: List[Dict[str, Any]] = Field(..., min_length=1)
    typing_indicator: Optional[bool] = True

    @field_validator("messages")
    @classmethod
    def validate_messages(cls, v):
        """Validate message structure."""
        for i, msg in enumerate(v):
            if not isinstance(msg, dict):
                raise ValueError(f"Message {i} must be a dictionary")
            if "content_id" not in msg and "content" not in msg and "text" not in msg:
                raise ValueError(
                    f"Message {i} must have 'content_id', 'content', or 'text' field"
                )
        return v


class QuestionContentSchema(BaseModel):
    """Validation schema for question node content.

    input_type must be a lowercase identifier (e.g. text, choice, book_feedback).
    Common built-in types: text, number, email, phone, url, date, choice,
    multiple_choice, slider, image_choice, carousel. Flow authors can define
    custom input types without schema changes.
    """

    question: Dict[str, Any] = Field(...)
    input_type: str = Field(
        ...,
        pattern=r"^[a-z][a-z0-9_]{0,49}$",
        json_schema_extra={"examples": sorted(KNOWN_INPUT_TYPES)},
    )
    options: Optional[List[Dict[str, Any]]] = None
    validation: Optional[Dict[str, Any]] = None
    variable: Optional[str] = Field(None, pattern=r"^[a-zA-Z_][a-zA-Z0-9_.]*$")

    # Slider-specific configuration
    slider_config: Optional[Dict[str, Any]] = None

    # Carousel-specific configuration
    carousel_config: Optional[Dict[str, Any]] = None

    @field_validator("question")
    @classmethod
    def validate_question(cls, v):
        """Validate question structure."""
        if not isinstance(v, dict):
            raise ValueError("Question must be a dictionary")
        if "content_id" not in v and "question" not in v and "text" not in v:
            raise ValueError(
                "Question must have 'content_id', 'question', or 'text' field"
            )
        return v

    @model_validator(mode="after")
    def validate_input_type_config(self):
        """Validate configuration for specific input types."""
        # Validate choice and multiple_choice options
        if self.input_type in ("choice", "multiple_choice"):
            if not self.options or len(self.options) == 0:
                raise ValueError("Choice questions must have at least one option")
            for i, option in enumerate(self.options):
                if not isinstance(option, dict):
                    raise ValueError(f"Option {i} must be a dictionary")
                if "value" not in option:
                    raise ValueError(f"Option {i} must have a 'value' field")

        # Validate image_choice options - must have image_url
        if self.input_type == "image_choice":
            if not self.options or len(self.options) == 0:
                raise ValueError("Image choice questions must have at least one option")
            for i, option in enumerate(self.options):
                if not isinstance(option, dict):
                    raise ValueError(f"Option {i} must be a dictionary")
                if "value" not in option:
                    raise ValueError(f"Option {i} must have a 'value' field")
                if "image_url" not in option and "image" not in option:
                    raise ValueError(
                        f"Option {i} must have an 'image_url' or 'image' field for image_choice"
                    )

        # Validate slider configuration
        if self.input_type == "slider":
            config = self.slider_config or {}
            min_val = config.get("min", 0)
            max_val = config.get("max", 100)
            if min_val >= max_val:
                raise ValueError("Slider 'min' must be less than 'max'")
            step = config.get("step", 1)
            if step <= 0:
                raise ValueError("Slider 'step' must be positive")

        # Validate carousel configuration
        if self.input_type == "carousel":
            if not self.options or len(self.options) == 0:
                raise ValueError("Carousel must have at least one item")
            for i, option in enumerate(self.options):
                if not isinstance(option, dict):
                    raise ValueError(f"Carousel item {i} must be a dictionary")
                if "value" not in option:
                    raise ValueError(f"Carousel item {i} must have a 'value' field")

        return self


class ConditionContentSchema(BaseModel):
    """Validation schema for condition node content."""

    conditions: List[Dict[str, Any]] = Field(..., min_length=1)
    default_path: Optional[str] = None

    @field_validator("conditions")
    @classmethod
    def validate_conditions(cls, v):
        """Validate condition structure."""
        for i, condition in enumerate(v):
            if not isinstance(condition, dict):
                raise ValueError(f"Condition {i} must be a dictionary")
            if "if" not in condition:
                raise ValueError(f"Condition {i} must have an 'if' field")
            if "then" not in condition:
                raise ValueError(f"Condition {i} must have a 'then' field")
        return v


class ActionContentSchema(BaseModel):
    """Validation schema for action node content."""

    actions: List[Dict[str, Any]] = Field(..., min_length=1)

    @field_validator("actions")
    @classmethod
    def validate_actions(cls, v):
        """Validate action structure."""
        valid_action_types = {
            "set_variable",
            "increment",
            "append",
            "remove",
            "clear",
            "calculate",
            "aggregate",
        }

        for i, action in enumerate(v):
            if not isinstance(action, dict):
                raise ValueError(f"Action {i} must be a dictionary")

            action_type = action.get("type")
            if not action_type:
                raise ValueError(f"Action {i} must have a 'type' field")

            if action_type not in valid_action_types:
                raise ValueError(
                    f"Action {i} has invalid type '{action_type}'. Valid types: {valid_action_types}"
                )

            # Validate specific action parameters
            cls._validate_action_params(action, i)

        return v

    @staticmethod
    def _validate_action_params(action: Dict[str, Any], index: int):
        """Validate parameters for specific action types."""
        action_type = action["type"]
        params = action.get("params", action)  # Support both nested and flat structure

        if action_type == "set_variable":
            if "variable" not in params:
                raise ValueError(
                    f"Action {index} (set_variable) must have 'variable' field"
                )
            if "value" not in params:
                raise ValueError(
                    f"Action {index} (set_variable) must have 'value' field"
                )

        elif action_type == "increment":
            if "variable" not in params:
                raise ValueError(
                    f"Action {index} (increment) must have 'variable' field"
                )

        elif action_type in ["append", "remove"]:
            if "variable" not in params:
                raise ValueError(
                    f"Action {index} ({action_type}) must have 'variable' field"
                )
            if "value" not in params:
                raise ValueError(
                    f"Action {index} ({action_type}) must have 'value' field"
                )

        elif action_type == "clear":
            if "variable" not in params:
                raise ValueError(f"Action {index} (clear) must have 'variable' field")

        elif action_type == "calculate":
            if "expression" not in params:
                raise ValueError(
                    f"Action {index} (calculate) must have 'expression' field"
                )
            if "result_variable" not in params:
                raise ValueError(
                    f"Action {index} (calculate) must have 'result_variable' field"
                )

        elif action_type == "aggregate":
            if "expression" not in params:
                raise ValueError(
                    f"Action {index} (aggregate) must have 'expression' field"
                )
            if "target" not in params:
                raise ValueError(f"Action {index} (aggregate) must have 'target' field")


class WebhookContentSchema(BaseModel):
    """Validation schema for webhook node content."""

    url: str = Field(..., pattern=r"^https?://.*")
    method: str = Field(default="POST", pattern=r"^(GET|POST|PUT|PATCH|DELETE)$")
    headers: Optional[Dict[str, str]] = None
    payload: Optional[Dict[str, Any]] = None
    timeout: Optional[int] = Field(default=30, ge=1, le=300)  # 1-300 seconds
    store_response: Optional[bool] = False
    response_key: Optional[str] = Field(None, pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")

    @field_validator("url")
    @classmethod
    def validate_url(cls, v):
        """Validate URL format allows variable substitution."""
        # Allow variable substitution patterns like {{variable}} or {variable}
        url_pattern = re.compile(
            r"^https?://[a-zA-Z0-9.\-_{}]+(/[a-zA-Z0-9.\-_{}]*)*(\?[a-zA-Z0-9=&\-_{}]*)?$"
        )
        if not url_pattern.match(v.replace("{{", "").replace("}}", "")):
            raise ValueError("Invalid URL format")
        return v


class ScriptContentSchema(BaseModel):
    """Validation schema for script node content."""

    code: str = Field(..., min_length=1)
    language: str = Field(..., pattern=r"^(typescript|javascript)$")
    sandbox: Optional[str] = Field(default="strict", pattern=r"^(strict|permissive)$")
    inputs: Optional[Dict[str, str]] = None
    outputs: Optional[List[str]] = None
    dependencies: Optional[List[str]] = None
    timeout: Optional[int] = Field(default=5000, ge=1000, le=60000)
    description: Optional[str] = None

    @field_validator("dependencies")
    @classmethod
    def validate_dependencies(cls, v):
        """Validate dependency URLs are from trusted sources."""
        if not v:
            return v

        trusted_domains = [
            "cdn.jsdelivr.net",
            "unpkg.com",
            "cdnjs.cloudflare.com",
        ]

        for i, dep in enumerate(v):
            if not isinstance(dep, str):
                raise ValueError(f"Dependency {i} must be a string URL")

            if not dep.startswith("https://"):
                raise ValueError(f"Dependency {i} must use HTTPS")

            # Check if from trusted domain
            is_trusted = any(domain in dep for domain in trusted_domains)
            if not is_trusted:
                raise ValueError(
                    f"Dependency {i} URL '{dep}' is not from a trusted CDN. "
                    f"Trusted domains: {', '.join(trusted_domains)}"
                )

        return v

    @field_validator("outputs")
    @classmethod
    def validate_outputs(cls, v):
        """Validate output variable paths."""
        if not v:
            return v

        for i, output in enumerate(v):
            if not isinstance(output, str):
                raise ValueError(f"Output {i} must be a string")

            if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_.]*$", output):
                raise ValueError(
                    f"Output {i} '{output}' has invalid format. "
                    "Must be a valid variable path like 'temp.result' or 'user.field'"
                )

        return v

    @field_validator("inputs")
    @classmethod
    def validate_inputs(cls, v):
        """Validate input variable mappings."""
        if not v:
            return v

        for key, path in v.items():
            if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", key):
                raise ValueError(
                    f"Input key '{key}' has invalid format. "
                    "Must be a valid identifier like 'bookCount' or 'userName'"
                )

            if not isinstance(path, str):
                raise ValueError(f"Input path for '{key}' must be a string")

            if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_.]*$", path):
                raise ValueError(
                    f"Input path '{path}' has invalid format. "
                    "Must be a valid variable path like 'session.field' or 'user.name'"
                )

        return v


class NodeInputValidator:
    """
    Comprehensive validator for node processor inputs.

    This validator ensures that node.content contains the required fields
    and valid structure before processing begins, preventing runtime errors.
    """

    # Mapping of node types to their validation schemas
    VALIDATION_SCHEMAS = {
        NodeType.MESSAGE: MessageContentSchema,
        NodeType.QUESTION: QuestionContentSchema,
        NodeType.CONDITION: ConditionContentSchema,
        NodeType.ACTION: ActionContentSchema,
        NodeType.WEBHOOK: WebhookContentSchema,
        NodeType.SCRIPT: ScriptContentSchema,
    }

    def __init__(self):
        self.logger = logger

    def validate_node(
        self, node_id: str, node_type: NodeType, node_content: Dict[str, Any]
    ) -> ValidationReport:
        """
        Validate a node's content structure and required fields.

        Args:
            node_id: Unique identifier for the node
            node_type: Type of the node (message, question, condition, etc.)
            node_content: The node.content dictionary to validate

        Returns:
            ValidationReport with detailed validation results
        """
        report = ValidationReport(node_id=node_id, node_type=node_type, is_valid=True)

        self.logger.debug(
            "Validating node input", node_id=node_id, node_type=node_type.value
        )

        # Check if content exists
        if not node_content:
            report.add_result(
                ValidationSeverity.ERROR,
                "Node content is empty or null",
                suggested_fix="Add content dictionary to node configuration",
            )
            return report

        if not isinstance(node_content, dict):
            report.add_result(
                ValidationSeverity.ERROR,
                f"Node content must be a dictionary, got {type(node_content).__name__}",
                suggested_fix="Ensure node content is a valid JSON object",
            )
            return report

        # Get validation schema for this node type
        schema_class = self.VALIDATION_SCHEMAS.get(node_type)
        if not schema_class:
            report.add_result(
                ValidationSeverity.WARNING,
                f"No validation schema defined for node type '{node_type.value}'",
                suggested_fix="Add validation schema for this node type",
            )
            # Perform basic validation for unknown node types
            self._validate_basic_structure(node_content, report)
            return report

        # Validate using Pydantic schema
        try:
            schema_class(**node_content)
            report.add_result(ValidationSeverity.INFO, "Node content validation passed")

        except ValidationError as e:
            for error in e.errors():
                field_path = (
                    ".".join(str(x) for x in error["loc"]) if error["loc"] else None
                )
                report.add_result(
                    ValidationSeverity.ERROR,
                    f"Validation error: {error['msg']}",
                    field_path=field_path,
                    suggested_fix=self._get_validation_fix_suggestion(error),
                )

        except Exception as e:
            report.add_result(
                ValidationSeverity.ERROR,
                f"Unexpected validation error: {str(e)}",
                suggested_fix="Check node content structure and data types",
            )

        # Additional business logic validations
        self._validate_business_rules(node_type, node_content, report)

        self.logger.debug(
            "Node validation completed",
            node_id=node_id,
            is_valid=report.is_valid,
            error_count=len(report.errors),
            warning_count=len(report.warnings),
        )

        return report

    def _validate_basic_structure(
        self, node_content: Dict[str, Any], report: ValidationReport
    ):
        """Basic validation for unknown node types."""
        # Check for common suspicious patterns
        if len(node_content) == 0:
            report.add_result(
                ValidationSeverity.WARNING,
                "Node content is empty dictionary",
                suggested_fix="Add required fields for this node type",
            )

        # Check for potential configuration errors
        for key, value in node_content.items():
            if value is None:
                report.add_result(
                    ValidationSeverity.WARNING,
                    f"Field '{key}' has null value",
                    field_path=key,
                    suggested_fix=f"Provide a valid value for '{key}' or remove the field",
                )

    def _validate_business_rules(
        self,
        node_type: NodeType,
        node_content: Dict[str, Any],
        report: ValidationReport,
    ):
        """Additional business logic validations using CEL-based rules."""
        from app.services.cel_business_rules import validate_business_rules

        try:
            # Use CEL-based business rules engine
            cel_results = validate_business_rules(
                report.node_id, node_type, node_content
            )

            # Add CEL results to the report
            for cel_result in cel_results:
                report.results.append(cel_result)
                # Update overall validity if we have errors
                if cel_result.severity == ValidationSeverity.ERROR:
                    report.is_valid = False

            self.logger.debug(
                "CEL business rules validation completed",
                node_id=report.node_id,
                cel_results_count=len(cel_results),
            )

        except Exception as e:
            self.logger.error(
                "CEL business rules validation failed",
                node_id=report.node_id,
                error=str(e),
            )
            # Add warning about business rules failure, but don't fail overall validation
            report.add_result(
                ValidationSeverity.WARNING,
                f"Business rules validation failed: {str(e)}",
                suggested_fix="Check CEL business rules configuration",
            )

    def _get_validation_fix_suggestion(self, error: Dict[str, Any]) -> str:
        """Generate helpful fix suggestions based on validation errors."""
        error_type = error.get("type", "")
        field = error.get("loc", [])[-1] if error.get("loc") else ""

        suggestions = {
            "missing": f"Add required field '{field}'",
            "value_error": f"Check the value format for '{field}'",
            "type_error": f"Ensure '{field}' has the correct data type",
            "string_pattern_mismatch": f"Fix the format of '{field}' to match the required pattern",
            "ensure_list": f"Make sure '{field}' is a list/array",
            "ensure_dict": f"Make sure '{field}' is a dictionary/object",
            "min_items": f"Add more items to '{field}' list",
            "max_items": f"Reduce number of items in '{field}' list",
        }

        return suggestions.get(error_type, "Check the field value and format")


# Singleton instance for global use
node_input_validator = NodeInputValidator()


def validate_node_input(
    node_id: str, node_type: NodeType, node_content: Dict[str, Any]
) -> ValidationReport:
    """
    Convenience function for validating node inputs.

    Args:
        node_id: Unique identifier for the node
        node_type: Type of the node
        node_content: Node content dictionary to validate

    Returns:
        ValidationReport with validation results
    """
    return node_input_validator.validate_node(node_id, node_type, node_content)


def require_valid_input(
    node_id: str, node_type: NodeType, node_content: Dict[str, Any]
) -> ValidationReport:
    """
    Validate node input and raise exception if validation fails.

    Args:
        node_id: Unique identifier for the node
        node_type: Type of the node
        node_content: Node content dictionary to validate

    Returns:
        ValidationReport if validation passes

    Raises:
        ValueError: If validation fails with errors
    """
    report = validate_node_input(node_id, node_type, node_content)

    if not report.is_valid:
        error_messages = [r.message for r in report.errors]
        raise ValueError(
            f"Node {node_id} validation failed: {'; '.join(error_messages)}"
        )

    return report
