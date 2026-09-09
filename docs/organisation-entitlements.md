# Organisation feature entitlements

Multiple-library management is included in every existing paid school/library subscription. There is no additional tier, price, charge, or Stripe Checkout flow.

## Ownership and policy

An organisation owns its subscriptions and feature entitlements. Libraries own collections and local access. A subscription can belong to only one organisation; an organisation can have multiple subscriptions during renewal or consolidation.

The internal `organisation_subscriptions` association names the exact existing subscription, not a billing library or a shared Stripe customer. Existing `Subscription.school_id` remains billing/reader compatibility data. Moving or attaching libraries does not assign, transfer or replace subscription ownership. Reader activation, invoice consolidation and payment ownership changes remain separate work.

The `multiple_libraries` entitlement requires a current paid school/library subscription associated with the organisation: active, Stripe customer present, verified `paid_at`, unexpired period. Family, complimentary, unpaid and expired subscriptions do not qualify. Existing webhook updates to the same subscription are reflected immediately; a replacement subscription ID requires a reviewed association.

Free organisations can have one library; paid organisations retain the technical limit of 100. Admission is locked and enforced even for platform staff. On expiry, existing data and memberships remain: organisation-derived multi-library writes stop, while direct/home library permissions remain additive. Grant revocation remains available.

## Interface

Organisation list/detail responses contain only entitlement status, reason, library count and limit. There are no subscription IDs or billing controls in the library workspace. The billing-library endpoint and attachment sponsorship option have been removed.

For verified migration/support assignments, an operator with database credentials can run:

```sh
uv run python -m scripts.associate_organisation_subscription --organisation UUID --subscription SUBSCRIPTION_ID
# Review the target and ownership evidence before repeating with --apply.
```

The default is a rolled-back dry run. The operation is idempotent, locks the subscription, refuses family subscriptions and refuses transferring an existing owner. It neither charges anyone nor changes payment evidence. This is deliberately not available to organisation managers or through View As.

## Migration and rollback

Migration `c62f73b0a12d` copies subscriptions from previously explicit prototype billing links only. It does not infer ownership from arbitrary member libraries, domains or administrators. The old link table remains an inert rollback record; current application code never reads or writes it. Reconcile ownership before any old-revision rollback: downgrade refuses to discard populated organisation subscription associations.

Apply the migration before the API and UI. Existing paid demo associations are preserved; no customer payment records or reviewed labels are modified. New customer associations require verified ownership, not library nomination. Production rollout remains separately gated.

## Stripe integration

Stripe is the payment authority; existing signed webhooks maintain local payment evidence. Current products all include this feature, so synchronous Stripe calls or a second entitlement synchronisation add no value now. If products diverge, extend the entitlement resolver using verified organisation/subscription ownership rather than customer-wide features alone.

## Verification

Regression coverage includes free admission, concurrent capacity, lapse/direct-access retention, removed billing endpoint, paid-library attachment without ownership transfer, library reorganisation, multiple subscriptions, family rejection and idempotent operator assignment. UI tests verify staff and educators see subscription guidance without nomination controls.
