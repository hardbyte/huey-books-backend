# School billing and access

School access is resolved locally from durable billing attempts, paid Stripe
subscriptions, and bounded complimentary grants. Stripe remains the authority
for payment objects; `GET /v1/school/{id}/billing-status` is the authority for
Huey's user interface and access decisions.

## Access precedence

| Priority | Local evidence | Access |
| --- | --- | --- |
| 1 | Active Stripe subscription with `paid_at` and an unexpired period | Paid until the period end |
| 2 | Unexpired `invoice_pending` grant | Active during invoice terms |
| 3 | Unexpired staff, invite, or contribution grant | Active until the grant expires |
| 4 | Pre-migration active Stripe row awaiting paid-state initialization | Existing access until its recorded period end; not considered paid |
| 5 | No qualifying entitlement | Inactive |

Stripe's `subscription.status=active` is not proof that a manually collected
invoice was paid. Only `invoice.paid` sets `paid_at`. `School.state` is a cached
projection recomputed from the table above; it is not a billing source of truth.

## API

- `GET /v1/school/{id}/billing-status` returns the entitlement, latest billing
  attempt, paid subscription, available actions, country-selected offer, and
  card/invoice ordering. School billing permission is required.
- `POST /v1/school/{id}/checkout` starts or replays a card attempt.
- `POST /v1/school/{id}/invoice-subscription` starts or replays a net-terms
  invoice attempt.
- `POST /v1/school/{id}/billing-portal` creates a short-lived Stripe portal
  session only when the school has a paid subscription and a dedicated school
  billing customer.

Both start commands accept an `Idempotency-Key` header. Clients keep one UUID
per school and payment method while retrying. The database admits one open
collectible attempt per school. Each Stripe POST uses an operation-specific key
derived from the attempt UUID, so a retry cannot duplicate a Customer, Checkout
Session, Subscription, or invoice update. A `CREATING` attempt has a 23-hour
Stripe idempotency safety window. If it is still ambiguous after that window it
remains admission-blocking for staff review: it is never swept or blindly
replayed after Stripe may have pruned the key, because either action could
create a second collectible object. Invoice terms are applied only after Stripe
creates the subscription. Both commands return the same persisted
attempt shape (`attempt_id`, `method`, `status`, and any available Checkout or
hosted-invoice URL); clients do not infer billing state from HTTP success alone.
The aggregate also returns a machine-readable `blocking_reason` and structured
recurring-price display data so clients can explain blocked states and disclose
the full commitment before creating an invoice.

The dedicated `school_billing_accounts` customer must not be inferred from old
subscription rows: the historical payer may be a parent or sponsor whose portal
must not be exposed to a school administrator.

## Stripe event transitions

| Event | Durable transition |
| --- | --- |
| `checkout.session.completed`, `checkout.session.async_payment_succeeded` | Correlate the attempt; mark paid only for a successful/no-payment-required session; persist the subscription and recompute access |
| `checkout.session.expired`, `checkout.session.async_payment_failed` | Mark only the matching attempt terminal |
| `invoice.finalized` | Store the hosted invoice URL; payment remains pending |
| `invoice.paid` | Set `paid_at`, mark the attempt paid, retire superseded grants, recompute access |
| `invoice.finalization_failed` | Keep the attempt and net-terms access open, record the actionable error, and wait for correction/retry or bounded expiry |
| `invoice.voided`, `invoice.marked_uncollectible` | Mark the matching attempt terminal, retire its pending grant, recompute without disturbing unrelated grants |
| `customer.subscription.created`, `customer.subscription.updated` | Refresh Stripe status without inventing payment evidence |
| `customer.subscription.deleted` | Retire that subscription and recompute from any replacement paid subscription or live grant |

The public webhook verifies Stripe's signature and queues the event envelope.
The worker records `event.id` in `stripe_event_receipts` in the same transaction
as the domain transition and audit event. Duplicate IDs are ignored. Event
timestamps prevent an older delivery from overwriting newer state; handlers can
retrieve current Stripe objects when an event payload is insufficient. Event
delivery order is never assumed. Tasks queued by the previous producer may omit
the event ID during the first deployment; this temporary compatibility path is
not receipt-deduplicated and can be removed after the task queue has drained.

