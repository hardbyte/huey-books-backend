from datetime import datetime
from typing import Optional

from stripe import Customer as StripeCustomer
from stripe import Price as StripePrice
from stripe import Product as StripeProduct
from stripe import Subscription as StripeSubscription
from structlog import get_logger
from structlog.contextvars import bind_contextvars

from app import crud
from app.config import get_settings
from app.db.session import get_session_maker
from app.models import School, User
from app.models.event import EventSlackChannel
from app.models.product import Product
from app.models.school import SchoolState
from app.models.subscription import Subscription, SubscriptionType
from app.models.user import UserAccountType
from app.repositories.product_repository import product_repository
from app.repositories.school_repository import school_repository
from app.repositories.subscription_repository import subscription_repository
from app.schemas.product import ProductCreateIn
from app.schemas.subscription import SubscriptionCreateIn
from app.services.email_notification import EmailType, send_email_reliable_sync
from app.services.events import create_event
from app.services.school_emails import (
    render_school_activated_html,
    render_school_renewal_reminder_html,
)

logger = get_logger()
settings = get_settings()


def process_stripe_event(event_type: str, event_data):
    """
    Processes an event sent by Stripe webhook.

    These webhook events are sent asynchronously, and order is not guaranteed -
    i.e. when a new customer subscribes, a `customer.subscription.created` event
    may be sent before a `customer.created` event.
    See https://stripe.com/docs/webhooks/best-practices#event-ordering

    Because of this, it may be wise to consider overarching transactions and use-cases,
    fetching data from Stripe as needed, rather than blindly processing each event separately.
    For example, when a new customer subscribes, we may want to establish a
    user/subscription/product relationship in a single transaction, rather than piecemeal.

    The main use-cases we want to handle at this stage are:
    - A brand new customer subscribes, without an existing account
    - An existing Free tier customer subscribes
    - An existing customer modifies their subscription (e.g. upgrades, downgrades, or cancels)

    We can still capture other events for logging purposes,
    but we need not apply any business logic in them.

    It's worth noting that it's only the events we receive that are asynchronous,
    not the underlying processes and database transactions at Stripe's end.
    So, for example, when a new customer successfully subscribes, and the following events are received
    in the order listed (with the actual chronological order of the underlying process in parentheses):

    (2) - `customer.subscription.created`
            (contains the `customer` id, information about the subscription)
    (3) - `checkout.session.completed`
            (contains the `customer` id, the `subscription` id, and any included
            `client_reference_id` sent by our frontend to identify the user)
    (1) - `customer.created`
            (contains the `customer` information, but no `subscription` information)

    We -could- handle each of these events separately (with the incomplete pictures they each provide),
    but will be able to handle them more efficiently if we target the event with the most significance and pointers/id's.

    For this example, it's most convenient to do this by only heavily processing the `checkout.session.completed`
    event, which includes id's for relevant instances of `customer` and `subscription` (and by extension `price`/`product`) in its payload.
    As this is the chronologically final event in this use-case, we can then simply query the Stripe API for the
    relevant instances of `customer` and `subscription`, knowing that Stripe has already processed these.
    This effectively gives us the full picture in a single event, instead of waiting for and blindly processing the
    `customer.created` and `customer.subscription.created` events.
    """

    object_type = event_data.get("object")

    logger.info(
        "Processing a stripe event", event_type=event_type, event_data=event_data
    )
    bind_contextvars(stripe_event_type=event_type)

    Session = get_session_maker()
    with Session() as session:
        wriveted_user, school, stripe_customer = (
            _extract_user_and_customer_from_stripe_object(
                session, event_data, object_type
            )
        )
        # we now have a stripe customer and, if they exist, an equivalent wriveted user/school

        match event_type:
            # Actionable events
            case "invoice.paid":
                _handle_invoice_paid(session, wriveted_user, school, event_data)
            case "invoice.upcoming":
                # Fires ~ahead of a renewal charge; remind school admins.
                _handle_invoice_upcoming(session, event_data)
            case "invoice.payment_failed":
                # Stripe runs its own dunning retries; final give-up arrives as
                # customer.subscription.deleted, which deactivates the school.
                logger.warning(
                    "Invoice payment failed",
                    stripe_subscription=event_data.get("subscription"),
                )
            case (
                "checkout.session.completed"
                | "checkout.session.async_payment_succeeded"
            ):
                _handle_checkout_session_completed(
                    session, wriveted_user, school, event_data
                )

            case "customer.subscription.updated":
                # Sent when the subscription is successfully started, after the payment is confirmed.
                # Also sent whenever a subscription is changed. For example applying a discount,
                # adding an invoice item, and changing plans all trigger this event.
                # https://stripe.com/docs/api/subscriptions/object
                logger.info(
                    "Subscription updated. Updating underlying product or plan if necessary"
                )
                _handle_subscription_updated(session, wriveted_user, school, event_data)

            case "customer.subscription.deleted":
                # Sent when a customer’s subscription ends
                # https://stripe.com/docs/api/subscriptions/object
                logger.info("Subscription deleted")
                _handle_subscription_cancelled(
                    session, wriveted_user, school, event_data
                )

            # Log-and-move-on events
            case "customer.created":
                logger.info("Stripe customer created")

            case "customer.updated":
                logger.info("Stripe customer updated. Not taking any action")

            case "customer.subscription.created":
                logger.info(
                    "Stripe subscription created",
                    stripe_subscription_id=event_data.get("subscription"),
                )
                _handle_subscription_created(session, wriveted_user, school, event_data)

            case "payment_intent.succeeded":
                logger.info("Payment succeeded")

            case "payment_intent.payment_failed":
                logger.warning("Payment failed")

            case _:
                logger.info("Unhandled Stripe event")
                logger.debug("Stripe event data", stripe_event_data=event_data)


