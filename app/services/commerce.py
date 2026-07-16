import json
from datetime import datetime
from json import loads
from typing import Container, ItemsView, Union
from urllib.error import HTTPError
from uuid import UUID

from pydantic import EmailStr, parse_obj_as
from python_http_client.client import Response
from python_http_client.exceptions import NotFoundError
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import From, Mail
from sqlalchemy.orm import Session
from structlog import get_logger
from twilio.rest import Client as TwilioClient

from app import crud
from app.config import get_settings
from app.db.session import get_session_maker
from app.models.service_account import ServiceAccount
from app.models.user import User
from app.schemas.sendgrid import (
    CustomSendGridContactData,
    SendGridContactData,
    SendGridCustomField,
    SendGridEmailData,
)
from app.schemas.shopify import ShopifyEventRoot
from app.services.account_refs import AccountRefType, load_account_ref

logger = get_logger()
config = get_settings()


def get_sendgrid_api() -> SendGridAPIClient:
    return SendGridAPIClient(config.SENDGRID_API_KEY)


def get_twilio_client() -> TwilioClient:
    return TwilioClient(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)


def get_sendgrid_custom_field_id_from_name(
    custom_fields: list[SendGridCustomField], name: str
) -> str | None:
    return next(
        (field.id for field in custom_fields if field.name == name),
        None,
    )


def sendgrid_contact_response_to_obj(
    raw_contact_response: Response,
) -> SendGridContactData:
    response_dict = raw_contact_response.to_dict

    results_obj: dict = response_dict.get("result")
    if not results_obj:
        raise NotFoundError

    results: ItemsView[str, Container] = results_obj.values()
    first_result = next(iter(results), None)
    if not first_result:
        raise NotFoundError

    contact_obj = first_result.get("contact")
    if not contact_obj:
        raise NotFoundError

    return CustomSendGridContactData(**contact_obj)


def get_sendgrid_custom_fields(sg: SendGridAPIClient) -> list[SendGridCustomField]:
    """
    Produces a list of the custom field objects currently on the SendGrid account
    """
    fields_raw = sg.client.marketing.field_definitions.get()
    fields_obj = loads(fields_raw.body)["custom_fields"]
    return parse_obj_as(list[SendGridCustomField], fields_obj)


def validate_sendgrid_custom_fields(
    custom_fields: list[SendGridCustomField], sg: SendGridAPIClient
):
    # enrich the 'named' custom fields with their equivalent sendgrid ids, provided they exist
    supplied_fields: list[SendGridCustomField] = parse_obj_as(
        list[SendGridCustomField], custom_fields
    )
    validated_fields: dict[str, int | datetime | str] = {}

    current_fields = get_sendgrid_custom_fields(sg)
    for supplied_field in supplied_fields:
        id = get_sendgrid_custom_field_id_from_name(current_fields, supplied_field.name)
        if id:
            validated_fields[id] = supplied_field.value
        else:
            raise ValueError(
                "No custom field exists with the name {supplied_field.name}."
            )

    return validated_fields


def get_sendgrid_contact_by_email(
    sg: SendGridAPIClient, email: EmailStr
) -> CustomSendGridContactData:
    try:
        found_contact_raw = sg.client.marketing.contacts.search.emails.post(
            request_body={"emails": [email]}
        )  # raises NotFoundError upon no matches

        # at this point we're guaranteed a matching 'result' object holding a 'contact' object
        return sendgrid_contact_response_to_obj(found_contact_raw)

    except NotFoundError:
        return None


def increment_sendgrid_children_custom_fields(
    email, custom_fields: dict[str, int | datetime | str], sg: SendGridAPIClient
) -> dict[str, int | datetime | str]:
    # we may want to increment custom fields such as "child_1_age" contextually
    # i.e. if a parent goes through a landbot chat twice for two children, each use of the landbot chat will still provide the endpoint with "child_1_x".
    # but instead of overwriting the contact's "child_1_x" fields, we want to "increment" them to "child_2_x", etc.
    # we can determine the need to increment based on whether or not the contact already exists, and what their current custom fields are.
    # this method is entirely imperfect, but the arbitrary data offerings on sendgrid are very primitive, and until we have child accounts
    # linked to parents, this is the best way to achieve mostly accurate segmentation

    # first, check if the contact already exists by searching for their email
    contact = get_sendgrid_contact_by_email(sg, email)

    # if not existing, no need to increment
    if not contact:
        return custom_fields

    # if existing, check their custom fields to "count" their children
    num_children = 0
    for i in range(1, 3):
        if contact.custom_fields.get(f"child_{i}_age"):
            num_children += 1
        else:
            break

    if num_children > 0:
        index_to_update = min(num_children + 1, 3)

        # the custom fields need to be updated with respect to their 'id'
        custom_fields_sendgrid = get_sendgrid_custom_fields(sg)

        base_age_id = get_sendgrid_custom_field_id_from_name(
            custom_fields_sendgrid, "child_1_age"
        )
        new_age_id = get_sendgrid_custom_field_id_from_name(
            custom_fields_sendgrid, f"child_{index_to_update}_age"
        )
        age = custom_fields.pop(base_age_id, None)
        if age is not None and new_age_id:
            custom_fields[new_age_id] = age

        base_reading_ability_id = get_sendgrid_custom_field_id_from_name(
            custom_fields_sendgrid, "child_1_reading_ability"
        )
        new_reading_ability_id = get_sendgrid_custom_field_id_from_name(
            custom_fields_sendgrid, f"child_{index_to_update}_reading_ability"
        )
        reading_ability = custom_fields.pop(base_reading_ability_id, None)
        if reading_ability is not None and new_reading_ability_id:
            custom_fields[new_reading_ability_id] = reading_ability

    return custom_fields


