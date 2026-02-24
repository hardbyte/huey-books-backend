"""CEL (Common Expression Language) evaluator service for safe expression evaluation."""

import random
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Union

from cel import Context, evaluate
from structlog import get_logger

logger = get_logger()


def _cel_sum(values: List[Any]) -> Union[int, float]:
    """Sum numeric values in a list."""
    return sum(v for v in values if isinstance(v, (int, float)))


def _cel_avg(values: List[Any]) -> float:
    """Calculate average of numeric values in a list."""
    nums = [v for v in values if isinstance(v, (int, float))]
    return sum(nums) / len(nums) if nums else 0.0


def _cel_merge(dicts: List[Dict[str, Any]], strategy: str = "sum") -> Dict[str, Any]:
    """Merge a list of dictionaries using specified strategy.

    Strategies:
    - sum: Add numeric values with same keys (default)
    - max: Take maximum value for each key
    - last: Last value wins
    """
    result: Dict[str, Any] = {}
    for item in dicts:
        if not isinstance(item, dict):
            continue
        for key, value in item.items():
            if key not in result:
                result[key] = value
            elif strategy == "sum" and isinstance(value, (int, float)):
                existing = result[key]
                if isinstance(existing, (int, float)):
                    result[key] = existing + value
            elif strategy == "max" and isinstance(value, (int, float)):
                existing = result[key]
                if isinstance(existing, (int, float)):
                    result[key] = max(existing, value)
            elif strategy == "last":
                result[key] = value
    return result


