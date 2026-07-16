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
- `POST /v1/school/{wriveted_identifier}/contribute` (`app/api/schools.py`) —
  new; a one-off "contribute a month" payment toward a school (see below).
  Returns `{ "checkout_url": ... }`. Auth is **any authenticated active user or
  service account** (not `Permission("update", ...)`), so a sponsor who is not
  the school admin — a parent, public supporter, or library — can pay a shared
  link. The school is resolved by `wriveted_identifier` (404 if missing). Stripe
  SDK call is offloaded off the event loop.

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
| "Thank you for contributing" | contribution paid | the contributing supporter |
| "A supporter contributed" | contribution paid | school contact (if different) |

The two contribution emails state the accurate outcome — "credited your next
invoice", "activated your school through &lt;date&gt;", or "extended your access
to &lt;date&gt;".

Copy is cadence-agnostic (no hardcoded "annual"); the renewal reminder pulls the
amount and date from the invoice. Renderers live in
`app/services/school_emails.py` and are unit-tested.

### Contribute a month

A parent, public supporter, or library can contribute toward a specific
school's subscription via a one-off payment. Like the school checkout, it is
payer-agnostic and scoped to the school, so a shareable link works.

```
1. Contribute  POST /v1/school/{id}/contribute -> one-off Stripe Checkout URL (mode=payment)
                                                -> supporter pays, or admin forwards the URL
2. Credit/     webhook checkout.session.completed (metadata kind=school_contribution, paid)
   grant        -> school has an active Stripe subscription: customer-balance credit
                -> otherwise: create/extend a bounded comped grant + activate school
                -> emails: accurate thank-you to payer (+ notice to school)
3. Lapse       POST /v1/maintenance/lapse-expired-schools (Cloud Scheduler)
                -> sets INACTIVE any school whose comped grant expired and that has
                   no live Stripe subscription
```

- Endpoint: `app/api/schools.py :: create_school_contribution`.
- Checkout builder: `app/services/school_billing.py ::
  create_school_contribution_checkout_session` — `mode="payment"`,
  `client_reference_id = school.wriveted_identifier`, and
  `metadata={"kind": "school_contribution", "wriveted_school_id": ...,
  "school_name": ...}`. Offloaded via `asyncio.to_thread`.

#### Webhook handling (the crediting model)

`app/services/stripe_events.py`:
- `process_stripe_event` short-circuits contribution checkouts **strictly by
  `metadata.kind == "school_contribution"`** (not by `mode`, which would swallow
  any future one-off checkout) **before** the customer-centric extraction,
  because a one-off (guest) checkout may have no Stripe customer. The school is
  resolved directly from `client_reference_id`.
- `_handle_contribution_checkout_completed` gates on `payment_status == "paid"`,
  then applies the crediting model:
  - **School has an active auto-renewing Stripe subscription** (determined by a
    direct query for an active subscription with a non-empty `stripe_customer_id`,
    not the ambiguous one-to-one relationship) → the contributed `amount_total`
    is applied as a **Stripe customer-balance credit** on that subscription's
    customer (`create_balance_transaction(amount=-amount_total, currency=...,
    idempotency_key=...)`), reducing the next renewal invoice. The school state is
    unchanged. Any leftover comped grant is retired (see below). Failure handling:
    a **permanent** error — currency mismatch (validated against the customer's
    balance currency first) or a Stripe `InvalidRequestError` — **fails soft**
    (logged, recorded `credit_failed`); a **potentially transient** error (rate
    limit, connection, API error) is **re-raised** so the idempotency claim is
    rolled back and Cloud Tasks retries — the `idempotency_key` makes that retry
    safe from double-crediting.
  - **School has no such subscription** → the contribution buys a **bounded paid
    grant proportional to the amount paid**. A first-class comped `Subscription`
    is created — id `comp_contribution_<wriveted_id>`, `type=SCHOOL`,
    `stripe_customer_id=""`, `info={"source": "contribution_grant"}` (so it is
    clearly distinguishable from an auto-renewing Stripe subscription),
    `expiration = now + grant_days` — and the school is activated. If a comped
    grant already exists the expiry is **extended** by the newly-computed days
    (contributions stack: from the current expiry if still in the future,
    otherwise from now).

#### Grant vs Stripe subscription (one active row)

A comped grant and a real Stripe subscription are separate `subscriptions` rows.
When a school gains/uses a real auto-renewing Stripe subscription (contribution
credit path, subscription checkout, or `customer.subscription.created`), any
active comped grant is **retired** (`is_active=False`) so the school never has two
*active* subscription rows. Because a retired (inactive) grant row still exists,
`School.subscription` (uselist=False) is ordered `is_active desc, expiration desc`
so it deterministically resolves to the live subscription; the retired row is kept
as an audit trail and is not orphan-deleted. Conversely,
`customer.subscription.deleted` does **not** deactivate a school that still has an
active, unexpired comped grant.

The `GET /schools?has_active_subscription=` staff filter means **paying**: it
matches via an `EXISTS` on an active subscription with a non-empty
`stripe_customer_id`, so comped grants are excluded and a school with multiple
subscription rows is not duplicated.

#### Pay-what-you-want + proportional access

