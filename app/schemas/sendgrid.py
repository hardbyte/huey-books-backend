from datetime import datetime

from pydantic import BaseModel, EmailStr, validator

from app.config import get_settings

config = get_settings()


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
    to_emails: list[EmailStr]
    reply_to: EmailStr | None = None
    subject: str | None = None
    html_content: str | None = None
    # Extra SMTP headers, e.g. List-Unsubscribe / List-Unsubscribe-Post.
    headers: dict[str, str] | None = None
    template_id: str | None = None
    template_data: dict = {}

    # TODO[pydantic]: We couldn't refactor the `validator`, please replace it by `field_validator` manually.
    # Check https://docs.pydantic.dev/dev-v2/migration/#changes-to-validators for more information.
    @validator("template_data", always=True)
    def validate_template_data(cls, value, values):
        if value and not values.get("template_id"):
            raise ValueError("Must provide template id if providing template data.")
        return value