def _extract_user_and_customer_from_stripe_object(
    session, stripe_object, stripe_object_type
):
    logger.info(
        "Extracting user and customer from stripe object", stripe_object=stripe_object
    )

    wriveted_user = None
    school = None
    # webhook is only listening to events that are guaranteed to include a customer id (for now)
    stripe_customer = _get_stripe_customer_from_stripe_object(
        stripe_object, stripe_object_type
    )
    logger.info(
        "Got stripe customer from stripe object", stripe_customer=stripe_customer
    )

    # check customer metadata for a wriveted user id
    # (this is stored upon the first successful checkout)
    metadata = stripe_customer.get("metadata")
    stripe_customer_wriveted_id = metadata.get("wriveted_id") if metadata else None
    if stripe_customer_wriveted_id:
        wriveted_user = crud.user.get(session, stripe_customer_wriveted_id)
        logger.info("Found wriveted user id in customer metadata", user=wriveted_user)

    # check for any custom client_reference_id injected by our frontend (a Wriveted user id or school id)
    # note: empty values can sometimes be returned as the strings "undefined" or "null"
    client_reference_id = stripe_object.get("client_reference_id")
    if client_reference_id == "undefined" or client_reference_id == "null":
        client_reference_id = None

    if client_reference_id:
        if referenced_user := crud.user.get(session, client_reference_id):
            if wriveted_user and referenced_user != wriveted_user:
                logger.warning(
                    "Client reference id does not match User associated with Stripe customer id",
                    referenced_user_id=referenced_user.id,
                )
            else:
                wriveted_user = referenced_user
                logger.info(
                    "Client reference id matches User associated with Stripe customer id",
                    referenced_user_id=referenced_user.id,
                )
        elif school := school_repository.get_by_wriveted_id(
            session, wriveted_id=client_reference_id
        ):
            logger.info(
                "Client reference id matches School",
                school_id=school.wriveted_identifier,
                school_name=school.name,
            )
            bind_contextvars(
                school_id=school.wriveted_identifier, school_name=school.name
            )

        else:
            logger.warning(
                "Client reference id does not match any user",
                client_reference_id=client_reference_id,
            )

    if wriveted_user:
        bind_contextvars(wriveted_user_id=str(wriveted_user.id))

    return wriveted_user, school, stripe_customer


def _get_stripe_customer_from_stripe_object(stripe_object, stripe_object_type):
    if stripe_object_type == "customer":
        stripe_customer = stripe_object
    else:
        stripe_customer_id = stripe_object.get("customer")
        if stripe_customer_id:
            stripe_customer = StripeCustomer.retrieve(stripe_customer_id)
            bind_contextvars(stripe_customer_id=stripe_customer_id)
        else:
            raise NotImplementedError("Stripe event does not include a customer id")
    return stripe_customer