def upsert_sendgrid_contact(
    data: CustomSendGridContactData,
    session: Session,
    account: User | ServiceAccount | None,
    sg: SendGridAPIClient,
    increment_children: bool = False,
):
    """
    Upserts a Sendgrid contact with the provided data, logging an event
    """
    if data.custom_fields and increment_children:
        data.custom_fields = increment_sendgrid_children_custom_fields(
            data.email, data.custom_fields, sg
        )

    try:
        response = sg.client.marketing.contacts.put(
            request_body={"contacts": [data.dict()]}
        )
        output = {
            "code": str(response.status_code),
            "body": str(response.body),
            "headers": str(response.headers),
        }

        error = None
    except HTTPError as e:
        error = f"Error: {e}"

    crud.event.create(
        session=session,
        title="SendGrid contact upsert requested",
        description="A SendGrid contact was queued to be either created or updated",
        info={
            "result": "success" if not error else "error",
            "detail": error or output,
            "data": data.dict(),
        },
        account=account,
        commit=True,
        level="warning" if error else "debug",
    )


def upsert_sendgrid_contact_background(
    data: CustomSendGridContactData,
    account_type: AccountRefType | None,
    account_id: UUID | None,
    increment_children: bool = False,
):
    Session = get_session_maker()
    with Session() as session:
        account = load_account_ref(session, account_type, account_id)
        upsert_sendgrid_contact(
            data, session, account, get_sendgrid_api(), increment_children
        )


def send_sendgrid_email(
    data: SendGridEmailData,
    session: Session,
    sg: SendGridAPIClient,
    account: Union[ServiceAccount, User] = None,
):
    """
    Send a dynamic email to a list of email addresses, logging an event
    """
    message = Mail(from_email=data.from_email, to_emails=data.to_emails)

    if data.template_data:
        message.dynamic_template_data = data.template_data
    if data.template_id:
        message.template_id = data.template_id
    if data.from_name:
        message.from_email = From(data.from_email, data.from_name)

    error = None
    try:
        response = sg.send(message)
        output = {
            "code": str(response.status_code),
            "body": str(response.body),
            "headers": str(response.headers),
        }

    except HTTPError as e:
        error = f"Error: {e}"

    crud.event.create(
        session=session,
        title="SendGrid email sent",
        description="An email was sent to a user via SendGrid",
        info={
            "result": "success" if not error else "error",
            "detail": error or output,
            "data": data.dict(),
        },
        account=account,
        commit=True,
        level="warning" if error else "debug",
    )


def upsert_sendgrid_from_shopify_order(
    data: ShopifyEventRoot, sg: SendGridAPIClient, session: Session
):
    existing_sendgrid_contact = get_sendgrid_contact_by_email(sg, data.customer.email)
    # create a sendgrid contact using the overlap of the schemas (will update the sendgrid contact's name etc.)
    new_sendgrid_contact = CustomSendGridContactData(
        **data.customer.dict(), custom_fields={}
    )
    custom_fields = get_sendgrid_custom_fields(sg)

    # update the 'last_purchase' custom field to the order's timestamp
    last_purchase_id = get_sendgrid_custom_field_id_from_name(
        custom_fields, "last_purchase"
    )
    new_sendgrid_contact.custom_fields[last_purchase_id] = data.created_at.isoformat()

    # increment the 'purchases' by 1
    purchases = (
        existing_sendgrid_contact.custom_fields.get("purchases", 0)
        if existing_sendgrid_contact
        else 0
    )
    purchases_id = get_sendgrid_custom_field_id_from_name(custom_fields, "purchases")
    new_sendgrid_contact.custom_fields[purchases_id] = purchases + 1

    # increment the 'amount_spent' by the... amount spent
    amount_spent = (
        existing_sendgrid_contact.custom_fields.get("amount_spent", 0)
        if existing_sendgrid_contact
        else 0
    )
    amount_spent_id = get_sendgrid_custom_field_id_from_name(
        custom_fields, "amount_spent"
    )
    new_sendgrid_contact.custom_fields[amount_spent_id] = amount_spent + float(
        data.total_price
    )

    upsert_sendgrid_contact(new_sendgrid_contact, session, None, sg)


def process_shopify_order(
    data: ShopifyEventRoot, sg: SendGridAPIClient, session: Session
):
    upsert_sendgrid_from_shopify_order(data, sg, session)
    crud.event.create(
        session,
        title="Shopify: Order placed",
        description="A customer placed an order on the HueyBooks Shopify store",
        info={"shopify_data": json.loads(json.dumps(data.dict(), default=str))},
    )


def process_shopify_order_background(data: ShopifyEventRoot):
    Session = get_session_maker()
    with Session() as session:
        process_shopify_order(data, get_sendgrid_api(), session)
