import pytest
from pydantic import ValidationError

from app.schemas.sendgrid import SendGridContactData, SendGridEmailData
from app.services.commerce import sendgrid_contact_response_to_obj


def test_sendgrid_email_search_to_contact(sendgrid_email_search_response):
    output = sendgrid_contact_response_to_obj(sendgrid_email_search_response)
    assert isinstance(output, SendGridContactData)
    assert output.email == "testaccount@sendgrid.com"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("subject", "Hello\r\nBcc: victim@example.com"),
        ("from_name", "Huey\nBcc: victim@example.com"),
    ],
)
def test_email_schema_rejects_header_injection(field, value):
    data = {
        "to_emails": ["recipient@example.com"],
        "subject": "Hello",
        field: value,
    }

    with pytest.raises(ValidationError, match="cannot contain newlines"):
        SendGridEmailData(**data)


def test_email_schema_rejects_custom_header_injection():
    with pytest.raises(ValidationError, match="cannot contain newlines"):
        SendGridEmailData(
            to_emails=["recipient@example.com"],
            subject="Hello",
            headers={"X-Value": "safe\r\nBcc: victim@example.com"},
        )


def test_email_schema_rejects_unapproved_custom_headers():
    with pytest.raises(ValidationError, match="not permitted"):
        SendGridEmailData(
            to_emails=["recipient@example.com"],
            subject="Hello",
            headers={"Bcc": "victim@example.com"},
        )