def _handle_invoice_paid(
    session, wriveted_user: User | None, school: School | None, event_data: dict
):
    logger.info("Invoice paid. Updating subscription")
    # Get the subscription id from the invoice paid event
    stripe_subscription_id = event_data.get("subscription")
    stripe_customer_id = event_data.get("customer")

    if stripe_subscription_id is None:
        logger.warning(
            "Invoice paid event does not include a subscription id. Ignoring"
        )
        return

    # Get the subscription from Stripe and fetch the current expiration date
    stripe_subscription = StripeSubscription.retrieve(stripe_subscription_id)
    subscription = subscription_repository.get_by_id(
        session, subscription_id=stripe_subscription_id
    )
    if subscription is None:
        logger.warning(
            "Invoice paid event references a subscription that is not in the database"
        )
        return

    subscription.expiration = datetime.utcfromtimestamp(
        stripe_subscription.current_period_end
    )
    subscription.is_active = stripe_subscription.status in {"active", "past_due"}

    # Use unified event workflow instead of direct crud.event.create
    create_event(
        session=session,
        title="Subscription payment received",
        description="Invoice paid for subscription",
        info={
            "stripe_invoice_id": event_data.get("id"),
            "stripe_customer_id": stripe_customer_id,
            "stripe_subscription_id": stripe_subscription_id,
            "expiration": str(subscription.expiration),
        },
        school=school,
        account=wriveted_user,
        enable_processing=False,  # No processing needed for payment received
        external_notifications=False,  # Internal accounting event
    )


def _handle_checkout_session_completed(
    session, wriveted_user: Optional[User], school: School | None, event_data: dict
) -> Optional[Subscription]:
    """

    # https://stripe.com/docs/api/checkout/sessions/object
    """
    logger.info("Checkout session completed. Creating subscription")
    # in this case we want to query the Stripe API for the subscription and customer,
    # as we know they exist and have been processed by Stripe.
    # we can then use this information to create a new subscription in our database (if needed),
    # and link the customer to the user or school (if needed).
    stripe_subscription_id = event_data.get("subscription")
    client_reference_id = event_data.get("client_reference_id")
    logger.info(
        "Client reference id from checkout session",
        client_reference_id=client_reference_id,
    )
    # Note this checkout complete could get fired for non-subscription purchases
    if stripe_subscription_id is None:
        logger.warning(
            "Checkout session completed for non-subscription purchase. Ignoring"
        )
        return

    stripe_subscription = StripeSubscription.retrieve(stripe_subscription_id)

    stripe_customer_id = stripe_subscription.customer
    stripe_customer = StripeCustomer.retrieve(stripe_customer_id)
    stripe_customer_email = stripe_customer.get("email")

    if not stripe_customer_email:
        logger.warning("Checkout session emitted without an email address")

    checkout_session_id = event_data.get("id")

    if wriveted_user and not stripe_customer.metadata.get("wriveted_id"):
        # we have a wriveted user, but no wriveted id on the stripe customer
        logger.info(
            "Updating Stripe customer metadata with Wriveted user id",
            stripe_customer_id=stripe_customer_id,
        )
        stripe_customer.metadata["wriveted_id"] = str(wriveted_user.id)
        try:
            stripe_customer.save()
        except Exception as e:
            # Can fail if e.g. current stripe api key doesn't have "rak_customer_write" permission
            logger.error(
                "Failed to update Stripe customer metadata with Wriveted user id",
                stripe_customer_id=stripe_customer_id,
                error=str(e),
            )

    # ensure our db knows about the specified product
    stripe_price_id = stripe_subscription["items"]["data"][0]["price"]["id"]
    _sync_stripe_price_with_wriveted_product(session, stripe_price_id)

    # create or update a base subscription in our database
    wriveted_parent_id = (
        str(wriveted_user.id)
        if wriveted_user and wriveted_user.type == UserAccountType.PARENT
        else None
    )

    base_subscription_data = SubscriptionCreateIn(
        id=stripe_subscription_id,
        product_id=stripe_price_id,
        stripe_customer_id=stripe_subscription.customer,
        parent_id=wriveted_parent_id,
        school_id=str(school.wriveted_identifier) if school else None,
        expiration=stripe_subscription.current_period_end,
    )
    logger.info(
        "Upserting subscription in our database",
        base_subscription_data=base_subscription_data,
        checkout_session_id=checkout_session_id,
    )
    subscription = subscription_repository.upsert(session, base_subscription_data)
    logger.debug("Upserted subscription in our database", subscription=subscription)

    # update the subscription in our database with the latest information
    # we store the checkout session id in the subscription so that we can
    # retrieve it later (in the case that a user hasn't yet signed up nor logged in,
    # and need to link this subscription to their account once they have).
    subscription.is_active = True
    subscription.latest_checkout_session_id = checkout_session_id

    # fetch from db instead of stripe object in case we have a product name override
    product = product_repository.get_by_id(session, product_id=stripe_price_id)
    product_name = product.name if product else "Unknown Product"

    create_event(
        session=session,
        title="Subscription started",
        description="Subscription created or updated",
        info={
            # "stripe_customer_id": stripe_customer_id,
            # "stripe_customer_name": stripe_customer.name,
            "stripe_customer_email": stripe_customer_email,
            "subscription_id": stripe_subscription_id,
            "stripe_product_id": stripe_price_id,
            "product_name": product_name,
        },
        account=wriveted_user,
        slack_channel=(
            None
            if checkout_session_id and "test" in checkout_session_id
            else EventSlackChannel.MEMBERSHIPS
        ),
        slack_extra={
            # "customer_name": stripe_customer.name,
            "customer_link": f"https://dashboard.stripe.com/customers/{stripe_customer_id}",
            # "subscription_link": f"https://dashboard.stripe.com/subscriptions/{stripe_subscription_id}",
            # "product_link": f"https://dashboard.stripe.com/products/{stripe_price_id}",
        },
        enable_processing=True,  # This will automatically handle background processing via EventOutbox
        external_notifications=True,  # This will ensure Slack alerts are sent
    )

    # Processing automatically handled by unified workflow via enable_processing=True

    # Send subscription welcome email via EventOutbox for reliable delivery
    if wriveted_parent_id is not None and stripe_customer_email:
        logger.info("Sending subscription welcome email via EventOutbox")

        email_data = {
            "from_email": "orders@hueybooks.com",
            "from_name": "Huey Books",
            "to_emails": [stripe_customer_email],
            "subject": "Your Huey Books Membership",
            "template_id": "d-fa829ecc76fc4e37ab4819abb6e0d188",
            "template_data": {
                "name": stripe_customer.name,
                "checkout_session_id": checkout_session_id,
            },
        }

        # Send as TRANSACTIONAL email - critical business email with 5 retries
        send_email_reliable_sync(
            db=session,
            email_data=email_data,
            email_type=EmailType.TRANSACTIONAL,
            user_id=str(wriveted_user.id) if wriveted_user else None,
        )
        # create_event above already committed the subscription; this queues the
        # welcome email as a separate outbox row that publish_event_sync does not
        # commit, so persist it before the session closes.
        session.commit()
    elif wriveted_parent_id is not None and not stripe_customer_email:
        logger.warning(
            "Skipping subscription welcome email - no customer email address available"
        )

    if school is not None:
        # Only a genuinely paid session activates a school. A completed session
        # can still be unpaid (async payment methods) or zero-charge (trial,
        # 100%-off promo), and must not unlock access.
        payment_status = event_data.get("payment_status")
        if payment_status == "paid":
            _activate_school_after_payment(session, school, stripe_customer_email)
        else:
            logger.warning(
                "School checkout completed but not paid; leaving school inactive",
                school=school.name,
                payment_status=payment_status,
            )

    return subscription