Contributions are pay-what-you-want: the Stripe price uses `custom_unit_amount`,
so the payer enters any amount (≥ the price's minimum) and
`checkout.session.amount_total` varies per checkout. The credit path credits the
actual `amount_total`. The grant path converts the amount into a proportional
number of days against a notional monthly rate:
`grant_days = max(1, min(round(amount_total / SCHOOL_CONTRIBUTION_MONTHLY_CENTS * 30), 3650))`
(default rate 2500 = $25/mo, so $50 ≈ 60 days, $240 ≈ ~a year; clamped to 10 years
so an absurd amount can't overflow the expiry datetime and wedge the webhook).

#### Bounded-grant semantics + lapse

The comped grant has no Stripe subscription behind it, so it does **not**
auto-renew and there is no `customer.subscription.deleted` to lapse it. Instead,
`POST /v1/maintenance/lapse-expired-schools` (`app/api/internal`) sweeps expired
grants: for each active `contribution_grant` subscription past its expiry it marks
the grant inactive and, unless the school has a live auto-renewing Stripe
subscription (which Stripe drives), sets the school `INACTIVE`. A Cloud Scheduler
job drives this on a cadence (wired in the infrastructure repo, not here).

Trade-off: access is granted for a bounded, amount-proportional window and lapses
cleanly after expiry, rather than persisting indefinitely. The contribution is
never left as a dangling unusable credit: with no customer to credit, its value is
realised as a grant.

#### Idempotency

Each contribution claims its checkout session id in
`stripe_contribution_receipts` (PK = `checkout_session_id`) via
`INSERT ... ON CONFLICT DO NOTHING` **before** doing any work. Concurrent Stripe
redeliveries serialise on that key, so only the first delivery activates/extends
and emails; the rest are full no-ops. The balance-credit call additionally passes
a Stripe `idempotency_key` (`contribution-{session_id}`) so money can't be
double-credited even independent of the receipt row.

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
- `STRIPE_SCHOOL_CONTRIBUTION_PRICE_IDS` — one-off "contribute a month" price
  ids (comma-separated or JSON); the first is the default. `/contribute` accepts
  an optional `price_id` that must be one of these. These must be **one-time**
  Stripe prices used in `mode="payment"`, configured with `custom_unit_amount`
  (pay-what-you-want).
- `SCHOOL_CONTRIBUTION_MONTHLY_CENTS` — notional monthly rate (minor units,
  default 2500) used to convert a pay-what-you-want contribution into a
  proportional access grant (`grant_days = round(amount / this * 30)`) for a
  school with no auto-renewing Stripe subscription. Contributions stack.
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

- Unit: `app/tests/unit/test_school_signup.py` (email renderers, checkout and
  contribution param building, missing/unknown-price errors).
- Integration: `app/tests/integration/test_school_activation.py` — paid
  activates + emails once, unpaid does not activate, duplicate event does not
  re-email, cancellation deactivates.
- Integration: `app/tests/integration/test_school_contribution.py` — routing
  matches only on metadata kind; no-subscription contribution creates a bounded
  proportional grant and activates; larger amounts grant more days; a repeat
  contribution extends by the proportional days; an active Stripe subscription
  gets a balance credit; currency mismatch and a permanent Stripe error fail soft
  while a transient error re-raises (no committed claim); duplicate events are
  no-ops; the grant→Stripe-subscription conversion retires the grant (one active
  row, grant row preserved); a cancelled Stripe sub keeps a school active if it
  has a live grant; the `has_active_subscription` filter excludes comps and
  de-dupes; and the lapse sweep deactivates expired grants but not schools with a
  live Stripe subscription. Run with
  `bash scripts/integration-tests.sh -k "school_activation or school_contribution"`.

## Going live

The school flow is parameterized by config, so going live is a
setup + secret/price change, not code:

1. In Stripe, create the school product/price(s) and a webhook endpoint at the
   internal API `/v1/stripe/webhook` with events: `checkout.session.completed`,
   `checkout.session.async_payment_succeeded`, `customer.subscription.deleted`,
   `invoice.upcoming`, `invoice.payment_failed`, plus the existing subscription
   events.
   The `checkout.session.completed` event already covers contributions (same
   event; distinguished by the `metadata.kind` marker); no extra webhook event is
   needed. For contributions paid via bank transfer/other delayed methods also
   enable `checkout.session.async_payment_succeeded`.
2. Set the secrets/config: `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`,
   `STRIPE_SCHOOL_PRICE_IDS`, `STRIPE_SCHOOL_CONTRIBUTION_PRICE_IDS`,
   `SCHOOL_CONTRIBUTION_MONTHLY_CENTS` (optional, default 2500),
   `EMAIL_PROVIDER=resend`, and `STAFF_ALERT_EMAILS`. The contribution price(s)
   must be configured in Stripe with `custom_unit_amount` (pay-what-you-want).
3. Wire a Cloud Scheduler job (OIDC, background-tasks service account) to
   `POST /v1/maintenance/lapse-expired-schools` on the internal API so bounded
   contribution grants lapse after expiry (done in the infrastructure repo).
4. Manual e2e: register a school → hit `/checkout` → pay with a Stripe test
   card → confirm the school flips to ACTIVE and the receipt email sends.
   Then hit `/contribute` on a school with no subscription → confirm a comped
   grant is created and the school activates.
