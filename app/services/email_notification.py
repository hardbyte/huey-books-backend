"""
Email Notification Service - Reliable email delivery using Event Outbox Pattern.

This service demonstrates the service layer architecture by:
1. Extracting email notification logic from direct background task queuing
2. Using Event Outbox pattern for reliable delivery
3. Providing clear separation of concerns and testability
4. Implementing proper error handling and retry logic

This replaces direct queue_background_task("send-email") calls with
reliable event-driven delivery.
"""

from enum import Enum
from typing import Any, Dict, List, Optional, Union

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from structlog import get_logger

from app.config import get_settings
from app.schemas.sendgrid import SendGridEmailData
from app.services.email_templates import render_email_template
from app.services.event_outbox_service import EventOutboxService, EventPriority
from app.services.exceptions import ServiceException

try:
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail
except ImportError:
    # Handle cases where SendGrid is not installed (testing/dev environments)
    SendGridAPIClient = None
    Mail = None

logger = get_logger()
config = get_settings()


class EmailType(str, Enum):
    """Email type classification for priority and retry logic."""

    TRANSACTIONAL = "transactional"  # Payment confirmations, receipts
    ONBOARDING = "onboarding"  # Welcome emails, account setup
    NOTIFICATION = "notification"  # Reading alerts, milestone notifications
    MARKETING = "marketing"  # Newsletters, promotional content
    SYSTEM = "system"  # Admin alerts, system notifications


class EmailNotificationError(ServiceException):
    """Email notification specific errors."""

    pass