Failed webhook deliveries can be replayed from Stripe Workbench. Because the
receipt claim and transition commit atomically, a failed transition leaves no
receipt and the same event ID can be retried safely. For a missed historical
paid event on a pre-rollout subscription, run the paid-state initialization
`uv run python -m scripts.reconcile_school_billing --school-id <uuid>` first,
then repeat with `--apply` after reviewing the paid latest-invoice evidence.
This command only initializes subscriptions without `paid_at`; it is not a
general bidirectional Stripe reconciliation or a revocation tool.

## Operations and rollout

Production webhook endpoint `we_1TtloVJn6IrlrObosBV99lhX` currently uses the
account-default API version. This release uses Stripe Python 15.5.1 and targets
event version `2026-07-29.dahlia`; test that version, pin the endpoint to it in
Workbench, and enable every event in the table. Do not silently follow a future
SDK default: an API version change is its own staged compatibility change. In
particular, the current endpoint must add
`checkout.session.expired`, `checkout.session.async_payment_failed`,
`invoice.finalized`, `invoice.finalization_failed`, `invoice.voided`, and
`invoice.marked_uncollectible`. `invoice.created` is intentionally not consumed:
Huey does not delay or mutate invoice finalization from that event. The received
API version is retained for audit; the application does not reject a different
version, so endpoint changes require a staged code review and test delivery.
Validate the live endpoint after changing it with:

```sh
uv run python -m scripts.check_stripe_school_billing \
  --endpoint-id we_1TtloVJn6IrlrObosBV99lhX \
  --expected-api-version 2026-07-29.dahlia
```

The same checker validates every configured Stripe school Price against the
amount, currency, and recurring interval disclosed by `billing-status`. The
current offers are A$240 yearly by default and A$80 yearly for schools in India;
a price or display change must update both Stripe and deployment configuration
in one reviewed rollout.

Stripe invoice emails/reminders and the customer portal must be enabled and
tested separately in live mode. Deliver a signed test event after any webhook
secret rotation to prove the deployed secret matches; also check Workbench for
duplicate enabled production endpoints. The daily authenticated
`/v1/maintenance/lapse-expired-schools` scheduler expires resumable abandoned
attempts, bounded grants, and locally expired paid/legacy periods in batches,
then recomputes school access. Ambiguous `CREATING` attempts remain for review.
Infrastructure
alerts both on exhausted Scheduler retries and on 26 hours without a successful
sweep. The latter is a liveness signal; it does not prove that every expired
entitlement was semantically classified correctly.

Deploy in this order:

1. Dry-run the historical paid-state initialization against production with
   `uv run python -m scripts.reconcile_school_billing` and review every
   skipped/failed row before merging. A skipped row remains an explicit legacy
   entitlement for manual follow-up; it is never promoted to paid without a paid
   latest invoice.
2. Deploy the backend pipeline. It migrates each database before its service is
   updated; for production it then initializes verified historical paid invoices and aborts
   on Stripe/API failures before updating either Cloud Run service. The
   migration deliberately leaves historical `paid_at` values empty rather than
   inferring payment from a Checkout Session or subscription status. Historical
   Checkout access remains explicitly labelled `legacy_subscription` until
   paid-state initialization or staff follow-up.
3. Confirm the lapse scheduler, webhook event selection and pinned API version,
   invoice emails/reminders, and portal configuration.
4. Deploy the admin UI and consumer site using `billing-status`. The backend is
   an enforced prerequisite; the frontends do not fall back to legacy
   subscription rows or the retired Stripe Pricing Table.
5. Exercise card success/cancel, invoice finalize/pay/void, comp conversion,
   duplicate delivery, and retry paths in Stripe test mode before live payment.

The implementation follows Stripe's guidance on [idempotent POST
requests](https://docs.stripe.com/api/idempotent_requests), [duplicate and
unordered webhook delivery](https://docs.stripe.com/webhooks), [subscription
webhooks and invoice payment](https://docs.stripe.com/billing/subscriptions/webhooks),
and [webhook API versioning](https://docs.stripe.com/webhooks/versioning).
