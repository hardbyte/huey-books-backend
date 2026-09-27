"""
Unit tests for chatbot integration helpers and request/response models.

Tests the pure helper functions and Pydantic models in
app/api/chatbot_integrations.py without database dependencies.
"""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.api.chatbot_integrations import (
    ChatbotRecommendationRequest,
    ChatbotRecommendationResponse,
    ReadingAssessmentRequest,
    ReadingAssessmentResponse,
    UserProfileResponse,
    _analyze_performance,
    _analyze_quiz_responses,
    _analyze_reading_sample,
    _determine_reading_level,
    _get_level_description,
    _get_level_recommendations,
    _get_next_steps,
)


class TestAnalyzeQuizResponses:
    """Test _analyze_quiz_responses helper."""

    def test_perfect_score(self):
        assert _analyze_quiz_responses({"correct": 10, "total": 10}) == 1.0

    def test_zero_score(self):
        assert _analyze_quiz_responses({"correct": 0, "total": 10}) == 0.0

    def test_partial_score(self):
        result = _analyze_quiz_responses({"correct": 7, "total": 10})
        assert abs(result - 0.7) < 1e-9

    def test_zero_total_returns_zero(self):
        assert _analyze_quiz_responses({"correct": 5, "total": 0}) == 0.0

    def test_missing_keys_default_to_zero(self):
        assert _analyze_quiz_responses({}) == 0.0

    def test_single_question(self):
        assert _analyze_quiz_responses({"correct": 1, "total": 1}) == 1.0


class TestAnalyzeReadingSample:
    """Test _analyze_reading_sample helper."""

    def test_short_sentences_low_score(self):
        sample = "I run. I jump. I play."
        result = _analyze_reading_sample(sample)
        assert result == 0.3

    def test_medium_sentences_mid_score(self):
        sample = "The quick brown fox jumps over the lazy dog near the river."
        result = _analyze_reading_sample(sample)
        assert result == 0.6

    def test_long_sentences_high_score(self):
        sample = (
            "The extraordinarily complex and multifaceted nature of modern "
            "literature demands that readers engage with texts on multiple "
            "levels of interpretation and analysis."
        )
        result = _analyze_reading_sample(sample)
        assert result == 0.9

    def test_no_punctuation_returns_default(self):
        sample = "This text has no sentence-ending punctuation"
        result = _analyze_reading_sample(sample)
        assert result == 0.5

    def test_empty_string(self):
        result = _analyze_reading_sample("")
        assert result == 0.5

    def test_exclamation_and_question_marks_count(self):
        sample = "Is this a sentence? Yes it is! And another one too!"
        result = _analyze_reading_sample(sample)
        # 12 words / 3 sentences = 4 avg -> short -> 0.3
        assert result == 0.3


class TestDetermineReadingLevel:
    """Test _determine_reading_level helper."""

    def test_early_reader_range(self):
        assert _determine_reading_level(0.0, None, None) == "early_reader"
        assert _determine_reading_level(0.15, None, None) == "early_reader"
        assert _determine_reading_level(0.29, None, None) == "early_reader"

    def test_developing_reader_range(self):
        assert _determine_reading_level(0.3, None, None) == "developing_reader"
        assert _determine_reading_level(0.45, None, None) == "developing_reader"

    def test_intermediate_range(self):
        assert _determine_reading_level(0.5, None, None) == "intermediate"
        assert _determine_reading_level(0.65, None, None) == "intermediate"

    def test_advanced_range(self):
        assert _determine_reading_level(0.7, None, None) == "advanced"
        assert _determine_reading_level(0.8, None, None) == "advanced"

    def test_expert_range(self):
        assert _determine_reading_level(0.85, None, None) == "expert"
        assert _determine_reading_level(0.95, None, None) == "expert"

    def test_score_at_exact_boundary(self):
        # 1.0 falls outside all ranges (max_score exclusive), defaults to intermediate
        assert _determine_reading_level(1.0, None, None) == "intermediate"

    def test_negative_score_defaults(self):
        # Negative score is below all ranges
        assert _determine_reading_level(-0.5, None, None) == "intermediate"

    def test_age_and_current_level_accepted(self):
        # These parameters are accepted but don't currently affect output
        result = _determine_reading_level(0.6, age=10, current_level="early_reader")
        assert result == "intermediate"


