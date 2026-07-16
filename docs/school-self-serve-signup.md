# Self-Serve Paid School Signup

How a school signs itself up, pays, and gets activated end to end, self-serve.
Payment gates activation. See ADR-008 for the decision rationale and ADR-007 for
the email backend.

## Flow

```
1. Register    POST /v1/onboarding/school   -> School(state=PENDING), user promoted to SchoolAdmin
                                             -> email: staff alert + "registered, subscribe to activate"
2. Pay         POST /v1/school/{id}/checkout -> Stripe Checkout Session URL (subscription mode)
                                             -> admin pays, or forwards the URL to a sponsor
3. Activate    webhook checkout.session.completed (paid or fully comped)
                                             -> School(state=ACTIVE) + receipt email
4. Renew       webhook invoice.upcoming      -> renewal reminder email to the contact
5. Lapse       webhook customer.subscription.deleted -> School(state=INACTIVE)
```

School state machine: `PENDING --paid--> ACTIVE --cancelled/ended--> INACTIVE`.
`ACTIVE` is what gates login/access (`app/api/auth.py`).

## Endpoints

- `POST /v1/onboarding/school` (`app/api/onboarding.py`) — existing; creates the
  PENDING school, promotes the caller to SchoolAdmin, and best-effort queues the
  signup emails (a failure here does **not** fail the signup — the school is
  committed first).
- `POST /v1/school/{wriveted_identifier}/checkout` (`app/api/schools.py`) —
  new; `Permission("update", ...)` (school admin or superuser). Returns
  `{ "checkout_url": ... }`. Stripe SDK call is offloaded off the event loop
  (`app/services/school_billing.py`).
- `POST /v1/school/{wriveted_identifier}/staff` + `GET .../staff`
  (`app/api/schools.py`) — a school admin adds a teacher (or admin) colleague by
  email and lists staff. The colleague is created as an educator bound to the
  school and is linked to their account by email on first sign-in (an email that
  already has an account is rejected with 409). An invite email is sent.

## Payment → activation (the money-gates-access rule)

`app/services/stripe_events.py`:
- `checkout.session.completed` / `checkout.session.async_payment_succeeded` →
  `_handle_checkout_session_completed`. A school is activated when
  `payment_status` is `paid` (charged) or `no_payment_required` (a fully-comped
  100%-off promo, or a trial — both intentional grants of access). An `unpaid`
  session (async payment not yet cleared) does **not** activate; it waits for
  `checkout.session.async_payment_succeeded`. Activation happens in the same
  transaction as the subscription (before the `create_event` commit).
- `_activate_school_after_payment` is idempotent: an already-ACTIVE school is a
  no-op and does not re-send the receipt (Stripe redelivers events).
- `customer.subscription.deleted` → the school is resolved from our
  `Subscription.school_id` (it is not in the Stripe payload) and set INACTIVE.
- `invoice.payment_failed` is logged only; Stripe runs its own dunning retries
  and the final give-up arrives as `customer.subscription.deleted`.

## Emails (all via the Resend outbox — ADR-007)

| Email | Trigger | To |
|-------|---------|----|
| Staff signup alert | onboarding submit | `STAFF_ALERT_EMAILS` (replaces Slack) |
| "Registered, subscribe to activate" | onboarding submit | school contact |
| Staff invite | staff added to a school | the invited colleague |
| "Your school is live" + receipt | paid activation | school contact / payer |
| Renewal reminder | `invoice.upcoming` | school contact |

Copy is cadence-agnostic (no hardcoded "annual"); the renewal reminder pulls the
amount and date from the invoice. Renderers live in
`app/services/school_emails.py` and are unit-tested.

### Planned: parent "contribute a month"
The checkout is payer-agnostic, so the sponsorship path already works (forward
the school checkout URL to a parent/library). The planned extension is a
parent-facing offer — a one-off "sponsor a month" price plus an email/broadcast
to a school's parents — that reuses the same school-scoped checkout. Not built.

## Sponsorship

The Checkout Session is scoped to the school (`client_reference_id = school
wriveted_identifier`), never to the payer. Anyone who completes it funds that
school's subscription; the subscription attaches to the school regardless of who
paid. Bulk multi-school sponsorship in one transaction is future work.

## Config

- `EMAIL_PROVIDER` — `resend` (prod) or `sendgrid` (fallback).
- `STRIPE_SCHOOL_PRICE_IDS` — offerable school price ids (comma-separated or
  JSON); the first is the default. Checkout accepts an optional `price_id` that
  must be one of these.
- `STAFF_ALERT_EMAILS` — recipients for signup alerts (comma-separated or JSON),
  set per deployment; empty disables the alert.
- `SCHOOL_ADMIN_URL` — where a school admin manages their school; used for links
  in the activation and staff-invite emails.

## Known gaps / accepted trade-offs

- **No immediate dunning deactivation.** A failed renewal keeps the school
  active until Stripe cancels the subscription; then it deactivates. Tune the
  Stripe dunning policy if a harder cutoff is wanted.
- **Dead ACL rule**: `School` grants `(Allow, "school:{id}", "update")` but no
  principal is ever `school:{id}`; checkout is effectively admin/superuser only.
  Harmless but misleading — remove or correct when touching `app/models/school.py`.

## Testing

- Unit: `app/tests/unit/test_school_signup.py` (email renderers, checkout param
  building, missing-price error).
- Integration: `app/tests/integration/test_school_activation.py` — paid
  activates + emails once, unpaid does not activate, duplicate event does not
  re-email, cancellation deactivates. Run with
  `bash scripts/integration-tests.sh -k school_activation`.

## Going live

The school flow is parameterized by config, so going live is a
setup + secret/price change, not code:

1. In Stripe, create the school product/price(s) and a webhook endpoint at the
   internal API `/v1/stripe/webhook` with events: `checkout.session.completed`,
   `checkout.session.async_payment_succeeded`, `customer.subscription.deleted`,
   `invoice.upcoming`, `invoice.payment_failed`, plus the existing subscription
   events.
2. Set the secrets/config: `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`,
   `STRIPE_SCHOOL_PRICE_IDS`, `EMAIL_PROVIDER=resend`, and `STAFF_ALERT_EMAILS`.
3. Manual e2e: register a school → hit `/checkout` → pay with a Stripe test
   card → confirm the school flips to ACTIVE and the receipt email sends.