def _cel_merge_sum(dicts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge dictionaries by summing numeric values."""
    return _cel_merge(dicts, "sum")


def _cel_merge_max(dicts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge dictionaries by taking max of numeric values."""
    return _cel_merge(dicts, "max")


def _cel_flatten(lists: List[Any]) -> List[Any]:
    """Flatten a list of lists into a single list."""
    result = []
    for item in lists:
        if isinstance(item, list):
            result.extend(item)
        else:
            result.append(item)
    return result


def _cel_max(values: List[Any]) -> Union[int, float, None]:
    """Find maximum value in a list of numbers."""
    nums = [v for v in values if isinstance(v, (int, float))]
    return max(nums) if nums else None


def _cel_min(values: List[Any]) -> Union[int, float, None]:
    """Find minimum value in a list of numbers."""
    nums = [v for v in values if isinstance(v, (int, float))]
    return min(nums) if nums else None


def _cel_merge_last(dicts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge dictionaries using last-value-wins strategy."""
    return _cel_merge(dicts, "last")


def _cel_count(values: List[Any]) -> int:
    """Count items in a list (alias for size)."""
    return len(values) if values else 0


def _cel_collect(items: List[Any]) -> List[Any]:
    """Collect and flatten items from a list (alias for flatten)."""
    return _cel_flatten(items)


def _cel_top_keys(d: Dict[str, Any], n: int = 5) -> List[str]:
    """Return the top N keys from a dict sorted by value descending.

    Useful for converting a hue_profile dict (hue→weight) into a ranked
    list of hue keys for the recommendation API.
    """
    if not isinstance(d, dict):
        return []
    numeric_items = [(k, v) for k, v in d.items() if isinstance(v, (int, float))]
    numeric_items.sort(key=lambda pair: pair[1], reverse=True)
    return [k for k, _ in numeric_items[:n]]


def _cel_days_since(iso_date_str: str) -> int:
    """Return number of days between an ISO datetime string and now.

    Returns -1 if the input is empty/None or unparseable.
    """
    if not iso_date_str or not isinstance(iso_date_str, str):
        return -1
    try:
        dt = datetime.fromisoformat(iso_date_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).days
    except (ValueError, TypeError):
        return -1


def _cel_random_choice(items: List[Any]) -> Any:
    """Pick a random item from a list."""
    if not items:
        return None
    return random.choice(items)


# Registry of custom functions available in CEL expressions
CUSTOM_CEL_FUNCTIONS: Dict[str, Callable] = {
    "sum": _cel_sum,
    "avg": _cel_avg,
    "max": _cel_max,
    "min": _cel_min,
    "count": _cel_count,
    "merge": _cel_merge_sum,
    "merge_sum": _cel_merge_sum,
    "merge_max": _cel_merge_max,
    "merge_last": _cel_merge_last,
    "flatten": _cel_flatten,
    "collect": _cel_collect,
    "top_keys": _cel_top_keys,
    "days_since": _cel_days_since,
    "random_choice": _cel_random_choice,
}


def create_cel_context(variables: Dict[str, Any]) -> Context:
    """Create a CEL context with custom aggregation functions.

    Args:
        variables: Dictionary of variables to make available in the context

    Returns:
        CEL Context with variables and custom functions registered
    """
    ctx = Context()

    # Add all variables
    for name, value in variables.items():
        ctx.add_variable(name, value)

    # Add custom aggregation functions
    for name, func in CUSTOM_CEL_FUNCTIONS.items():
        ctx.add_function(name, func)

    return ctx


def evaluate_cel_expression(
    expression: str,
    context: Dict[str, Any],
    include_aggregation_functions: bool = True,
) -> Any:
    """
    Safely evaluates a CEL expression against a given data context.

    Args:
        expression: CEL expression string to evaluate
        context: Dictionary containing variables for the expression
        include_aggregation_functions: If True, includes custom aggregation
            functions (sum, avg, merge, flatten). Default True.

    Returns:
        Result of the expression evaluation

    Raises:
        ValueError: If expression is invalid or evaluation fails
        TypeError: If context contains unsupported types

    Custom aggregation functions available when include_aggregation_functions=True:
        - sum(list): Sum numeric values in a list
        - avg(list): Calculate average of numeric values
        - max(list): Find maximum value in a list
        - min(list): Find minimum value in a list
        - count(list): Count items in a list (alias for size)
        - merge(list_of_dicts): Merge dictionaries by summing numeric values
        - merge_sum(list_of_dicts): Same as merge
        - merge_max(list_of_dicts): Merge taking max value for each key
        - merge_last(list_of_dicts): Merge with last value wins
        - flatten(list_of_lists): Flatten nested lists into single list
        - collect(list): Alias for flatten

    Example expressions:
        - sum(answers.map(x, x.score))
        - avg(ratings)
        - max(scores)
        - min(temp.quiz_results.map(x, x.time))
        - merge(selections.map(x, x.preferences))
        - flatten(items.map(x, x.tags))
        - top_keys(user.hue_profile, 5)
    """
    try:
        if include_aggregation_functions:
            cel_context = create_cel_context(context)
            result = evaluate(expression, cel_context)
        else:
            result = evaluate(expression, context)

        logger.debug(
            "CEL expression evaluated successfully",
            expression=expression,
            result=result,
            context_keys=list(context.keys()),
        )

        return result

    except Exception as e:
        logger.error(
            "CEL expression evaluation failed",
            expression=expression,
            error=str(e),
            context_keys=list(context.keys()),
        )
        raise ValueError(f"Failed to evaluate expression '{expression}': {str(e)}")


def validate_cel_expression(expression: str) -> bool:
    """
    Validate that a CEL expression is syntactically correct.

    Args:
        expression: CEL expression string to validate

    Returns:
        True if expression is valid, False otherwise
    """
    try:
        # Try to evaluate with a sample context to check syntax
        sample_context = {
            "node_type": "test",
            "content": {
                "url": "https://example.com",
                "messages": [],
                "test_field": "test_value",
            },
        }
        evaluate(expression, sample_context)
        return True
    except Exception:
        return False


def get_supported_operators() -> Dict[str, str]:
    """
    Get list of supported operators and functions in CEL.

    Returns:
        Dictionary mapping operator/function names to descriptions
    """
    return {
        "+": "Addition",
        "-": "Subtraction",
        "*": "Multiplication",
        "/": "Division",
        "%": "Modulo",
        "==": "Equality",
        "!=": "Inequality",
        "<": "Less than",
        "<=": "Less than or equal",
        ">": "Greater than",
        ">=": "Greater than or equal",
        "&&": "Logical AND",
        "||": "Logical OR",
        "!": "Logical NOT",
        "size": "Get size of string/list/map",
        "int": "Convert to integer",
        "double": "Convert to double",
        "string": "Convert to string",
        "type": "Get type of value",
        "has": "Check if field exists",
        "in": "Check membership in list/map",
    }
