"""
Tests for EmailNotificationService - Reliable email delivery using Event Outbox Pattern.

These tests validate:
1. Email queuing via Event Outbox
2. Email type to priority mapping
3. Email delivery processing
4. Error handling scenarios
5. Convenience methods
6. Backward compatibility functions
"""

from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest

from app.schemas.sendgrid import SendGridEmailData
from app.services.email_notification import (
    EmailNotificationError,
    EmailNotificationService,
    EmailType,
    create_email_notification_service,
    send_email_reliable,
    send_email_reliable_sync,
)
from app.services.event_outbox_service import EventPriority


class TestEmailNotificationService:
    """Test the EmailNotificationService class."""

    @pytest.fixture
    def mock_outbox_service(self):
        """Mock EventOutboxService for testing."""
        mock_service = Mock()
        mock_service.publish_event = AsyncMock()
        mock_service.publish_event_sync = Mock()
        return mock_service

    @pytest.fixture
    def service(self, mock_outbox_service):
        """Create EmailNotificationService with mocked dependencies."""
        return EmailNotificationService(mock_outbox_service)

    @pytest.fixture
    def sample_email_data(self):
        """Sample email data for testing."""
        return SendGridEmailData(
            from_email="test@example.com",
            from_name="Test Sender",
            to_emails=["recipient@example.com"],
            subject="Test Email",
            template_id="d-test123",
            template_data={"name": "Test User"},
        )

    @pytest.mark.asyncio
    async def test_send_email_via_outbox_success(self, service, sample_email_data):
        """Test successful email queuing via outbox."""
        # Arrange
        mock_db = AsyncMock()
        user_id = str(uuid4())

        # Act
        await service.send_email_via_outbox(
            db=mock_db,
            email_data=sample_email_data,
            email_type=EmailType.NOTIFICATION,
            user_id=user_id,
        )

        # Assert
        service.event_outbox_service.publish_event.assert_called_once()
        call_args = service.event_outbox_service.publish_event.call_args

        assert call_args[1]["event_type"] == "email_notification"
        assert call_args[1]["destination"] == "sendgrid:notification"
        assert (
            call_args[1]["priority"] == EventPriority.HIGH
        )  # NOTIFICATION maps to HIGH
        assert call_args[1]["max_retries"] == 4  # NOTIFICATION gets 4 retries

        payload = call_args[1]["payload"]
        assert payload["email_data"] == sample_email_data.model_dump()
        assert payload["email_type"] == "notification"
        assert payload["user_id"] == user_id

    @pytest.mark.asyncio
    async def test_priority_mapping(self, service, sample_email_data):
        """Test email type to priority mapping."""
        mock_db = AsyncMock()

        # Test each email type priority mapping
        test_cases = [
            (EmailType.TRANSACTIONAL, EventPriority.CRITICAL, 5),
            (EmailType.ONBOARDING, EventPriority.HIGH, 4),
            (EmailType.NOTIFICATION, EventPriority.HIGH, 4),
            (EmailType.SYSTEM, EventPriority.NORMAL, 3),
            (EmailType.MARKETING, EventPriority.LOW, 2),
        ]

        for email_type, expected_priority, expected_retries in test_cases:
            service.event_outbox_service.reset_mock()

            await service.send_email_via_outbox(
                db=mock_db, email_data=sample_email_data, email_type=email_type
            )

            call_args = service.event_outbox_service.publish_event.call_args
            assert call_args[1]["priority"] == expected_priority
            assert call_args[1]["max_retries"] == expected_retries

    @pytest.mark.asyncio
    async def test_explicit_priority_override(self, service, sample_email_data):
        """Test that explicit priority overrides email type mapping."""
        mock_db = AsyncMock()

        await service.send_email_via_outbox(
            db=mock_db,
            email_data=sample_email_data,
            email_type=EmailType.MARKETING,  # Would normally be LOW
            priority=EventPriority.CRITICAL,  # Override to CRITICAL
        )

        call_args = service.event_outbox_service.publish_event.call_args
        assert call_args[1]["priority"] == EventPriority.CRITICAL

    @pytest.mark.asyncio
    async def test_send_email_via_outbox_error_handling(
        self, service, sample_email_data
    ):
        """Test error handling in outbox email sending."""
        mock_db = AsyncMock()
        service.event_outbox_service.publish_event.side_effect = Exception(
            "Outbox error"
        )

        with pytest.raises(
            EmailNotificationError, match="Failed to queue email notification"
        ):
            await service.send_email_via_outbox(
                db=mock_db,
                email_data=sample_email_data,
                email_type=EmailType.NOTIFICATION,
            )

    def test_send_email_via_outbox_sync(self, service, sample_email_data):
        """Test synchronous version of outbox email sending."""
        mock_db = Mock()
        user_id = str(uuid4())

        service.send_email_via_outbox_sync(
            db=mock_db,
            email_data=sample_email_data,
            email_type=EmailType.ONBOARDING,
            user_id=user_id,
        )

        service.event_outbox_service.publish_event_sync.assert_called_once()
        call_args = service.event_outbox_service.publish_event_sync.call_args

        assert call_args[1]["event_type"] == "email_notification"
        assert call_args[1]["destination"] == "sendgrid:onboarding"
        assert call_args[1]["priority"] == EventPriority.HIGH

    @pytest.mark.asyncio
    async def test_send_email_direct_success(self, service, sample_email_data):
        """Test direct email sending success."""
        mock_db = AsyncMock()

        # Mock the SendGrid sending method
        service._send_email_via_sendgrid = AsyncMock(return_value=True)

        result = await service.send_email_direct(
            db=mock_db, email_data=sample_email_data, email_type=EmailType.SYSTEM
        )

        assert result is True
        service._send_email_via_sendgrid.assert_called_once_with(
            sample_email_data.model_dump()
        )

    @pytest.mark.asyncio
    async def test_send_email_direct_failure(self, service, sample_email_data):
        """Test direct email sending failure."""
        mock_db = AsyncMock()

        # Mock the SendGrid sending method to fail
        service._send_email_via_sendgrid = AsyncMock(return_value=False)

        result = await service.send_email_direct(
            db=mock_db, email_data=sample_email_data, email_type=EmailType.SYSTEM
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_send_email_via_sendgrid_success(self, service, sample_email_data):
        """Test SendGrid email sending success."""
        with (
            patch("app.services.email_notification.config") as mock_config,
            patch(
                "app.services.email_notification.SendGridAPIClient"
            ) as mock_sg_client,
        ):
            mock_config.SENDGRID_API_KEY = "test_key"
            mock_response = Mock()
            mock_response.status_code = 202
            mock_sg_client.return_value.send.return_value = mock_response

            result = await service._send_email_via_sendgrid(sample_email_data)

            assert result is True
            mock_sg_client.assert_called_once_with("test_key")

    @pytest.mark.asyncio
    async def test_send_email_via_sendgrid_no_api_key(self, service, sample_email_data):
        """Test SendGrid email sending without API key."""
        with patch("app.services.email_notification.config") as mock_config:
            mock_config.SENDGRID_API_KEY = None

            result = await service._send_email_via_sendgrid(sample_email_data)

            assert result is False

    @pytest.mark.asyncio
    async def test_send_email_via_sendgrid_http_error(self, service, sample_email_data):
        """Test SendGrid HTTP error handling."""
        from urllib.error import HTTPError

        with (
            patch("app.services.email_notification.config") as mock_config,
            patch(
                "app.services.email_notification.SendGridAPIClient"
            ) as mock_sg_client,
        ):
            mock_config.SENDGRID_API_KEY = "test_key"
            mock_sg_client.return_value.send.side_effect = HTTPError(
                "", 400, "", {}, None
            )

            result = await service._send_email_via_sendgrid(sample_email_data)

            assert result is False

    @pytest.mark.asyncio
    async def test_process_outbox_email_notification_success(self, service):
        """Test processing email notification from outbox."""
        # Arrange
        payload = {
            "email_data": {
                "from_email": "test@example.com",
                "to_emails": ["recipient@example.com"],
                "subject": "Test Email",
                "template_id": "d-test123",
                "template_data": {"name": "Test"},
            },
            "email_type": "notification",
            "user_id": str(uuid4()),
        }

        # Mock the _send_email_via_sendgrid method
        service._send_email_via_sendgrid = AsyncMock(return_value=True)

        # Act
        result = await service.process_outbox_email_notification(payload)

        # Assert
        assert result is True
        service._send_email_via_sendgrid.assert_called_once()

        # Verify the email data was passed through to the provider adapter.
        # The dispatcher normalises to a dict so any backend can consume it.
        call_args = service._send_email_via_sendgrid.call_args[0][0]
        assert call_args["from_email"] == "test@example.com"
        assert call_args["to_emails"] == ["recipient@example.com"]

    @pytest.mark.asyncio
    async def test_process_outbox_email_notification_invalid_payload(self, service):
        """Test handling of invalid outbox payload."""
        # Arrange
        invalid_payload = {
            "email_type": "notification",
            # Missing required email_data
        }

        # Act
        result = await service.process_outbox_email_notification(invalid_payload)

        # Assert
        assert result is False

    @pytest.mark.asyncio
    async def test_process_outbox_email_notification_malformed_email_data(
        self, service
    ):
        """Test handling of malformed email data in payload."""
        # Arrange
        payload = {
            "email_data": {
                "invalid_field": "value"
                # Missing required fields for SendGridEmailData
            },
            "email_type": "notification",
        }

        # Act
        result = await service.process_outbox_email_notification(payload)

        # Assert
        assert result is False


class TestEmailNotificationConvenienceMethods:
    """Test the convenience methods for specific email types."""

    @pytest.fixture
    def mock_outbox_service(self):
        return AsyncMock()

    @pytest.fixture
    def service(self, mock_outbox_service):
        return EmailNotificationService(mock_outbox_service)

    @pytest.mark.asyncio
    async def test_send_welcome_email(self, service):
        """Test welcome email convenience method."""
        mock_db = AsyncMock()
        user_id = str(uuid4())

        await service.send_welcome_email(
            db=mock_db,
            user_email="user@example.com",
            user_name="John Doe",
            user_id=user_id,
            children_names=["Alice", "Bob"],
        )

        service.event_outbox_service.publish_event.assert_called_once()
        call_args = service.event_outbox_service.publish_event.call_args

        assert call_args[1]["event_type"] == "email_notification"
        payload = call_args[1]["payload"]
        assert payload["email_type"] == "onboarding"

        email_data = payload["email_data"]
        assert email_data["to_emails"] == ["user@example.com"]
        assert email_data["template_id"] == "d-3655b189b9a8427d99fe02cf7e7f3fd9"
        assert "Alice and Bob" in email_data["template_data"]["children_string"]

    @pytest.mark.asyncio
    async def test_send_subscription_welcome_email(self, service):
        """Test subscription welcome email convenience method."""
        mock_db = AsyncMock()
        user_id = str(uuid4())

        await service.send_subscription_welcome_email(
            db=mock_db,
            customer_email="customer@example.com",
            customer_name="Jane Smith",
            checkout_session_id="cs_test123",
            user_id=user_id,
        )

        service.event_outbox_service.publish_event.assert_called_once()
        call_args = service.event_outbox_service.publish_event.call_args

        payload = call_args[1]["payload"]
        assert payload["email_type"] == "transactional"

        email_data = payload["email_data"]
        assert email_data["from_email"] == "orders@hueybooks.com"
        assert email_data["template_id"] == "d-fa829ecc76fc4e37ab4819abb6e0d188"
        assert email_data["template_data"]["checkout_session_id"] == "cs_test123"

    @pytest.mark.asyncio
    async def test_send_reading_feedback_email(self, service):
        """Test reading feedback email convenience method."""
        mock_db = AsyncMock()
        reader_id = str(uuid4())

        await service.send_reading_feedback_email(
            db=mock_db,
            supporter_email="parent@example.com",
            supporter_name="Parent Name",
            reader_name="Child Name",
            book_title="Great Book",
            emoji="😊",
            descriptor="Amazing",
            feedback_url="https://example.com/feedback",
            user_id=reader_id,
        )

        service.event_outbox_service.publish_event.assert_called_once()
        call_args = service.event_outbox_service.publish_event.call_args

        payload = call_args[1]["payload"]
        assert payload["email_type"] == "notification"

        email_data = payload["email_data"]
        assert email_data["subject"] == "Child Name has done some reading!"
        assert email_data["template_id"] == "d-841938d74d9142509af934005ad6e3ed"


class TestEmailNotificationConvenienceFunctions:
    """Test the convenience functions for backward compatibility."""

    @pytest.mark.asyncio
    async def test_send_email_reliable(self):
        """Test reliable email convenience function."""
        with patch(
            "app.services.email_notification.create_email_notification_service"
        ) as mock_create:
            mock_service = AsyncMock()
            mock_create.return_value = mock_service

            mock_db = AsyncMock()
            email_data = SendGridEmailData(
                from_email="test@example.com",
                to_emails=["user@example.com"],
                subject="Test",
            )

            await send_email_reliable(
                db=mock_db,
                email_data=email_data,
                email_type=EmailType.SYSTEM,
                user_id="user123",
            )

            mock_service.send_email_via_outbox.assert_called_once_with(
                db=mock_db,
                email_data=email_data,
                email_type=EmailType.SYSTEM,
                user_id="user123",
                service_account_id=None,
                priority=None,
            )

    def test_send_email_reliable_sync(self):
        """Test reliable email sync convenience function."""
        with patch(
            "app.services.email_notification.create_email_notification_service"
        ) as mock_create:
            mock_service = Mock()
            mock_create.return_value = mock_service

            mock_db = Mock()
            email_data = SendGridEmailData(
                from_email="test@example.com",
                to_emails=["user@example.com"],
                subject="Test",
            )

            send_email_reliable_sync(
                db=mock_db,
                email_data=email_data,
                email_type=EmailType.MARKETING,
                service_account_id="service123",
            )

            mock_service.send_email_via_outbox_sync.assert_called_once_with(
                db=mock_db,
                email_data=email_data,
                email_type=EmailType.MARKETING,
                user_id=None,
                service_account_id="service123",
                priority=None,
            )


class TestEmailNotificationServiceFactory:
    """Test the factory function."""

    def test_create_email_notification_service(self):
        """Test service factory creates proper instance."""
        with patch(
            "app.services.email_notification.EventOutboxService"
        ) as mock_outbox_service:
            service = create_email_notification_service()

            assert isinstance(service, EmailNotificationService)
            mock_outbox_service.assert_called_once()
            assert service.event_outbox_service == mock_outbox_service.return_value


class TestEmailTypeEnumeration:
    """Test the EmailType enumeration."""

    def test_email_types_exist(self):
        """Test all expected email types exist."""
        assert EmailType.TRANSACTIONAL.value == "transactional"
        assert EmailType.ONBOARDING.value == "onboarding"
        assert EmailType.NOTIFICATION.value == "notification"
        assert EmailType.MARKETING.value == "marketing"
        assert EmailType.SYSTEM.value == "system"