def _activate_school_after_payment(session, school: School, customer_email):
    """Mark a paid school ACTIVE and email the contact a confirmation/receipt.

    Idempotent: Stripe redelivers events, so an already-active school is a
    no-op (and does not re-send the receipt).
    """
    if school.state == SchoolState.ACTIVE:
        logger.info("School already active; ignoring duplicate", school=school.name)
        return

    school.state = SchoolState.ACTIVE
    session.add(school)

    onboarding = (school.info or {}).get("onboarding") or {}
    to_email = onboarding.get("contact_email") or customer_email
    if to_email:
        send_email_reliable_sync(
            db=session,
            email_data={
                "from_email": settings.BROADCAST_FROM_EMAIL,
                "from_name": "Huey Books",
                "to_emails": [to_email],
                "subject": f"{school.name} is live on Huey Books",
                "html_content": render_school_activated_html(
                    school.name, onboarding.get("contact_name")
                ),
            },
            email_type=EmailType.TRANSACTIONAL,
        )

    # publish_event_sync only adds the outbox row; persist activation + email.
    session.commit()
    logger.info(
        "School activated after payment",
        school=school.name,
        emailed=bool(to_email),
    )


def _handle_invoice_upcoming(session, event_data):
    """Remind an active school's contact that their subscription renews soon."""
    stripe_subscription_id = event_data.get("subscription")
    if not stripe_subscription_id:
        return
    subscription = subscription_repository.get_by_id(
        session, subscription_id=stripe_subscription_id
    )
    if subscription is None or not subscription.school_id:
        return
    school = school_repository.get_by_wriveted_id(session, str(subscription.school_id))
    if school is None or school.state != SchoolState.ACTIVE:
        return

    onboarding = (school.info or {}).get("onboarding") or {}
    to_email = onboarding.get("contact_email")
    if not to_email:
        return

    amount = event_data.get("amount_due")
    currency = (event_data.get("currency") or "aud").upper()
    amount_str = (
        f"${(amount or 0) / 100:.2f} {currency}" if amount is not None else None
    )
    renews_at = event_data.get("next_payment_attempt") or event_data.get("period_end")
    renewal_date = (
        datetime.utcfromtimestamp(renews_at).date().isoformat() if renews_at else None
    )

    send_email_reliable_sync(
        db=session,
        email_data={
            "from_email": settings.BROADCAST_FROM_EMAIL,
            "from_name": "Huey Books",
            "to_emails": [to_email],
            "subject": f"{school.name} — your Huey Books subscription renews soon",
            "html_content": render_school_renewal_reminder_html(
                school.name, onboarding.get("contact_name"), amount_str, renewal_date
            ),
        },
        email_type=EmailType.TRANSACTIONAL,
    )
    session.commit()
    logger.info("Sent school renewal reminder", school=school.name)


