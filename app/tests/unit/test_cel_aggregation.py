"""
Unit tests for CEL (Common Expression Language) aggregation functions.

Tests the custom CEL functions used for aggregating data in chatflow expressions:
- sum, avg, max, min, count
- merge, merge_sum, merge_max, merge_last
- flatten, collect

These tests validate the CEL evaluator with custom aggregation functions
that are used in aggregate actions within chatflow nodes.
"""

import pytest

from app.services.cel_evaluator import (
    CUSTOM_CEL_FUNCTIONS,
    _cel_avg,
    _cel_collect,
    _cel_count,
    _cel_flatten,
    _cel_max,
    _cel_merge,
    _cel_merge_last,
    _cel_merge_max,
    _cel_merge_sum,
    _cel_min,
    _cel_sum,
    create_cel_context,
    evaluate_cel_expression,
)


class TestCelSumFunction:
    """Tests for the sum() CEL function."""

    def test_sum_integers(self):
        """Sum a list of integers."""
        result = _cel_sum([1, 2, 3, 4, 5])
        assert result == 15

    def test_sum_floats(self):
        """Sum a list of floats."""
        result = _cel_sum([1.5, 2.5, 3.0])
        assert result == 7.0

    def test_sum_mixed_numeric(self):
        """Sum mixed integers and floats."""
        result = _cel_sum([1, 2.5, 3, 4.5])
        assert result == 11.0

    def test_sum_with_non_numeric_ignored(self):
        """Non-numeric values are ignored in sum."""
        result = _cel_sum([1, "text", 2, None, 3, {"key": "value"}])
        assert result == 6

    def test_sum_empty_list(self):
        """Sum of empty list returns 0."""
        result = _cel_sum([])
        assert result == 0

    def test_sum_via_cel_expression(self):
        """Test sum function via CEL expression."""
        context = {"scores": [10, 20, 30, 40]}
        result = evaluate_cel_expression("sum(scores)", context)
        assert result == 100

    def test_sum_with_map_extraction(self):
        """Test sum with map to extract field."""
        context = {
            "items": [
                {"name": "A", "value": 10},
                {"name": "B", "value": 20},
                {"name": "C", "value": 30},
            ]
        }
        result = evaluate_cel_expression("sum(items.map(x, x.value))", context)
        assert result == 60


class TestCelAvgFunction:
    """Tests for the avg() CEL function."""

    def test_avg_integers(self):
        """Average of integers."""
        result = _cel_avg([10, 20, 30])
        assert result == 20.0

    def test_avg_floats(self):
        """Average of floats."""
        result = _cel_avg([1.0, 2.0, 3.0, 4.0])
        assert result == 2.5

    def test_avg_with_non_numeric_ignored(self):
        """Non-numeric values are ignored in average."""
        result = _cel_avg([10, "text", 20, None, 30])
        assert result == 20.0

    def test_avg_empty_list(self):
        """Average of empty list returns 0."""
        result = _cel_avg([])
        assert result == 0.0

    def test_avg_via_cel_expression(self):
        """Test avg function via CEL expression."""
        context = {"ratings": [4.0, 5.0, 3.0, 4.0, 4.0]}
        result = evaluate_cel_expression("avg(ratings)", context)
        assert result == 4.0

    def test_avg_with_map_extraction(self):
        """Test avg with map to extract field."""
        context = {
            "reviews": [
                {"score": 3},
                {"score": 4},
                {"score": 5},
            ]
        }
        result = evaluate_cel_expression("avg(reviews.map(x, x.score))", context)
        assert result == 4.0


