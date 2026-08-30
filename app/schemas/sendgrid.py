from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from app.config import get_settings

config = get_settings()

_ALLOWED_EMAIL_HEADERS = frozenset({"list-unsubscribe", "list-unsubscribe-post"})


class SendGridCustomField(BaseModel):
    name: str
    value: int | datetime | str | None = None
    id: str | None = None


class SendGridContactData(BaseModel):
    email: EmailStr
    first_name: str | None = None
    last_name: str | None = None
    address_line_1: str | None = None
    address_line_2: str | None = None
    city: str | None = None
    state_province_region: str | None = None
    postal_code: str | None = None
    country: str | None = None
    phone_number: str | None = None
    whatsapp: str | None = None
    line: str | None = None
    facebook: str | None = None
    unique_name: str | None = None


class CustomSendGridContactData(SendGridContactData):
    custom_fields: dict[str, int | datetime | str] | None = None


class SendGridEmailData(BaseModel):
    from_email: EmailStr = "hello@hueybooks.com"
    from_name: str | None = None
    to_emails: list[EmailStr] = Field(min_length=1, max_length=50)
    reply_to: EmailStr | None = None
    subject: str | None = None
    html_content: str | None = None
    # Extra SMTP headers, e.g. List-Unsubscribe / List-Unsubscribe-Post.
    headers: dict[str, str] | None = None
    template_id: str | None = None
    template_data: dict = Field(default_factory=dict)

    @field_validator("subject", "from_name")
    @classmethod
    def reject_header_newlines(cls, value: str | None) -> str | None:
        if value is not None and ("\r" in value or "\n" in value):
            raise ValueError("Email header values cannot contain newlines")
        return value

    @field_validator("headers")
    @classmethod
    def validate_headers(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if value is None:
            return None
        for name, header_value in value.items():
            if not name or any(char in name + header_value for char in "\r\n"):
                raise ValueError("Email headers cannot contain newlines")
            if name.lower() not in _ALLOWED_EMAIL_HEADERS:
                raise ValueError(f"Email header is not permitted: {name}")
        return value

    @model_validator(mode="after")
    def validate_template_data(self):
        if self.template_data and not self.template_id:
            raise ValueError("Must provide template id if providing template data.")
        return self