def _handle_subscription_created(
    session, wriveted_user: Optional[User], school: School | None, event_data: dict
):
    stripe_subscription_id = event_data.get("id")
    assert event_data.get("object") == "subscription"
    assert stripe_subscription_id is not None, "Subscription ID is required"

    stripe_subscription_status = event_data["status"]
    stripe_subscription_expiry = event_data["current_period_end"]

    # ensure our db knows about the specified product
    stripe_price_id = event_data["items"]["data"][0]["price"]["id"]
    _sync_stripe_price_with_wriveted_product(session, stripe_price_id)

    # If user is missing, look to see if the Stripe Customer's metadata includes `wriveted_id`
    if wriveted_user is None:
        stripe_customer = _get_stripe_customer_from_stripe_object(
            event_data, "subscription"
        )

        # check customer metadata for a wriveted user id
        # (this is stored upon the first successful checkout)
        if user_id := stripe_customer["metadata"].get("wriveted_id"):
            wriveted_user = crud.user.get(session, user_id)
            logger.info(
                "Found wriveted user id in Stripe Customer metadata", user=wriveted_user
            )

    wriveted_parent_id = (
        str(wriveted_user.id)
        if wriveted_user and wriveted_user.type == UserAccountType.PARENT
        else None
    )
    subscription_data = SubscriptionCreateIn(
        id=stripe_subscription_id,
        type=SubscriptionType.FAMILY if wriveted_parent_id else SubscriptionType.SCHOOL,
        is_active=stripe_subscription_status in {"active", "past_due"},
        product_id=stripe_price_id,
        stripe_customer_id=str(event_data.get("customer"))
        if event_data.get("customer")
        else "",
        parent_id=wriveted_parent_id,
        school_id=str(school.wriveted_identifier) if school else None,
        expiration=stripe_subscription_expiry,
    )

    logger.debug(
        "Creating subscription in our database", subscription_data=subscription_data
    )
    subscription, created = subscription_repository.get_or_create(
        session, subscription_data
    )
    if created:
        logger.info("Created a new subscription", subscription=subscription)