class TestCelMaxFunction:
    """Tests for the max() CEL function."""

    def test_max_integers(self):
        """Find max in list of integers."""
        result = _cel_max([5, 2, 8, 1, 9])
        assert result == 9

    def test_max_floats(self):
        """Find max in list of floats."""
        result = _cel_max([1.5, 3.7, 2.2, 3.6])
        assert result == 3.7

    def test_max_with_non_numeric_ignored(self):
        """Non-numeric values are ignored in max."""
        result = _cel_max([5, "text", 8, None, 3])
        assert result == 8

    def test_max_empty_list(self):
        """Max of empty list returns None."""
        result = _cel_max([])
        assert result is None

    def test_max_via_cel_expression(self):
        """Test max function via CEL expression."""
        context = {"scores": [75, 82, 90, 68, 88]}
        result = evaluate_cel_expression("max(scores)", context)
        assert result == 90

    def test_max_with_map_extraction(self):
        """Test max with map to extract field."""
        context = {
            "results": [
                {"score": 75},
                {"score": 92},
                {"score": 88},
            ]
        }
        result = evaluate_cel_expression("max(results.map(x, x.score))", context)
        assert result == 92


class TestCelMinFunction:
    """Tests for the min() CEL function."""

    def test_min_integers(self):
        """Find min in list of integers."""
        result = _cel_min([5, 2, 8, 1, 9])
        assert result == 1

    def test_min_floats(self):
        """Find min in list of floats."""
        result = _cel_min([1.5, 3.7, 2.2, 0.8])
        assert result == 0.8

    def test_min_with_non_numeric_ignored(self):
        """Non-numeric values are ignored in min."""
        result = _cel_min([5, "text", 8, None, 3])
        assert result == 3

    def test_min_empty_list(self):
        """Min of empty list returns None."""
        result = _cel_min([])
        assert result is None

    def test_min_via_cel_expression(self):
        """Test min function via CEL expression."""
        context = {"times": [45, 32, 50, 28, 40]}
        result = evaluate_cel_expression("min(times)", context)
        assert result == 28

    def test_min_with_map_extraction(self):
        """Test min with map to extract field."""
        context = {
            "attempts": [
                {"time_seconds": 45},
                {"time_seconds": 32},
                {"time_seconds": 50},
            ]
        }
        result = evaluate_cel_expression(
            "min(attempts.map(x, x.time_seconds))", context
        )
        assert result == 32


class TestCelCountFunction:
    """Tests for the count() CEL function."""

    def test_count_items(self):
        """Count items in a list."""
        result = _cel_count([1, 2, 3, 4, 5])
        assert result == 5

    def test_count_mixed_types(self):
        """Count items of mixed types."""
        result = _cel_count([1, "text", None, {"key": "value"}, [1, 2]])
        assert result == 5

    def test_count_empty_list(self):
        """Count of empty list returns 0."""
        result = _cel_count([])
        assert result == 0

    def test_count_via_cel_expression(self):
        """Test count function via CEL expression."""
        context = {"items": ["a", "b", "c", "d"]}
        result = evaluate_cel_expression("count(items)", context)
        assert result == 4

    def test_count_with_filter(self):
        """Test count after filtering."""
        context = {"numbers": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]}
        # Count numbers greater than 5
        result = evaluate_cel_expression("count(numbers.filter(x, x > 5))", context)
        assert result == 5