class EmailNotificationService:
    """
    Service for sending email notifications using Event Outbox pattern.

    This service provides reliable email delivery with:
    - Event Outbox pattern for durability and retry logic
    - Email type classification for intelligent priority handling
    - Separation of message composition from delivery
    - Proper error handling and logging
    - Testability through dependency injection
    """

    def __init__(self, event_outbox_service: EventOutboxService):
        self.event_outbox_service = event_outbox_service

    async def send_email_via_outbox(
        self,
        db: AsyncSession,
        email_data: Union[Dict[str, Any], SendGridEmailData],
        email_type: EmailType = EmailType.NOTIFICATION,
        user_id: Optional[str] = None,
        service_account_id: Optional[str] = None,
        priority: Optional[EventPriority] = None,
    ) -> None:
        """
        Send email via Event Outbox (reliable delivery).

        This replaces queue_background_task("send-email") with reliable event delivery.
        The email is stored in the outbox and processed by background workers.

        Args:
            db: Database session
            email_data: Email payload (compatible with existing SendGrid format)
            email_type: Type of email for priority/retry logic
            user_id: Optional user ID for tracking
            service_account_id: Optional service account ID
            priority: Override automatic priority mapping
        """
        try:
            # Convert Pydantic model to dict if needed
            if hasattr(email_data, "model_dump"):
                email_data_dict = email_data.model_dump()
            elif hasattr(email_data, "dict"):
                email_data_dict = email_data.dict()
            else:
                email_data_dict = email_data

            # Validate email data structure
            self._validate_email_data(email_data_dict)

            # Determine priority based on email type if not specified
            if priority is None:
                priority = self._get_email_priority(email_type)

            # Determine retry count based on email type
            max_retries = self._get_max_retries(email_type)

            # Prepare outbox event payload
            outbox_payload = {
                "email_data": email_data_dict,
                "email_type": email_type.value,
                "user_id": user_id,
                "service_account_id": service_account_id,
            }

            # Publish to outbox for reliable delivery
            await self.event_outbox_service.publish_event(
                db=db,
                event_type="email_notification",
                destination=f"sendgrid:{email_type.value}",
                payload=outbox_payload,
                priority=priority,
                routing_key="emails",
                headers={
                    "email_type": email_type.value,
                    "to_emails": str(email_data_dict.get("to_emails", [])),
                    "subject": email_data_dict.get("subject", ""),
                },
                max_retries=max_retries,
                user_id=user_id,
                session_id=None,
                flow_id=None,
            )

            logger.info(
                "Email notification queued for reliable delivery",
                email_type=email_type.value,
                priority=priority.value,
                max_retries=max_retries,
                user_id=user_id,
                subject=email_data_dict.get("subject", "Unknown"),
            )

        except Exception as e:
            logger.error(
                "Failed to queue email notification",
                email_type=email_type.value,
                user_id=user_id,
                error=str(e),
            )
            raise EmailNotificationError(f"Failed to queue email notification: {e}")

    def send_email_via_outbox_sync(
        self,
        db: Session,
        email_data: Union[Dict[str, Any], SendGridEmailData],
        email_type: EmailType = EmailType.NOTIFICATION,
        user_id: Optional[str] = None,
        service_account_id: Optional[str] = None,
        priority: Optional[EventPriority] = None,
    ) -> None:
        """
        Synchronous wrapper for send_email_via_outbox.

        Use this in synchronous contexts where async is not available.
        """
        try:
            # Convert Pydantic model to dict if needed
            if hasattr(email_data, "model_dump"):
                email_data_dict = email_data.model_dump()
            elif hasattr(email_data, "dict"):
                email_data_dict = email_data.dict()
            else:
                email_data_dict = email_data

            # Validate email data structure
            self._validate_email_data(email_data_dict)

            # Determine priority based on email type if not specified
            if priority is None:
                priority = self._get_email_priority(email_type)

            # Determine retry count based on email type
            max_retries = self._get_max_retries(email_type)

            # Prepare outbox event payload
            outbox_payload = {
                "email_data": email_data_dict,
                "email_type": email_type.value,
                "user_id": user_id,
                "service_account_id": service_account_id,
            }

            # Publish to outbox for reliable delivery (sync)
            self.event_outbox_service.publish_event_sync(
                db=db,
                event_type="email_notification",
                destination=f"sendgrid:{email_type.value}",
                payload=outbox_payload,
                priority=priority,
                routing_key="emails",
                headers={
                    "email_type": email_type.value,
                    "to_emails": str(email_data_dict.get("to_emails", [])),
                    "subject": email_data_dict.get("subject", ""),
                },
                max_retries=max_retries,
                user_id=user_id,
            )

            logger.info(
                "Email notification queued for reliable delivery (sync)",
                email_type=email_type.value,
                priority=priority.value,
                max_retries=max_retries,
                user_id=user_id,
                subject=email_data_dict.get("subject", "Unknown"),
            )

        except Exception as e:
            logger.error(
                "Failed to queue email notification (sync)",
                email_type=email_type.value,
                user_id=user_id,
                error=str(e),
            )
            raise EmailNotificationError(f"Failed to queue email notification: {e}")

    async def process_outbox_email_notification(self, payload: Dict[str, Any]) -> bool:
        """
        Process an email notification from the Event Outbox.

        This method is called by the outbox processor to deliver
        email notifications reliably.

        Returns True if successful, False to trigger retry.
        """
        try:
            # Extract payload data
            email_data_dict = payload.get("email_data")
            email_type = payload.get("email_type", EmailType.NOTIFICATION.value)
            user_id = payload.get("user_id")

            if not email_data_dict:
                logger.error(
                    "Invalid email notification payload - missing email_data",
                    payload=payload,
                )
                return False  # Don't retry invalid payloads

            # Convert dict to SendGridEmailData for consistent processing
            try:
                email_data = SendGridEmailData(**email_data_dict)
            except Exception as e:
                logger.error(
                    "Invalid email data structure in outbox payload",
                    email_data=email_data_dict,
                    error=str(e),
                )
                return False  # Don't retry malformed payloads

            # Deliver via the configured provider (Resend or SendGrid)
            success = await self._send_email(email_data)

            if success:
                logger.info(
                    "Outbox email notification delivered",
                    email_type=email_type,
                    user_id=user_id,
                    subject=email_data_dict.get("subject", "Unknown"),
                    to_emails=email_data_dict.get("to_emails", []),
                )
            else:
                logger.warning(
                    "Outbox email notification failed",
                    email_type=email_type,
                    user_id=user_id,
                    subject=email_data_dict.get("subject", "Unknown"),
                )

            return success

        except Exception as e:
            logger.error(
                "Error processing outbox email notification",
                payload=payload,
                error=str(e),
            )
            return False  # Trigger retry

    async def send_email_direct(
        self,
        db: AsyncSession,
        email_data: Union[Dict[str, Any], SendGridEmailData],
        email_type: EmailType = EmailType.NOTIFICATION,
        user_id: Optional[str] = None,
    ) -> bool:
        """
        Send email directly without using the Event Outbox (for testing/immediate sending).

        Returns True if successful, False otherwise.
        """
        try:
            # Convert Pydantic model to dict if needed
            if hasattr(email_data, "model_dump"):
                email_data_dict = email_data.model_dump()
            elif hasattr(email_data, "dict"):
                email_data_dict = email_data.dict()
            else:
                email_data_dict = email_data

            # Deliver via the configured provider (Resend or SendGrid)
            return await self._send_email(email_data_dict)

        except Exception as e:
            logger.error(
                "Failed to send email directly",
                email_type=email_type.value,
                user_id=user_id,
                error=str(e),
            )
            return False

    @staticmethod
    def _to_dict(
        email_data: Union[Dict[str, Any], SendGridEmailData],
    ) -> Dict[str, Any]:
        if hasattr(email_data, "model_dump"):
            return email_data.model_dump()
        if hasattr(email_data, "dict"):
            return email_data.dict()
        return email_data

    async def _send_email(
        self, email_data: Union[Dict[str, Any], SendGridEmailData]
    ) -> bool:
        """Deliver an email via the provider selected by EMAIL_PROVIDER."""
        data = self._to_dict(email_data)
        provider = (config.EMAIL_PROVIDER or "sendgrid").lower()
        if provider == "resend":
            return await self._send_email_via_resend(data)
        return await self._send_email_via_sendgrid(data)

    async def _send_email_via_resend(self, data: Dict[str, Any]) -> bool:
        """Send via the Resend HTTP API (async, non-blocking).

        Uses html_content when present; otherwise renders a known legacy
        template id to HTML. Returns True on a 2xx response.
        """
        if not config.RESEND_API_KEY:
            logger.warning("Resend API key not configured")
            return False

        html = data.get("html_content")
        if not html and data.get("template_id"):
            html = render_email_template(data["template_id"], data.get("template_data"))
        if not html:
            logger.error(
                "No HTML or known template for Resend email",
                subject=data.get("subject", ""),
                template_id=data.get("template_id"),
            )
            return False

        from_email = data.get("from_email") or config.BROADCAST_FROM_EMAIL
        from_name = data.get("from_name")
        sender = f"{from_name} <{from_email}>" if from_name else from_email

        payload: Dict[str, Any] = {
            "from": sender,
            "to": data.get("to_emails"),
            "subject": data.get("subject") or "",
            "html": html,
        }
        if reply_to := data.get("reply_to"):
            payload["reply_to"] = reply_to
        if headers := data.get("headers"):
            payload["headers"] = headers

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    "https://api.resend.com/emails",
                    headers={"Authorization": f"Bearer {config.RESEND_API_KEY}"},
                    json=payload,
                )

            success = response.status_code in (200, 201)
            if success:
                logger.info(
                    "Email sent via Resend",
                    subject=payload["subject"],
                    to_emails=payload["to"],
                )
            else:
                logger.warning(
                    "Resend returned non-2xx status",
                    status_code=response.status_code,
                    body=response.text[:500],
                )
            return success
        except Exception as e:
            logger.error("Resend API error", error=str(e))
            return False

    async def _send_email_via_sendgrid(
        self, email_data: Union[Dict[str, Any], SendGridEmailData]
    ) -> bool:
        """
        Send email via SendGrid API directly.

        Returns True if successful, False otherwise.
        """
        try:
            # Check if SendGrid is available and API key is configured
            if not SendGridAPIClient or not Mail:
                logger.warning("SendGrid library not available")
                return False

            if not config.SENDGRID_API_KEY:
                logger.warning("SendGrid API key not configured")
                return False

            # Convert to dict if needed
            if hasattr(email_data, "model_dump"):
                data = email_data.model_dump()
            elif hasattr(email_data, "dict"):
                data = email_data.dict()
            else:
                data = email_data

            # Create SendGrid client
            sg = SendGridAPIClient(config.SENDGRID_API_KEY)

            # Prepare email message
            message = Mail(
                from_email=data.get("from_email"),
                to_emails=data.get("to_emails"),
                subject=data.get("subject"),
                html_content=data.get("html_content", ""),
            )

            # Route replies to a monitored inbox when specified.
            if reply_to := data.get("reply_to"):
                from sendgrid.helpers.mail import ReplyTo

                message.reply_to = ReplyTo(reply_to)

            # Custom SMTP headers, e.g. List-Unsubscribe / List-Unsubscribe-Post.
            if headers := data.get("headers"):
                from sendgrid.helpers.mail import Header

                message.header = [Header(key, value) for key, value in headers.items()]

            # Add template ID if specified
            if template_id := data.get("template_id"):
                message.template_id = template_id

            # Add template data if specified
            if template_data := data.get("template_data"):
                message.dynamic_template_data = template_data

            # Send the email
            response = sg.send(message)

            # Check if successful (SendGrid returns 202 for accepted)
            success = response.status_code == 202

            if success:
                logger.info(
                    "Email sent via SendGrid",
                    subject=data.get("subject", ""),
                    to_emails=data.get("to_emails", []),
                )
            else:
                logger.warning(
                    "SendGrid returned non-202 status", status_code=response.status_code
                )

            return success

        except Exception as e:
            logger.error("SendGrid API error", error=str(e))
            return False

    def _validate_email_data(self, email_data: Dict[str, Any]) -> None:
        """Validate email data structure."""
        required_fields = ["to_emails", "subject"]

        for field in required_fields:
            if field not in email_data:
                raise EmailNotificationError(f"Missing required email field: {field}")

        if not email_data.get("to_emails"):
            raise EmailNotificationError("Email must have at least one recipient")

    def _get_email_priority(self, email_type: EmailType) -> EventPriority:
        """Map email type to EventOutbox priority."""
        priority_map = {
            EmailType.TRANSACTIONAL: EventPriority.CRITICAL,  # Payment confirmations
            EmailType.ONBOARDING: EventPriority.HIGH,  # Welcome emails
            EmailType.NOTIFICATION: EventPriority.HIGH,  # Reading alerts
            EmailType.MARKETING: EventPriority.LOW,  # Newsletters
            EmailType.SYSTEM: EventPriority.NORMAL,  # Admin alerts
        }
        return priority_map.get(email_type, EventPriority.NORMAL)

    def _get_max_retries(self, email_type: EmailType) -> int:
        """Determine max retries based on email type criticality."""
        retry_map = {
            EmailType.TRANSACTIONAL: 5,  # Critical business emails
            EmailType.ONBOARDING: 4,  # Important first impression
            EmailType.NOTIFICATION: 4,  # Time-sensitive alerts
            EmailType.MARKETING: 2,  # Less critical
            EmailType.SYSTEM: 3,  # Standard retry
        }
        return retry_map.get(email_type, 3)

    # Convenience methods for specific email types
    async def send_welcome_email(
        self,
        db: AsyncSession,
        user_email: str,
        user_name: str,
        user_id: str,
        children_names: List[str],
    ) -> None:
        """Send welcome email to new parent users."""
        children_string = (
            " and ".join(children_names) if children_names else "your child"
        )

        email_data = SendGridEmailData(
            from_email="orders@hueybooks.com",
            from_name="Huey Books",
            to_emails=[user_email],
            subject="Welcome to Huey Books!",
            template_id="d-3655b189b9a8427d99fe02cf7e7f3fd9",  # Welcome email template
            template_data={"name": user_name, "children_string": children_string},
        )

        await self.send_email_via_outbox(
            db=db,
            email_data=email_data,
            email_type=EmailType.ONBOARDING,
            user_id=user_id,
        )

    async def send_subscription_welcome_email(
        self,
        db: AsyncSession,
        customer_email: str,
        customer_name: str,
        checkout_session_id: str,
        user_id: str,
    ) -> None:
        """Send subscription welcome email after successful payment."""
        email_data = SendGridEmailData(
            from_email="orders@hueybooks.com",
            from_name="Huey Books",
            to_emails=[customer_email],
            subject="Your Huey Books Membership",
            template_id="d-fa829ecc76fc4e37ab4819abb6e0d188",  # Subscription welcome template
            template_data={
                "name": customer_name,
                "checkout_session_id": checkout_session_id,
            },
        )

        await self.send_email_via_outbox(
            db=db,
            email_data=email_data,
            email_type=EmailType.TRANSACTIONAL,
            user_id=user_id,
        )

    async def send_reading_feedback_email(
        self,
        db: AsyncSession,
        supporter_email: str,
        supporter_name: str,
        reader_name: str,
        book_title: str,
        emoji: str,
        descriptor: str,
        feedback_url: str,
        user_id: str,
    ) -> None:
        """Send reading feedback notification to supporters."""
        email_data = SendGridEmailData(
            from_email="orders@hueybooks.com",
            from_name="Huey Books",
            to_emails=[supporter_email],
            subject=f"{reader_name} has done some reading!",
            template_id="d-841938d74d9142509af934005ad6e3ed",  # Reading feedback template
            template_data={
                "supporter_name": supporter_name,
                "reader_name": reader_name,
                "book_title": book_title,
                "emoji": emoji,
                "descriptor": descriptor,
                "feedback_url": feedback_url,
            },
        )

        await self.send_email_via_outbox(
            db=db,
            email_data=email_data,
            email_type=EmailType.NOTIFICATION,
            user_id=user_id,
        )