def _handle_subscription_updated(
    session, wriveted_user: Optional[User], school: School | None, event_data: dict
) -> Optional[Subscription]:
    stripe_subscription_id = event_data.get("id")
    assert event_data.get("object") == "subscription"

    stripe_subscription_status = event_data["status"]

    # ensure our db knows about the specified product
    stripe_price_id = event_data["items"]["data"][0]["price"]["id"]
    product = _sync_stripe_price_with_wriveted_product(session, stripe_price_id)

    # If user is missing, look to see if the Stripe Customer's metadata includes `wriveted_id`
    if wriveted_user is None:
        stripe_customer = _get_stripe_customer_from_stripe_object(
            event_data, "subscription"
        )

        # check customer metadata for a wriveted user id
        # (this is stored upon the first successful checkout)
        if user_id := stripe_customer["metadata"].get("wriveted_id"):
            wriveted_user = crud.user.get(session, user_id)
            logger.info(
                "Found wriveted user id in Stripe Customer metadata", user=wriveted_user
            )

    subscription = subscription_repository.get_by_id(
        session, subscription_id=stripe_subscription_id
    )
    if not subscription:
        logger.warning(
            "Ignoring subscription update event for missing subscription",
            subscription=subscription,
        )
        return

    # populate the subscription in our database with the latest information
    subscription.product_id = stripe_price_id
    subscription.is_active = stripe_subscription_status in {"active", "past_due"}
    subscription.expiration = datetime.utcfromtimestamp(
        event_data["current_period_end"]
    )

    if (
        wriveted_user
        and subscription.type == SubscriptionType.FAMILY
        and subscription.parent_id is None
    ):
        # we have a wriveted user, but no wriveted id on the subscription
        logger.info("Updating family subscription with Wriveted user id")
        subscription.parent_id = wriveted_user.id

    if (
        school
        and subscription.type == SubscriptionType.SCHOOL
        and subscription.school_id is None
    ):
        # we have a school, but no school id on the subscription
        logger.info("Updating school subscription with school id")
        subscription.school_id = school.wriveted_identifier

    # Use unified event workflow instead of direct crud.event.create
    create_event(
        session=session,
        title="Subscription updated",
        description="Subscription updated on Stripe",
        info={
            "product": product.name if product else "Unknown Product",
            "stripe_subscription_id": stripe_subscription_id,
            "product_id": stripe_price_id,
            "status": stripe_subscription_status,
        },
        school=school,
        account=wriveted_user,
        enable_processing=False,  # No processing needed for updates
        external_notifications=False,  # Internal accounting event
    )

    return subscription


def _handle_subscription_cancelled(
    session, wriveted_user: Optional[User], school: School | None, event_data: dict
):
    stripe_subscription_id = event_data.get("id")
    subscription = subscription_repository.get_by_id(
        session, subscription_id=stripe_subscription_id
    )

    if subscription is not None:
        logger.info("Marking subscription as inactive", subscription=subscription)
        product = subscription.product
        subscription.is_active = False
        if "ended_at" in event_data and event_data["ended_at"] is not None:
            subscription.expiration = datetime.utcfromtimestamp(event_data["ended_at"])

        # Deactivate the school this subscription paid for. The school is not in
        # the subscription.deleted payload (client_reference_id is only on
        # checkout sessions), so resolve it from our subscription record.
        if school is None and subscription.school_id:
            school = school_repository.get_by_wriveted_id(
                session, str(subscription.school_id)
            )
        if school is not None and school.state == SchoolState.ACTIVE:
            school.state = SchoolState.INACTIVE
            session.add(school)
            logger.info(
                "Deactivated school after subscription ended", school=school.name
            )

        # Use unified event workflow instead of direct crud.event.create
        create_event(
            session=session,
            title="Subscription cancelled",
            description=f"User cancelled their subscription to {product.name if product else 'Unknown Product'}",
            info={
                "stripe_subscription_id": stripe_subscription_id,
                "product_id": product.id if product else "unknown",
                "product_name": product.name if product else "Unknown Product",
                "cancellation_details": event_data.get("cancellation_reason", {}),
            },
            school=school,
            account=wriveted_user,
            slack_channel=EventSlackChannel.MEMBERSHIPS,  # Important business event - notify team
            enable_processing=False,  # No processing needed for cancellations
            external_notifications=True,  # Team should be notified of cancellations
        )
    else:
        logger.info(
            "Ignoring subscription cancelled event for unknown subscription (likely already removed)",
            stripe_subscription_id=stripe_subscription_id,
        )


def _sync_stripe_price_with_wriveted_product(
    session, stripe_price_id: str
) -> Optional[Product]:
    # Note multiple stripe events will all occur at essentially the same time.
    # We upsert into product table to avoid conflict

    logger.debug("Syncing Stripe price with Wriveted product")
    wriveted_product = product_repository.get_by_id(session, product_id=stripe_price_id)
    if not wriveted_product:
        logger.info("Creating new product in db")
        stripe_price = StripePrice.retrieve(stripe_price_id)
        stripe_product = StripeProduct.retrieve(stripe_price.product)

        product_repository.upsert(
            session, ProductCreateIn(id=stripe_price_id, name=stripe_product.name)
        )
        wriveted_product = product_repository.get_by_id(
            session, product_id=stripe_price_id
        )

        logger.info(
            "Created new product in db",
            product_id=stripe_price_id,
            product_name=wriveted_product.name
            if wriveted_product
            else "Unknown Product",
        )
    else:
        logger.debug(
            "Product already exists in db",
            product_id=stripe_price_id,
            product_name=wriveted_product.name
            if wriveted_product
            else "Unknown Product",
        )
    return wriveted_product
