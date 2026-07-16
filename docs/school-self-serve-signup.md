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
3. Activate    webhook checkout.session.completed (payment_status == "paid")
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

## Payment → activation (the money-gates-access rule)

`app/services/stripe_events.py`:
- `checkout.session.completed` / `checkout.session.async_payment_succeeded` →
  `_handle_checkout_session_completed`. A school is activated **only when
  `event_data["payment_status"] == "paid"`**. Completed-but-unpaid sessions
  (async payment methods, trials, 100%-off promos) do not activate.
- `_activate_school_after_payment` is idempotent: an already-ACTIVE school is a
  no-op and does not re-send the receipt (Stripe redelivers events).
- `customer.subscription.deleted` → the school is resolved from our
  `Subscription.school_id` (it is not in the Stripe payload) and set INACTIVE.
- `invoice.payment_failed` is logged only; Stripe runs its own dunning retries
  and the final give-up arrives as `customer.subscription.deleted`.

## Emails (all via the Resend outbox — ADR-007)

| Email | Trigger | To |
|-------|---------|----|
| Staff signup alert | onboarding submit | `STAFF_ALERT_EMAIL` (replaces Slack) |
| "Registered, subscribe to activate" | onboarding submit | school contact |
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
- `STRIPE_SCHOOL_PRICE_ID` — the flat school price (Supporter School).
- `STAFF_ALERT_EMAIL` — recipient for signup alerts, set per deployment; unset
  disables the alert.

## Known gaps / accepted trade-offs

- **Not single-transaction.** Subscription and school-state changes commit
  separately; a crash between them self-heals on Stripe's retry (activation is
  idempotent). Accepted for MVP.
- **No immediate dunning deactivation.** A failed renewal keeps the school
  active until Stripe cancels the subscription; then it deactivates. Tune the
  Stripe dunning policy if a harder cutoff is wanted.
- **Promo codes disabled** on checkout (`allow_promotion_codes` not set) — a
  100%-off code would be a no-charge activation path; re-enable deliberately with
  a policy.
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

## Stripe account cutover (hardbyte)

The school flow is parameterized by config, so going live is a
setup + secret/price swap, not code:

1. In the hardbyte Stripe account, create the school product/price (flat
   recurring) and a webhook endpoint at the internal API `/v1/stripe/webhook`
   with events: `checkout.session.completed`,
   `checkout.session.async_payment_succeeded`, `customer.subscription.deleted`,
   `invoice.upcoming`, `invoice.payment_failed`, plus the existing subscription
   events. `scripts/stripe_migrate.py` automates recreating the catalog and
   emits the old→new price-ID mapping.
2. Set the secrets/config: `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`,
   `STRIPE_SCHOOL_PRICE_ID` (from the mapping), `EMAIL_PROVIDER=resend`.
3. Manual e2e: register a school → hit `/checkout` → pay with a Stripe test
   card → confirm the school flips to ACTIVE and the receipt email sends.
