"""HTML for school lifecycle emails (signup alert, registration, activation).

Rendered inline (Resend has no server-side templates) from plain data, so these
functions stay pure and unit-testable. Call sites pull the values out of
``school.info["onboarding"]`` and send via the email-notification service.
"""

from html import escape
from typing import Optional

_FONT = "font-family: Arial, Helvetica, sans-serif; font-size: 15px; line-height: 1.5; color: #1f1f1f;"


def _shell(body_html: str) -> str:
    return (
        f'<div style="{_FONT} max-width: 600px; margin: 0 auto;">'
        f"{body_html}"
        '<hr style="border:none;border-top:1px solid #eee;margin:24px 0 12px;">'
        '<p style="font-size:12px;color:#888;">Huey Books</p>'
        "</div>"
    )


def render_school_registered_html(school_name: str, contact_name: Optional[str]) -> str:
    """Sent to the school contact once onboarding is submitted (pre-payment)."""
    greeting = f"Hi {escape(contact_name)}," if contact_name else "Hi there,"
    return _shell(
        f"<h2>{escape(school_name)} is registered</h2>"
        f"<p>{greeting}</p>"
        "<p>Thanks for signing your school up to Huey Books. To activate your "
        "school's account, the last step is to start your annual subscription.</p>"
        "<p>Once payment is complete your school is live and your students can "
        "start reading.</p>"
        "<p>Happy reading,<br>The Huey Books team</p>"
    )


def render_school_activated_html(school_name: str, contact_name: Optional[str]) -> str:
    """Sent to the school contact once payment activates the school."""
    greeting = f"Hi {escape(contact_name)}," if contact_name else "Hi there,"
    return _shell(
        f"<h2>{escape(school_name)} is live! \U0001f389</h2>"
        f"<p>{greeting}</p>"
        "<p>Your subscription is active and your Huey Books school account is "
        "ready to use. You can now add classes and students and get them "
        "reading.</p>"
        "<p>This is a receipt that your annual school subscription has started. "
        "If you have any questions just reply to this email.</p>"
        "<p>Happy reading,<br>The Huey Books team</p>"
    )


def render_staff_new_school_alert_html(
    *,
    school_name: str,
    wriveted_id: str,
    contact_name: Optional[str],
    contact_email: Optional[str],
    contact_role: Optional[str],
    country_code: Optional[str],
    student_count_estimate: Optional[int],
    message: Optional[str],
) -> str:
    """Internal alert (to staff) that a new school has signed up."""

    def row(label: str, value) -> str:
        return (
            f'<tr><td style="padding:3px 12px 3px 0;color:#888;">{escape(label)}</td>'
            f'<td style="padding:3px 0;">{escape(str(value)) if value not in (None, "") else "&mdash;"}</td></tr>'
        )

    table = (
        '<table style="border-collapse:collapse;font-size:14px;">'
        + row("School", school_name)
        + row("Wriveted id", wriveted_id)
        + row("Contact", contact_name)
        + row("Email", contact_email)
        + row("Role", contact_role)
        + row("Country", country_code)
        + row("Est. students", student_count_estimate)
        + row("Message", message)
        + "</table>"
    )
    return _shell(
        "<h2>New school signup</h2>"
        "<p>A school just self-registered on Huey Books and is pending payment.</p>"
        f"{table}"
    )
