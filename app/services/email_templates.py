"""Inline HTML for transactional emails.

SendGrid hosted these as dynamic templates (``d-...`` ids) keyed by
``template_id``. Resend has no server-side templates, so we render equivalent
HTML here from the same ``template_data`` the call sites already pass. Keep the
markup simple and inline-styled for broad email-client support.

The copy here is a functional port, not a design pass — worth a review by
someone who owns the brand voice.
"""

from html import escape
from typing import Callable, Optional

_FONT = "font-family: Arial, Helvetica, sans-serif; font-size: 15px; line-height: 1.5; color: #1f1f1f;"


def _shell(body_html: str) -> str:
    """Wrap body content in the shared Huey Books email frame."""
    return (
        f'<div style="{_FONT} max-width: 600px; margin: 0 auto;">'
        f"{body_html}"
        '<hr style="border:none;border-top:1px solid #eee;margin:24px 0 12px;">'
        '<p style="font-size:12px;color:#888;">Huey Books</p>'
        "</div>"
    )


def _button(href: str, label: str) -> str:
    return (
        f'<p style="margin:24px 0;"><a href="{escape(href, quote=True)}" '
        'style="background:#5b3fd6;color:#fff;text-decoration:none;padding:12px 20px;'
        'border-radius:6px;display:inline-block;">'
        f"{escape(label)}</a></p>"
    )


def _welcome(d: dict) -> str:
    name = escape(str(d.get("name") or "there"))
    children = escape(str(d.get("children_string") or "your reader"))
    return _shell(
        f"<h2>Welcome to Huey Books!</h2>"
        f"<p>Hi {name},</p>"
        f"<p>Thanks for joining Huey Books. We're excited to help {children} "
        "discover books they'll love.</p>"
        "<p>Happy reading,<br>The Huey Books team</p>"
    )


def _subscription(d: dict) -> str:
    name = escape(str(d.get("name") or "there"))
    return _shell(
        "<h2>Your Huey Books Membership</h2>"
        f"<p>Hi {name},</p>"
        "<p>Thank you for becoming a Huey Books member! Your subscription is "
        "active and you now have full access.</p>"
        "<p>If you have any questions just reply to this email.</p>"
        "<p>Happy reading,<br>The Huey Books team</p>"
    )


def _reading_feedback(d: dict) -> str:
    supporter = escape(str(d.get("supporter_name") or "there"))
    reader = escape(str(d.get("reader_name") or "your reader"))
    book = escape(str(d.get("book_title") or "a book"))
    emoji = escape(str(d.get("emoji") or ""))
    descriptor = escape(str(d.get("descriptor") or ""))
    url = d.get("encoded_url")
    cta = _button(url, "See their reading") if url else ""
    return _shell(
        f"<h2>{reader} has done some reading! {emoji}</h2>"
        f"<p>Hi {supporter},</p>"
        f"<p>{reader} just logged some reading of <strong>{book}</strong>"
        + (f" and found it {descriptor}." if descriptor else ".")
        + "</p>"
        + cta
        + "<p>Happy reading,<br>The Huey Books team</p>"
    )


# Maps the legacy SendGrid dynamic-template ids to their HTML renderers.
_RENDERERS: dict[str, Callable[[dict], str]] = {
    "d-3655b189b9a8427d99fe02cf7e7f3fd9": _welcome,
    "d-fa829ecc76fc4e37ab4819abb6e0d188": _subscription,
    "d-841938d74d9142509af934005ad6e3ed": _reading_feedback,
}


def render_email_template(template_id: str, data: Optional[dict]) -> Optional[str]:
    """Render HTML for a known legacy template id, or None if unknown."""
    renderer = _RENDERERS.get(template_id)
    if renderer is None:
        return None
    return renderer(data or {})
