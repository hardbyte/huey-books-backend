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


def _button(url: str, label: str) -> str:
    """A prominent call-to-action button (inline-styled for email clients)."""
    return (
        '<p style="margin:24px 0;">'
        f'<a href="{escape(url, quote=True)}" '
        'style="display:inline-block;background:#F22555;color:#ffffff;'
        "text-decoration:none;padding:12px 28px;border-radius:9999px;"
        f'font-weight:bold;">{escape(label)}</a></p>'
    )


def render_school_registered_html(
    school_name: str,
    contact_name: Optional[str],
    activate_url: Optional[str] = None,
) -> str:
    """Sent to the school contact once onboarding is submitted (pre-payment)."""
    greeting = f"Hi {escape(contact_name)}," if contact_name else "Hi there,"
    cta = _button(activate_url, "Activate your school") if activate_url else ""
    return _shell(
        f"<h2>{escape(school_name)} is registered</h2>"
        f"<p>{greeting}</p>"
        "<p>Thanks for signing your school up to Huey Books. To activate your "
        "school's account, the last step is to start your subscription.</p>"
        f"{cta}"
        "<p>Once payment is complete your school is live and your students can "
        "start reading.</p>"
        "<p>Happy reading,<br>The Huey Books team</p>"
    )


def render_school_activated_html(
    school_name: str, contact_name: Optional[str], admin_url: Optional[str] = None
) -> str:
    """Sent to the school contact once payment activates the school."""
    greeting = f"Hi {escape(contact_name)}," if contact_name else "Hi there,"
    manage = (
        f"<p>Manage your school, invite your teachers, and set up classes here: "
        f'<a href="{escape(admin_url, quote=True)}">{escape(admin_url)}</a></p>'
        if admin_url
        else ""
    )
    return _shell(
        f"<h2>{escape(school_name)} is live! \U0001f389</h2>"
        f"<p>{greeting}</p>"
        "<p>Your subscription is active and your Huey Books school account is "
        "ready to use. You can now add classes and students and get them "
        "reading.</p>"
        f"{manage}"
        "<p>This is a receipt that your school subscription has started. "
        "If you have any questions just reply to this email.</p>"
        "<p>Happy reading,<br>The Huey Books team</p>"
    )


def render_school_staff_invite_html(
    school_name: str, invitee_name: Optional[str], admin_url: Optional[str]
) -> str:
    """Sent to a teacher/staff member added to a school."""
    greeting = f"Hi {escape(invitee_name)}," if invitee_name else "Hi there,"
    cta = (
        f"<p>Sign in with this email address to get started: "
        f'<a href="{escape(admin_url, quote=True)}">{escape(admin_url)}</a></p>'
        if admin_url
        else "<p>Sign in with this email address to get started.</p>"
    )
    return _shell(
        f"<h2>You've been added to {escape(school_name)} on Huey Books</h2>"
        f"<p>{greeting}</p>"
        f"<p>{escape(school_name)} has added you to their Huey Books account, so "
        "you can help your students find books they'll love.</p>"
        f"{cta}"
        "<p>Happy reading,<br>The Huey Books team</p>"
    )


def render_school_renewal_reminder_html(
    school_name: str,
    contact_name: Optional[str],
    amount: Optional[str],
    renewal_date: Optional[str],
) -> str:
    """Sent ahead of a school subscription renewal charge."""
    greeting = f"Hi {escape(contact_name)}," if contact_name else "Hi there,"
    when = f" on {escape(renewal_date)}" if renewal_date else " soon"
    cost = f" for {escape(amount)}" if amount else ""
    return _shell(
        f"<h2>Your Huey Books subscription renews{when}</h2>"
        f"<p>{greeting}</p>"
        f"<p>This is a heads-up that {escape(school_name)}'s Huey Books "
        f"subscription will renew{when}{cost}, so your school keeps its access "
        "without interruption.</p>"
        "<p>No action is needed to continue. If you'd like to make any changes, "
        "just reply to this email.</p>"
        "<p>Happy reading,<br>The Huey Books team</p>"
    )


def _contribution_outcome_sentence(
    outcome: str, school_name: str, access_until: Optional[str], *, third_person: bool
) -> str:
    """Accurate outcome copy shared by the payer and school contribution emails.

    ``outcome`` is one of ``credited``, ``activated``, ``extended``, or
    ``received`` (a fallback when the contribution could not be applied
    automatically and needs manual handling).
    """
    school = escape(school_name)
    when = f" through {escape(access_until)}" if access_until else ""
    subject = "your school" if third_person else school
    if outcome == "credited":
        return (
            f"<p>Your contribution has been credited toward {school}'s next Huey "
            "Books invoice.</p>"
            if not third_person
            else "<p>Their contribution has been credited toward your school's "
            "next Huey Books invoice.</p>"
        )
    if outcome == "activated":
        return (
            f"<p>Their contribution has activated {subject}{when}.</p>"
            if third_person
            else (
                f"<p>Thanks to you, {school} is now live on Huey Books{when} and its "
                "students can start reading.</p>"
            )
        )
    if outcome == "extended":
        return (
            f"<p>Their contribution has extended {subject}'s access{when}.</p>"
            if third_person
            else f"<p>Your contribution has extended {school}'s access{when}.</p>"
        )
    # received: paid but not yet applied automatically
    return (
        "<p>Their contribution has been received; our team will apply it to your "
        "school shortly.</p>"
        if third_person
        else f"<p>Your contribution toward {school} has been received; our team "
        "will apply it shortly.</p>"
    )


def render_contribution_thankyou_html(
    school_name: str,
    amount: Optional[str],
    outcome: str,
    access_until: Optional[str] = None,
) -> str:
    """Sent to a supporter who paid a one-off contribution toward a school."""
    gift = f" of {escape(amount)}" if amount else ""
    outcome_html = _contribution_outcome_sentence(
        outcome, school_name, access_until, third_person=False
    )
    return _shell(
        "<h2>Thank you for contributing \U0001f49b</h2>"
        "<p>Hi there,</p>"
        f"<p>Thank you for your contribution{gift} toward "
        f"{escape(school_name)}'s Huey Books subscription.</p>"
        f"{outcome_html}"
        "<p>This email is your receipt. If you have any questions just reply to "
        "it.</p>"
        "<p>Happy reading,<br>The Huey Books team</p>"
    )


def render_school_contribution_notice_html(
    school_name: str,
    contact_name: Optional[str],
    amount: Optional[str],
    outcome: str,
    access_until: Optional[str] = None,
) -> str:
    """Sent to a school's contact when a supporter contributes toward them."""
    greeting = f"Hi {escape(contact_name)}," if contact_name else "Hi there,"
    gift = f" of {escape(amount)}" if amount else ""
    outcome_html = _contribution_outcome_sentence(
        outcome, school_name, access_until, third_person=True
    )
    return _shell(
        "<h2>Someone contributed to your Huey Books subscription</h2>"
        f"<p>{greeting}</p>"
        f"<p>A supporter has contributed{gift} toward {escape(school_name)}'s "
        "Huey Books subscription.</p>"
        f"{outcome_html}"
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