# Factory function for dependency injection
def create_email_notification_service() -> EmailNotificationService:
    """Create EmailNotificationService with dependencies."""
    event_outbox_service = EventOutboxService()
    return EmailNotificationService(event_outbox_service)


# Convenience functions for backward compatibility and easy migration
async def send_email_reliable(
    db: AsyncSession,
    email_data: Dict[str, Any],
    email_type: EmailType = EmailType.NOTIFICATION,
    user_id: Optional[str] = None,
    service_account_id: Optional[str] = None,
    priority: Optional[EventPriority] = None,
) -> None:
    """
    Send email with reliable delivery (replaces queue_background_task("send-email")).

    This is the new recommended way to send emails.
    """
    service = create_email_notification_service()
    await service.send_email_via_outbox(
        db=db,
        email_data=email_data,
        email_type=email_type,
        user_id=user_id,
        service_account_id=service_account_id,
        priority=priority,
    )


def send_email_reliable_sync(
    db: Session,
    email_data: Dict[str, Any],
    email_type: EmailType = EmailType.NOTIFICATION,
    user_id: Optional[str] = None,
    service_account_id: Optional[str] = None,
    priority: Optional[EventPriority] = None,
) -> None:
    """
    Send email with reliable delivery (synchronous version).

    Use this in synchronous contexts where async is not available.
    """
    service = create_email_notification_service()
    service.send_email_via_outbox_sync(
        db=db,
        email_data=email_data,
        email_type=email_type,
        user_id=user_id,
        service_account_id=service_account_id,
        priority=priority,
    )


# Specialized convenience functions for common email types
async def send_transactional_email(
    db: AsyncSession, email_data: Dict[str, Any], user_id: Optional[str] = None
) -> None:
    """Send critical transactional email (payment confirmations, receipts)."""
    await send_email_reliable(
        db=db,
        email_data=email_data,
        email_type=EmailType.TRANSACTIONAL,
        user_id=user_id,
    )


async def send_onboarding_email(
    db: AsyncSession, email_data: Dict[str, Any], user_id: Optional[str] = None
) -> None:
    """Send onboarding email (welcome messages, account setup)."""
    await send_email_reliable(
        db=db, email_data=email_data, email_type=EmailType.ONBOARDING, user_id=user_id
    )


async def send_notification_email(
    db: AsyncSession, email_data: Dict[str, Any], user_id: Optional[str] = None
) -> None:
    """Send notification email (reading alerts, milestone notifications)."""
    await send_email_reliable(
        db=db, email_data=email_data, email_type=EmailType.NOTIFICATION, user_id=user_id
    )