class TestCelMergeFunction:
    """Tests for the merge() CEL function family."""

    def test_merge_sum_numeric_values(self):
        """Merge dictionaries by summing numeric values."""
        dicts = [
            {"a": 1, "b": 2},
            {"a": 3, "c": 4},
            {"b": 5, "c": 6},
        ]
        result = _cel_merge_sum(dicts)
        assert result == {"a": 4, "b": 7, "c": 10}

    def test_merge_sum_with_floats(self):
        """Merge dictionaries with float values."""
        dicts = [
            {"x": 0.5, "y": 0.3},
            {"x": 0.3, "y": 0.4, "z": 0.2},
        ]
        result = _cel_merge_sum(dicts)
        assert result["x"] == pytest.approx(0.8)
        assert result["y"] == pytest.approx(0.7)
        assert result["z"] == pytest.approx(0.2)

    def test_merge_max_values(self):
        """Merge dictionaries by taking max values."""
        dicts = [
            {"skill_a": 3, "skill_b": 5},
            {"skill_a": 4, "skill_b": 2, "skill_c": 5},
            {"skill_a": 2, "skill_c": 3},
        ]
        result = _cel_merge_max(dicts)
        assert result == {"skill_a": 4, "skill_b": 5, "skill_c": 5}

    def test_merge_last_values(self):
        """Merge dictionaries with last-value-wins strategy."""
        dicts = [
            {"theme": "light", "size": "small"},
            {"theme": "dark"},
            {"size": "large"},
        ]
        result = _cel_merge_last(dicts)
        assert result == {"theme": "dark", "size": "large"}

    def test_merge_with_non_dict_ignored(self):
        """Non-dict items are ignored in merge."""
        dicts = [{"a": 1}, "not a dict", {"b": 2}, None, {"a": 3}]
        result = _cel_merge_sum(dicts)
        assert result == {"a": 4, "b": 2}

    def test_merge_empty_list(self):
        """Merge of empty list returns empty dict."""
        result = _cel_merge_sum([])
        assert result == {}

    def test_merge_via_cel_expression(self):
        """Test merge function via CEL expression."""
        context = {
            "preferences": [
                {"adventure": 0.8, "mystery": 0.2},
                {"adventure": 0.3, "romance": 0.5},
            ]
        }
        result = evaluate_cel_expression("merge(preferences)", context)
        assert result["adventure"] == pytest.approx(1.1)
        assert result["mystery"] == pytest.approx(0.2)
        assert result["romance"] == pytest.approx(0.5)

    def test_merge_max_via_cel_expression(self):
        """Test merge_max function via CEL expression."""
        context = {
            "skill_tests": [
                {"reading": 3, "math": 5},
                {"reading": 4, "math": 3},
            ]
        }
        result = evaluate_cel_expression("merge_max(skill_tests)", context)
        assert result == {"reading": 4, "math": 5}

    def test_merge_last_via_cel_expression(self):
        """Test merge_last function via CEL expression."""
        context = {
            "config_updates": [
                {"theme": "light", "notifications": True},
                {"theme": "dark"},
            ]
        }
        result = evaluate_cel_expression("merge_last(config_updates)", context)
        assert result["theme"] == "dark"
        assert result["notifications"] is True

    def test_merge_with_map_extraction(self):
        """Test merge with map to extract field."""
        context = {
            "answers": [
                {"text": "A", "weights": {"trait_a": 0.8, "trait_b": 0.2}},
                {"text": "B", "weights": {"trait_a": 0.3, "trait_c": 0.7}},
            ]
        }
        result = evaluate_cel_expression("merge(answers.map(x, x.weights))", context)
        assert result["trait_a"] == pytest.approx(1.1)
        assert result["trait_b"] == pytest.approx(0.2)
        assert result["trait_c"] == pytest.approx(0.7)


