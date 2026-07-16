# Stripe Integration

Wriveted integrate with Stripe via webhooks.

## Stripe Webhooks

Stripe webhooks are used to notify Wriveted when events relating to subscriptions, customers and payments occur.

The Wriveted API includes a webhook that receives Stripe events and updates the relevant Wriveted data.


## Local Testing

To test Stripe webhooks locally, you can use [Stripe CLI](https://stripe.com/docs/stripe-cli). This allows you to send Stripe events to your local running Wriveted API.

Example of sending a customer.subscription.created event to the local API

First set up the Stripe CLI to use your Stripe API keys:

    stripe login

Then start the Stripe CLI webhook proxy:

    stripe listen --forward-to localhost:8000/v1/stripe/webhook

Note this will print out a local webhook secret. You will need to set this as the `STRIPE_WEBHOOK_SECRET` environment variable.

Then send a test event to the Stripe CLI webhook proxy:
    
    stripe trigger customer.subscription.created --override customer:email=brian@hardbyte.nz --override customer:metadata.wriveted_id=83a889bf-5722-4c35-8d81-224cc600e394

    stripe trigger customer.subscription.deleted --override customer:email=brian@hardbyte.nz --override customer:metadata.wriveted_id=83a889bf-5722-4c35-8d81-224cc600e394


## Non Production Environments


https://dashboard.stripe.com/test/webhooks

## Stripe Webhook Events


### customer.created

When a new Stripe customer is created we link to the Wriveted User.
Adding a `stripe_customer_id` to the User's `info` object.

### customer.subscription.created

When a customer subscribes to a plan, a customer.subscription.created event is sent to the webhook. 


### customer.subscription.deleted

When a customer unsubscribes from a plan, a customer.subscription.deleted event is sent to the webhook and we mark the `User.is_active` to `False`. For a **school** subscription we also resolve the school from our `Subscription.school_id` and set the school `INACTIVE` (the school is not in this payload).

### checkout.session.completed / checkout.session.async_payment_succeeded

Primary "someone paid" signal. Creates/updates the subscription and, for a
**school** subscription, activates the school — but **only when
`payment_status == "paid"`** (a completed session can be unpaid for async
payment methods, trials, or 100%-off promos). Activation is idempotent across
Stripe's redeliveries.

### invoice.upcoming

Fires ahead of a renewal charge. For an active school we email the contact a
renewal reminder (amount + date from the invoice).

### invoice.payment_failed

Logged only. Stripe runs its own dunning retries; the final give-up arrives as
`customer.subscription.deleted`, which deactivates the school.

## School self-serve paid signup

`POST /v1/school/{wriveted_identifier}/checkout` creates a Checkout Session
(subscription mode) for `STRIPE_SCHOOL_PRICE_ID`, scoped to the school via
`client_reference_id` so an admin or a sponsor can pay it. Payment gates
activation via the webhook above. Full design + the account-cutover checklist:
[school-self-serve-signup.md](./school-self-serve-signup.md).

