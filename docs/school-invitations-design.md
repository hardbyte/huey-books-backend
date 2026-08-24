# School Invitations (referral → free trial access)

First-pass design. Lets an **active, paying** school refer another school; the
invited school gets a configurable free-access period (default 3 months) when
its admin **accepts** (signs in / activates). Invited-only (no referrer reward
in v1). Includes staff management + growth metrics.

## Decisions (locked)
- Initiator: an existing school admin of an **ACTIVE + paying** school.
- Grant timing: **on acceptance** (clock starts when the invited admin activates).
- Duration: **configurable**, default 90 days; per-invite override allowed.
- Cap: **3 invites per paying school** (configurable), counting SENT + ACCEPTED
  (EXPIRED/REVOKED free a slot).
- Invitable target: any school **not already active on Huey** (reference/INACTIVE
  school selected from search, or a brand-new school) — never one that already
  has access.
- Persistence: dedicated `school_invitations` table (+ Alembic migration).
- Scope: Phase 1 (loop) **and** Phase 2 (staff management + metrics). No referrer
  reward.

## Reused foundations
- **Comp grant**: refactor `_apply_or_extend_contribution_grant`
  (`app/services/stripe_events.py`) into `grant_school_access(session, school,
  days, *, source)` → synthetic `Subscription` (`is_active`, `expiration=now+days`,
  empty `stripe_customer_id`, comp product), flips `school.state=ACTIVE`, stacks.
  Contributions and invites both call it (`source` = `"contribution"` | `"invite"`;
  distinct grant ids `comp_contribution_<uuid>` / `comp_invite_<uuid>`).
- **Access gate**: school is usable when `state=ACTIVE` + an active `subscription`
  row exists; comps are excluded from the "paying" filter
  (`school_repository.has_active_subscription`) — invited schools show as
  *active, not paying*. No KPI distortion.
- **School resolve/create + promote-to-SchoolAdmin + educator binding**:
  `app/api/onboarding.py` logic (extract a shared helper).
- **Auth for the invited admin**: existing magic-link / SSO signup (`create:true`).
- **Email send**: `send_email_reliable` + `render_school_*` templates.
- **School search** (frontend) for picking the invited school.

## Data model — `school_invitations`
| column | type | notes |
|---|---|---|
| id | uuid PK (gen_random_uuid) | |
| token | str unique, indexed | secure random (secrets.token_urlsafe); the email link |
| inviter_school_id | uuid FK schools.wriveted_identifier, idx | referring school |
| inviter_user_id | uuid FK users.id | admin who sent it |
| invited_school_id | uuid FK schools.wriveted_identifier, nullable, idx | set if a reference school was chosen; else resolved/created on accept |
| invited_school_name | str(300) | display + creation |
| country_code | str(3) FK countries | for creation/search |
| invited_contact_email | str, idx | recipient |
| invited_contact_name | str nullable | |
| grant_days | int | snapshot (default INVITE_GRANT_DAYS) |
| status | enum SENT/ACCEPTED/EXPIRED/REVOKED | |
| created_at / updated_at / accepted_at(null) / expires_at | datetime | expires_at = created + INVITE_EXPIRY_DAYS |
| redeemed_subscription_id | str nullable | the grant created on accept |
| info | JSONB nullable | extensibility |

Indexes: unique(token); (inviter_school_id); (invited_school_id);
(invited_contact_email). No hard DB uniqueness on "one grant per invited school"
— enforced in service logic (a school may legitimately be invited by several,
but only the first accept grants).

## Config (app/config.py)
- `INVITE_GRANT_DAYS: int = 90`
- `INVITE_MAX_PER_SCHOOL: int = 3` (SENT+ACCEPTED per inviter)
- `INVITE_EXPIRY_DAYS: int = 30`
- `INVITE_REQUIRE_PAYING_INVITER: bool = True`

## Service layer (`app/services/school_invitations.py`)
- `create_invitation(session, inviter_school, inviter_user, *, invited_ref)` →
  validates then persists + returns row. Validations:
  1. inviter is ACTIVE and (if `INVITE_REQUIRE_PAYING_INVITER`) has a paying
     subscription (`has_active_subscription`);
  2. inviter's SENT+ACCEPTED count < `INVITE_MAX_PER_SCHOOL`;
  3. target ≠ inviter (no self-invite);
  4. target school (if resolvable) is not already ACTIVE / on Huey;
  5. contact email is not already an admin of an active school;
  6. no existing ACCEPTED invite for the same target school.
- `accept_invitation(session, token, user)` → (idempotent-ish):
  1. load by token FOR UPDATE; must be SENT and not past `expires_at`
     (else EXPIRED/error);
  2. resolve invited school (existing `invited_school_id`, else create from
     name+country via the shared onboarding helper); re-check not already ACTIVE;
  3. promote `user`→SchoolAdmin + bind to school (shared helper);
  4. `grant_school_access(school, invite.grant_days, source="invite")`;
  5. mark ACCEPTED, set accepted_at + redeemed_subscription_id; send activation
     email; event for metrics.
- `revoke_invitation`, `expire_stale` (lazy on list + optional scheduled sweep).

## API (`app/api/schools.py` / new `app/api/invitations.py`)
- `POST /v1/school/{wriveted_id}/invitations` — auth: SchoolAdmin of that school. Body:
  `{invited_school_wriveted_id?, invited_school_name?, country_code?, contact_email,
   contact_name?, grant_days?}`. 201 → invitation.
- `GET /v1/school/{wriveted_id}/invitations` — inviter's list + statuses.
- `POST /v1/invitations/{token}/revoke` — inviter revokes a SENT invite.
- `GET /v1/invitations/{token}` — minimal public preview (inviter school name,
  invited school name, grant months, status) for the accept page.