class TestCelFlattenFunction:
    """Tests for the flatten() and collect() CEL functions."""

    def test_flatten_nested_lists(self):
        """Flatten a list of lists."""
        lists = [[1, 2], [3, 4], [5, 6]]
        result = _cel_flatten(lists)
        assert result == [1, 2, 3, 4, 5, 6]

    def test_flatten_mixed_items(self):
        """Flatten with mixed lists and single items."""
        items = [[1, 2], 3, [4, 5], 6]
        result = _cel_flatten(items)
        assert result == [1, 2, 3, 4, 5, 6]

    def test_flatten_single_items(self):
        """Flatten list of single items (no change)."""
        items = [1, 2, 3, 4]
        result = _cel_flatten(items)
        assert result == [1, 2, 3, 4]

    def test_flatten_empty_list(self):
        """Flatten empty list returns empty list."""
        result = _cel_flatten([])
        assert result == []

    def test_flatten_with_empty_sublists(self):
        """Flatten handles empty sublists."""
        lists = [[1, 2], [], [3], []]
        result = _cel_flatten(lists)
        assert result == [1, 2, 3]

    def test_collect_is_alias_for_flatten(self):
        """collect() is an alias for flatten()."""
        items = [[1, 2], [3, 4]]
        assert _cel_collect(items) == _cel_flatten(items)

    def test_flatten_via_cel_expression(self):
        """Test flatten function via CEL expression."""
        context = {
            "tag_groups": [
                ["adventure", "fantasy"],
                ["mystery", "thriller"],
                ["humor"],
            ]
        }
        result = evaluate_cel_expression("flatten(tag_groups)", context)
        assert result == ["adventure", "fantasy", "mystery", "thriller", "humor"]

    def test_flatten_with_map_extraction(self):
        """Test flatten with map to extract field."""
        context = {
            "selections": [
                {"title": "Book 1", "tags": ["adventure", "fantasy"]},
                {"title": "Book 2", "tags": ["mystery"]},
            ]
        }
        result = evaluate_cel_expression("flatten(selections.map(x, x.tags))", context)
        assert result == ["adventure", "fantasy", "mystery"]


class TestCelContextCreation:
    """Tests for CEL context creation with custom functions."""

    def test_context_includes_all_custom_functions(self):
        """Verify all custom functions are registered."""
        expected_functions = {
            "sum",
            "avg",
            "max",
            "min",
            "count",
            "merge",
            "merge_sum",
            "merge_max",
            "merge_last",
            "flatten",
            "collect",
            "top_keys",
            "days_since",
            "random_choice",
        }
        assert set(CUSTOM_CEL_FUNCTIONS.keys()) == expected_functions

    def test_context_includes_variables(self):
        """Context includes provided variables."""
        variables = {"user": {"name": "Test"}, "scores": [1, 2, 3]}
        ctx = create_cel_context(variables)
        # Variables should be accessible in expressions
        result = evaluate_cel_expression("user.name", variables)
        assert result == "Test"

    def test_complex_expression_with_multiple_functions(self):
        """Test complex expression using multiple custom functions."""
        context = {
            "quiz_results": [
                {"score": 80, "time": 45},
                {"score": 90, "time": 30},
                {"score": 85, "time": 50},
            ]
        }
        # Calculate average score
        avg_score = evaluate_cel_expression(
            "avg(quiz_results.map(x, x.score))", context
        )
        assert avg_score == 85.0

        # Find best time
        best_time = evaluate_cel_expression("min(quiz_results.map(x, x.time))", context)
        assert best_time == 30


class TestCelExpressionEvaluation:
    """Tests for CEL expression evaluation with aggregation functions."""

    def test_expression_with_nested_state(self):
        """Test expression with nested state variables."""
        context = {
            "temp": {
                "answers": [
                    {"score": 10},
                    {"score": 20},
                    {"score": 30},
                ]
            }
        }
        result = evaluate_cel_expression("sum(temp.answers.map(x, x.score))", context)
        assert result == 60

    def test_expression_with_deeply_nested_state(self):
        """Test expression with deeply nested state variables."""
        context = {"data": {"results": {"scores": [5, 10, 15]}}}
        result = evaluate_cel_expression("sum(data.results.scores)", context)
        assert result == 30

    def test_invalid_expression_raises_error(self):
        """Invalid expression raises ValueError."""
        context = {"items": [1, 2, 3]}
        with pytest.raises(ValueError) as exc_info:
            evaluate_cel_expression("invalid_function(items)", context)
        assert "Failed to evaluate expression" in str(exc_info.value)

    def test_expression_with_missing_variable(self):
        """Expression with missing variable raises error."""
        context = {"items": [1, 2, 3]}
        with pytest.raises(ValueError) as exc_info:
            evaluate_cel_expression("sum(nonexistent)", context)
        assert "Failed to evaluate expression" in str(exc_info.value)