class TestGetLevelDescription:
    """Test _get_level_description helper."""

    def test_all_known_levels_have_descriptions(self):
        levels = [
            "early_reader",
            "developing_reader",
            "intermediate",
            "advanced",
            "expert",
        ]
        for level in levels:
            desc = _get_level_description(level)
            assert isinstance(desc, str)
            assert len(desc) > 0

    def test_unknown_level_returns_default(self):
        desc = _get_level_description("nonexistent_level")
        assert "developing your reading skills" in desc

    def test_descriptions_are_unique(self):
        levels = [
            "early_reader",
            "developing_reader",
            "intermediate",
            "advanced",
            "expert",
        ]
        descriptions = [_get_level_description(level) for level in levels]
        assert len(set(descriptions)) == len(descriptions)


class TestGetLevelRecommendations:
    """Test _get_level_recommendations helper."""

    def test_all_known_levels_return_lists(self):
        levels = [
            "early_reader",
            "developing_reader",
            "intermediate",
            "advanced",
            "expert",
        ]
        for level in levels:
            recs = _get_level_recommendations(level)
            assert isinstance(recs, list)
            assert len(recs) > 0
            assert all(isinstance(r, str) for r in recs)

    def test_unknown_level_returns_default(self):
        recs = _get_level_recommendations("nonexistent")
        assert len(recs) == 1
        assert "Keep reading" in recs[0]


class TestAnalyzePerformance:
    """Test _analyze_performance helper."""

    def test_returns_tuple_of_lists(self):
        strengths, improvements = _analyze_performance({})
        assert isinstance(strengths, list)
        assert isinstance(improvements, list)

    def test_returns_non_empty_lists(self):
        strengths, improvements = _analyze_performance({"score": 0.8})
        assert len(strengths) > 0
        assert len(improvements) > 0


class TestGetNextSteps:
    """Test _get_next_steps helper."""

    def test_high_score_base_steps(self):
        steps = _get_next_steps("intermediate", 0.8)
        assert len(steps) == 3
        assert any("intermediate" in step for step in steps)

    def test_low_score_adds_extra_steps(self):
        steps = _get_next_steps("early_reader", 0.3)
        assert len(steps) == 5
        assert any("fluency" in step for step in steps)
        assert any("questions" in step for step in steps)

    def test_boundary_score_no_extras(self):
        steps = _get_next_steps("developing_reader", 0.6)
        assert len(steps) == 3


class TestChatbotRecommendationRequest:
    """Test ChatbotRecommendationRequest Pydantic model."""

    def test_minimal_valid_request(self):
        req = ChatbotRecommendationRequest(user_id=uuid4())
        assert req.limit == 5
        assert req.preferences == {}
        assert req.exclude_isbns == []
        assert req.hues == []
        assert req.genres == []
        assert req.reading_level is None
        assert req.age is None

    def test_full_request(self):
        uid = uuid4()
        req = ChatbotRecommendationRequest(
            user_id=uid,
            preferences={"genre": "fantasy"},
            limit=10,
            exclude_isbns=["978-0-13-468599-1"],
            reading_level="intermediate",
            age=12,
            genres=["fantasy", "adventure"],
            hues=["red", "blue"],
        )
        assert req.user_id == uid
        assert req.limit == 10
        assert req.reading_level == "intermediate"

    def test_limit_minimum(self):
        req = ChatbotRecommendationRequest(user_id=uuid4(), limit=1)
        assert req.limit == 1

    def test_limit_maximum(self):
        req = ChatbotRecommendationRequest(user_id=uuid4(), limit=20)
        assert req.limit == 20

    def test_limit_below_minimum_rejected(self):
        with pytest.raises(ValidationError):
            ChatbotRecommendationRequest(user_id=uuid4(), limit=0)

    def test_limit_above_maximum_rejected(self):
        with pytest.raises(ValidationError):
            ChatbotRecommendationRequest(user_id=uuid4(), limit=21)

    def test_missing_user_id_rejected(self):
        with pytest.raises(ValidationError):
            ChatbotRecommendationRequest()