- `POST /v1/invitations/{token}/accept` — auth: signed-in user (the invited admin).
- Staff (Phase 2): `GET /v1/admin/invitations` (all, filters) + revoke; behind
  WRIVETED admin scope.

## Emails (`app/services/school_emails.py`)
- `render_school_invite_html(inviter_school, invited_school, accept_url,
  grant_months)` — "‹Inviter› invited your school to try Huey Books free for N
  months" + CTA.
- On accept: reuse/adapt `render_school_activated_html` mentioning the free period.

## Frontend — consumer app (`hueybooks.com`)
- Inviter dashboard: **"Invite a school"** — reuses the country+name school search,
  contact fields, optional duration; shows sent invites + status. Visible only to
  paying-active school admins.
- Invited: `/school/invited?token=…` — preview ("‹Inviter› invited you, N months
  free") → sign in / create account (existing) → accept → success ("live until
  <date>"). Reuses signup/activate pages.

## Admin UI (Phase 2, staff)
- Invitations page: table (inviter, invited, contact, status, dates), revoke,
  funnel tiles (sent / accepted / conversion, invited-schools-activated).

## Metrics
- Emit `events` on send + accept; a KPIs query for sent/accepted/conversion.

## Abuse / integrity
- Paying-inviter gate; ≤3 per inviter; single-use expiring token; no self-invite;
  can't invite an already-active school or an email that already admins an active
  school; first accept wins (one grant per invited school); concurrent-accept safe
  via FOR UPDATE + status check.

## Edge cases
- New vs reference invited school resolved on accept (mirror onboarding).
- Invited school later pays → real subscription coexists with the comp; fine.
- Inviter lapses after sending → invite still honoured (valid when sent).
- Grant stacking: an invited school can't get a second invite-grant.

## Test plan
- Unit: `grant_school_access` (create/extend/source); invite validations (paying,
  cap, self, already-active, dup); accept (grant issued, ACTIVE, status,
  idempotency); expiry.
- Integration: send→accept happy path; cap enforcement; expired token; revoke.

## Rollout
- Backend migration + code + config (env: leave defaults; enable per deploy).
- Feature-flag the inviter UI (show only to paying-active admins — natural gate).
- Reversible: comp grants tagged `source=invite`; can be bulk-retired.

---

## Revisions from design review (incorporated)

**Grant architecture (revised — lower regression risk):**
- Do NOT rip out the sync `_apply_or_extend_contribution_grant` (webhook path, sync
  Session). Add a **parallel async** `grant_invite_access(session, school, days)` in
  a new `app/services/school_access.py`, mirroring the proven pattern: `merge`
  the comp product, `SELECT … FOR UPDATE` on the **deterministic** id
  `comp_invite_<school_wriveted_id>` (one row per school — required for the
  FOR-UPDATE stacking to serialise concurrent accepts), create/extend, `is_active`,
  `expiration=now+days`, `info={"source":"invite"}`, empty `stripe_customer_id`,
  `type=SCHOOL`, `product_id=comp_school_invite`, flip `school.state=ACTIVE`,
  return `(outcome, expiration)`.
- `School.subscription` is uselist=False **ordered `(is_active desc, expiration
  desc)`**, so a contribution comp + invite comp + real Stripe row can coexist and
  still resolve to the live one. So separate per-source rows are safe.
- Discrimination is by `info["source"]`, not id prefix. Define
  `COMP_GRANT_SOURCES = {"contribution","invite"}` in `school_access.py`.
- **Two surgical broadenings (must-fix):**
  - Lapse sweep (`app/api/internal/__init__.py`, `/maintenance/lapse-expired-schools`)
    → filter `info["source"].in_(COMP_GRANT_SOURCES)` (else invited schools never
    lapse after 90d — the core trial boundary).
  - `_retire_contribution_grants` (stripe_events.py) → retire **all** comp grants
    (`info["source"].in_(COMP_GRANT_SOURCES)`) on Stripe conversion, so a leftover
    invite grant doesn't leave a second active row.
- Preserve contribution semantics exactly (proportional days, product merge+flush
  before insert, FOR UPDATE stacking, `(outcome, expiration)` where outcome
  depends on liveness AND `school.state==ACTIVE`).

**Paying-inviter gate:** `has_active_subscription` is a *filter param*, not a
callable — add a small EXISTS helper (`is_active AND stripe_customer_id != ''`).

**Accept hardening:**
- Reuse onboarding's **admin-hijack guard** — refuse if the invited school already
  has a SchoolAdmin (not just "not ACTIVE").
- Reject a user who already admins another school (an existing SCHOOL_ADMIN would
  otherwise be silently re-bound via `ON CONFLICT … SET school_id`).
- Lock the invited school row + re-check state/admins **after** locking (serialise
  against concurrent onboarding/contribution).
- **One invite grant per school ever:** if a `comp_invite_<id>` row already exists,
  accept the invite record but do NOT (re-)grant/extend — prevents cycling more
  free time via multiple inviters.
- Token is a **bearer capability**: any authenticated user may accept via the link
  (record who accepted); email match not required (documented choice).

**Cap race:** `SELECT … FOR UPDATE` the inviter school row during `create_invitation`
so two concurrent sends can't both pass the ≤3 check.

**Misc:** naive `datetime.utcnow()` throughout (consistent with the codebase);
rely on the existing (name, country_code) uniqueness backstop for duplicate
school creation on accept.

**Shared onboarding helper:** extract `promote_to_school_admin` + educator-bind +
resolve/create-school from `onboarding.py` into a reusable helper (both onboarding
and accept use it); do NOT reuse onboarding's contact-info/`state=PENDING` block
(accept goes straight to ACTIVE).