class TestCelAggregationChatflowScenarios:
    """Real-world chatflow scenarios using CEL aggregation."""

    def test_preference_quiz_hue_aggregation(self):
        """Aggregate hue maps from preference quiz answers."""
        context = {
            "temp": {
                "preference_answers": [
                    {
                        "question_id": 1,
                        "hue_map": {
                            "hue01_dark_suspense": 1.0,
                            "hue02_beautiful_whimsical": 0.2,
                        },
                    },
                    {
                        "question_id": 2,
                        "hue_map": {
                            "hue01_dark_suspense": 0.3,
                            "hue02_beautiful_whimsical": 0.8,
                            "hue03_dark_beautiful": 0.5,
                        },
                    },
                    {
                        "question_id": 3,
                        "hue_map": {
                            "hue02_beautiful_whimsical": 0.5,
                            "hue03_dark_beautiful": 0.7,
                        },
                    },
                ]
            }
        }

        result = evaluate_cel_expression(
            "merge(temp.preference_answers.map(x, x.hue_map))", context
        )

        assert result["hue01_dark_suspense"] == pytest.approx(1.3)
        assert result["hue02_beautiful_whimsical"] == pytest.approx(1.5)
        assert result["hue03_dark_beautiful"] == pytest.approx(1.2)

    def test_quiz_scoring_with_weighted_questions(self):
        """Calculate quiz scores using sum of scores."""
        context = {
            "temp": {
                "quiz_answers": [
                    {"question_id": 1, "score": 5, "max_score": 5},
                    {"question_id": 2, "score": 8, "max_score": 10},
                    {"question_id": 3, "score": 7, "max_score": 10},
                ]
            }
        }

        total_score = evaluate_cel_expression(
            "sum(temp.quiz_answers.map(x, x.score))", context
        )
        max_possible = evaluate_cel_expression(
            "sum(temp.quiz_answers.map(x, x.max_score))", context
        )
        question_count = evaluate_cel_expression("count(temp.quiz_answers)", context)

        assert total_score == 20
        assert max_possible == 25
        assert question_count == 3

    def test_personality_trait_aggregation(self):
        """Aggregate personality traits from multiple questions."""
        context = {
            "temp": {
                "personality_answers": [
                    {"traits": {"introvert": 0.8, "analytical": 0.6}},
                    {"traits": {"introvert": 0.3, "creative": 0.9}},
                    {"traits": {"analytical": 0.5, "creative": 0.4}},
                ]
            }
        }

        result = evaluate_cel_expression(
            "merge(temp.personality_answers.map(x, x.traits))", context
        )

        assert result["introvert"] == pytest.approx(1.1)
        assert result["analytical"] == pytest.approx(1.1)
        assert result["creative"] == pytest.approx(1.3)

    def test_book_tag_collection(self):
        """Collect all tags from book selections."""
        context = {
            "temp": {
                "book_selections": [
                    {"title": "Book 1", "tags": ["adventure", "fantasy"]},
                    {"title": "Book 2", "tags": ["mystery", "thriller"]},
                    {"title": "Book 3", "tags": ["adventure", "humor"]},
                ]
            }
        }

        result = evaluate_cel_expression(
            "flatten(temp.book_selections.map(x, x.tags))", context
        )

        assert len(result) == 6
        assert result.count("adventure") == 2
        assert "mystery" in result
        assert "humor" in result

    def test_survey_response_statistics(self):
        """Calculate multiple statistics from survey responses."""
        context = {
            "temp": {
                "survey_responses": [
                    {"rating": 4},
                    {"rating": 5},
                    {"rating": 3},
                    {"rating": 5},
                    {"rating": 4},
                ]
            }
        }

        avg_rating = evaluate_cel_expression(
            "avg(temp.survey_responses.map(x, x.rating))", context
        )
        max_rating = evaluate_cel_expression(
            "max(temp.survey_responses.map(x, x.rating))", context
        )
        min_rating = evaluate_cel_expression(
            "min(temp.survey_responses.map(x, x.rating))", context
        )
        response_count = evaluate_cel_expression(
            "count(temp.survey_responses)", context
        )

        assert avg_rating == pytest.approx(4.2)
        assert max_rating == 5
        assert min_rating == 3
        assert response_count == 5

    def test_skill_assessment_peak_scores(self):
        """Find peak skill scores across multiple assessments."""
        context = {
            "temp": {
                "skill_assessments": [
                    {"reading": 3, "math": 5, "science": 4},
                    {"reading": 4, "math": 3, "art": 5},
                    {"reading": 2, "science": 5, "art": 3},
                ]
            }
        }

        result = evaluate_cel_expression("merge_max(temp.skill_assessments)", context)

        assert result["reading"] == 4
        assert result["math"] == 5
        assert result["science"] == 5
        assert result["art"] == 5