class TestChatbotRecommendationResponse:
    """Test ChatbotRecommendationResponse Pydantic model."""

    def test_empty_recommendations(self):
        resp = ChatbotRecommendationResponse(
            recommendations=[],
            count=0,
            filters_applied={},
        )
        assert resp.count == 0
        assert resp.fallback_used is False
        assert resp.user_reading_level is None

    def test_with_recommendations(self):
        resp = ChatbotRecommendationResponse(
            recommendations=[{"id": "1", "title": "Test Book", "author": "Author"}],
            count=1,
            user_reading_level="intermediate",
            filters_applied={"age": 10},
            fallback_used=False,
        )
        assert resp.count == 1
        assert resp.recommendations[0]["title"] == "Test Book"

    def test_fallback_response(self):
        resp = ChatbotRecommendationResponse(
            recommendations=[],
            count=0,
            filters_applied={},
            fallback_used=True,
        )
        assert resp.fallback_used is True


class TestReadingAssessmentRequest:
    """Test ReadingAssessmentRequest Pydantic model."""

    def test_minimal_request(self):
        req = ReadingAssessmentRequest(
            user_id=uuid4(),
            assessment_data={"type": "quiz"},
        )
        assert req.quiz_answers is None
        assert req.reading_sample is None
        assert req.comprehension_score is None
        assert req.vocabulary_score is None
        assert req.current_reading_level is None
        assert req.age is None

    def test_full_request(self):
        req = ReadingAssessmentRequest(
            user_id=uuid4(),
            assessment_data={"type": "comprehensive"},
            quiz_answers={"correct": 8, "total": 10},
            reading_sample="The cat sat on the mat.",
            comprehension_score=0.85,
            vocabulary_score=0.7,
            current_reading_level="intermediate",
            age=12,
        )
        assert req.comprehension_score == 0.85
        assert req.vocabulary_score == 0.7
        assert req.age == 12


class TestReadingAssessmentResponse:
    """Test ReadingAssessmentResponse Pydantic model."""

    def test_valid_response(self):
        resp = ReadingAssessmentResponse(
            reading_level="intermediate",
            confidence=0.85,
            level_description="You're a confident reader!",
            assessment_summary={"overall_score": 0.7},
        )
        assert resp.reading_level == "intermediate"
        assert resp.confidence == 0.85
        assert resp.recommendations == []
        assert resp.next_steps == []

    def test_confidence_bounds_minimum(self):
        resp = ReadingAssessmentResponse(
            reading_level="early_reader",
            confidence=0.0,
            level_description="Starting out",
            assessment_summary={},
        )
        assert resp.confidence == 0.0

    def test_confidence_bounds_maximum(self):
        resp = ReadingAssessmentResponse(
            reading_level="expert",
            confidence=1.0,
            level_description="Expert",
            assessment_summary={},
        )
        assert resp.confidence == 1.0

    def test_confidence_below_minimum_rejected(self):
        with pytest.raises(ValidationError):
            ReadingAssessmentResponse(
                reading_level="early_reader",
                confidence=-0.1,
                level_description="Invalid",
                assessment_summary={},
            )

    def test_confidence_above_maximum_rejected(self):
        with pytest.raises(ValidationError):
            ReadingAssessmentResponse(
                reading_level="expert",
                confidence=1.1,
                level_description="Invalid",
                assessment_summary={},
            )


class TestUserProfileResponse:
    """Test UserProfileResponse Pydantic model."""

    def test_minimal_profile(self):
        uid = uuid4()
        resp = UserProfileResponse(user_id=uid)
        assert resp.user_id == uid
        assert resp.reading_level is None
        assert resp.interests == []
        assert resp.reading_history == []
        assert resp.school_name is None
        assert resp.school_id is None
        assert resp.class_group is None
        assert resp.books_read_count == 0
        assert resp.average_reading_time is None
        assert resp.favorite_genres == []

    def test_full_profile(self):
        uid = uuid4()
        school_id = uuid4()
        resp = UserProfileResponse(
            user_id=uid,
            reading_level="advanced",
            interests=["fantasy", "sci-fi"],
            reading_history=[{"title": "Book 1", "isbn": "123"}],
            school_name="Test School",
            school_id=school_id,
            class_group="Year 5",
            books_read_count=42,
            average_reading_time=25.5,
            favorite_genres=["fantasy", "adventure"],
        )
        assert resp.books_read_count == 42
        assert resp.school_name == "Test School"
        assert len(resp.reading_history) == 1