class TestTopKeys:
    """Tests for the top_keys CEL function used in hue profile ranking."""

    def test_basic_ranking(self):
        """Return keys ordered by descending value."""
        context = {
            "profile": {
                "hue01_dark_suspense": 1.3,
                "hue02_beautiful_whimsical": 1.5,
                "hue03_dark_beautiful": 1.2,
            }
        }
        result = evaluate_cel_expression("top_keys(profile, 3)", context)
        assert result == [
            "hue02_beautiful_whimsical",
            "hue01_dark_suspense",
            "hue03_dark_beautiful",
        ]

    def test_n_limits_results(self):
        """Only the top N keys are returned."""
        context = {
            "scores": {"a": 10, "b": 30, "c": 20, "d": 5, "e": 25}
        }
        result = evaluate_cel_expression("top_keys(scores, 2)", context)
        assert result == ["b", "e"]

    def test_n_larger_than_dict(self):
        """Requesting more keys than exist returns all keys."""
        context = {"scores": {"x": 1, "y": 2}}
        result = evaluate_cel_expression("top_keys(scores, 10)", context)
        assert result == ["y", "x"]

    def test_non_numeric_values_ignored(self):
        """Keys with non-numeric values are excluded from ranking."""
        context = {
            "mixed": {"a": 5, "b": "high", "c": 10, "d": None, "e": 3}
        }
        result = evaluate_cel_expression("top_keys(mixed, 5)", context)
        assert result == ["c", "a", "e"]

    def test_empty_dict(self):
        """Empty dict returns empty list."""
        context = {"empty": {}}
        result = evaluate_cel_expression("top_keys(empty, 3)", context)
        assert result == []

    def test_non_dict_returns_empty(self):
        """Non-dict input returns empty list."""
        from app.services.cel_evaluator import CUSTOM_CEL_FUNCTIONS

        assert CUSTOM_CEL_FUNCTIONS["top_keys"]("not a dict") == []
        assert CUSTOM_CEL_FUNCTIONS["top_keys"]([1, 2, 3]) == []

    def test_huey_hue_profile_pipeline(self):
        """End-to-end: merge preference hue_maps then rank with top_keys."""
        context = {
            "temp": {
                "preference_answers": [
                    {"hue_map": {"dark": 1.0, "whimsical": 0.2, "funny": 0.5}},
                    {"hue_map": {"dark": 0.3, "whimsical": 0.8, "action": 0.9}},
                    {"hue_map": {"whimsical": 0.5, "funny": 0.7}},
                ]
            }
        }
        merged = evaluate_cel_expression(
            "merge(temp.preference_answers.map(x, x.hue_map))", context
        )
        # whimsical=1.5, dark=1.3, funny=1.2, action=0.9
        result = evaluate_cel_expression("top_keys(profile, 2)", {"profile": merged})
        assert result == ["whimsical", "dark"]
