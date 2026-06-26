# Design: Per-school / per-region chatflow segments ("Campaigns")

**Status:** Draft for review · **Date:** 2026-06-27 · **Author:** (design spike)

## 1. Problem & goals

We want chat experiences that adapt to **who** the reader is (their school / region) and
**when** it is (season, cultural moment, current event). Concrete motivating cases:

- **Matariki** (NZ, ~10 days out): NZ schools get a Matariki-themed flow, visual theme,
  and a book bias toward Matariki / Māori stories — only during the Matariki window.
- **Football World Cup**: a football flow/theme and a bias toward sport/football books,
  for everyone or a chosen set of schools, for the tournament window.

Goals:
1. Surface the *right* flow + visual theme + book bias for a session based on **school,
   region, and date** — without per-campaign client changes.
2. Make it **seasonal** (auto-activates/expires).
3. Lay a path so that **eventually schools author and share** their own segments.

Non-goals (v1): a full visual campaign builder for educators; a public marketplace.

## 2. What exists today (and the gap)

| Capability | State | Reference |
|---|---|---|
| Flow definition w/ `school_id`, `visibility` (PRIVATE/SCHOOL/PUBLIC/WRIVETED), `info` JSONB, publish/version | ✅ stored | `app/models/cms.py` FlowDefinition |
| **Flow selection** | ⚠️ **client passes `flow_id`** to `/chat/start`; no server targeting | `app/api/chat.py`, `chat_runtime.start_session` |
| Visual theme, school-scoped, referenced by a flow via `info.theme_id` | ✅ works | `ChatTheme`, `chat.py` theme load |
| Content variants w/ `conditions` + `weight` | ⚠️ schema only; `conditions` not evaluated at runtime | `CMSContentVariant` |
| School-aware content selection inside flows | ✅ `get_random_content(school_id=…)` filters by visibility | `cms_repository.py` |
| Booklists w/ `type` (PERSONAL/SCHOOL/**REGION**/HUEY/OTHER), `sharing` (PRIVATE/RESTRICTED/PUBLIC), `slug`, mature school ACL, public router | ✅ | `app/models/booklist.py`, `app/api/booklists.py` |
| Book bias in recommendations | ❌ scoring is hue / reading-ability / school-collection only; no booklist boost | `app/services/recommendations.py` |
| Region modelling | ⚠️ country FK only; sub-national lives in `School.info.location.state` JSONB | `school.py`, `school_repository.py` |
| **Time-windowing** (active_from/until) | ❌ nothing is date-scoped | — |
| CMS authoring | ⚠️ WRIVETED-staff only; admin UI gated to `wriveted` | `cms.py` routers, `authenticated-page` |
| Visibility enforcement in CMS ACL | ❌ `FlowDefinition.__acl__` / `CMSContent.__acl__` ignore `visibility`/`school_id` (ChatTheme & BookList *do* enforce it) | `cms.py` |

**Takeaway:** the targeting/seasonality/book-bias logic is the new part. Flows, themes,
and booklists are reusable payloads we already have.

## 3. Core proposal — a `Campaign` (a.k.a. Segment) entity + server-side resolution

A **Campaign** is a *targeting rule + a bundle of payloads*. It is deliberately separate
from the flow so the same flow can be reused, and so targeting/seasonality is editable
without touching flow internals.

```
Campaign
  id, name, description, slug (unique; for public discovery/clone)
  # Payload (all optional → a campaign can be just a book bias, or just a flow swap)
  flow_id        -> FlowDefinition   (which flow to run)
  theme_id       -> ChatTheme        (visual skin)
  booklist_id    -> BookList         (book bias)
  bias_mode      enum: BOOST | FILTER (v1 = BOOST only; FILTER reserved, see §5)
  # Targeting — structured (SQL-prefilterable, author-friendly)
  country_codes  text[]              (e.g. {"NZL"})
  region_states  text[]              (matches School.info.location.state; optional)
  school_ids     int[]               (schools.id allow-list; optional — see FK note)
  min_age, max_age                   (optional reader filter)
  # Targeting — optional power-user escape hatch
  targeting_cel  text                (nullable CEL expression, AND-gated after the
                                       structured prefilter; see §4a)
  # Seasonality
  active_from, active_until  timestamptz (nullable = open-ended)
  # Precedence & lifecycle
  priority       int                 (tie-break; higher wins)
  is_active      bool
  # Ownership / access / sharing (designed in from day one — see §7)
  created_by     -> users.id
  school_id      -> schools.id (int)  (owner's school; null = Wriveted-global)
  visibility     enum PRIVATE|SCHOOL|PUBLIC|WRIVETED   (default WRIVETED)
  created_at, updated_at, published_at
```

**School FK note (resolved):** the campaign's `school_id` and `school_ids[]` use
`schools.id` (**int**), matching the user/booklist/educator domain — because ACL
principals (`educator:{school_id}`, `student:{school_id}`) and the reader's session
school context are all built from `schools.id`. (Flow/theme/booklist references are by
their own PKs and are unaffected by this choice. Note: CMS entities — flow/theme/content —
instead key school on `schools.wriveted_identifier` (uuid); we deliberately do **not**
follow that here because targeting/ACL live in the user domain.)

A campaign with only `booklist_id` + `bias_mode=BOOST` + `country_codes={NZL}` +
window is exactly "bias NZ readers toward Matariki books for 3 weeks". Add `flow_id` +
`theme_id` and it's the full Matariki special.

### Why a new entity rather than targeting fields on the flow?
- The mental model *is* a bundle ("a Matariki special" = flow + theme + books + dates).
- Flows stay reusable; targeting/seasonality is decoupled and independently editable.
- Natural home for precedence, date windows, and later **school authorship + sharing**
  (a campaign is the unit a school publishes/clones — like a booklist).
- Avoids overloading `FlowDefinition.info` JSONB with untyped targeting logic.

## 4. Server-side resolution (the key change)

Introduce a resolver. `/chat/start` keeps accepting an explicit `flow_id` (override, for
testing/deep-links); **if omitted**, the server resolves the active campaign for the
caller's context and returns the resolved `flow_id` + `theme` + active `booklist`.

```
resolve(context = {school_id?, country_code?, region_state?, age?, now}):
  # 1. SQL prefilter — indexable, does the heavy lifting (covers Matariki/World Cup):
  candidates = Campaign where is_active
      and (active_from  is null or active_from  <= now)
      and (active_until is null or active_until >= now)
      and (school_ids    is empty or context.school_id    = any(school_ids))
      and (country_codes is empty or context.country_code = any(country_codes))
      and (region_states is empty or context.region_state = any(region_states))
      and (min_age is null or context.age >= min_age) and (max_age …)
      and visible_to(context)               # visibility/school SQL filter, §7
  # 2. Optional CEL gate — only for the few candidates that carry targeting_cel:
  candidates = [c for c in candidates
                if c.targeting_cel is null or eval_cel(c.targeting_cel, context)]
  if none: return default global flow (today's behaviour)
  # 3. Precedence:
  return best(candidates)
```

**Precedence (most specific wins), then `priority`, then most-recent:**
`school_ids` match ▶ `region_states` match ▶ `country_codes` match ▶ global (no
targeting). A school's own Matariki campaign thus overrides the national one.

Resolution is cheap (small table, indexable prefilter) and cacheable; it must be
**async / non-blocking** (cf. the recent event-loop incident). CEL eval is pure-compute
(no I/O), runs only on the already-small prefiltered set, and should use compiled-
expression caching.

### 4a. Filtering language: structured columns + optional CEL (hybrid)

We already ship a CEL evaluator (`common-expression-language`, `app/services/cel_evaluator.py`)
with custom functions and a `/flows/evaluate-cel` test/validation endpoint. Rather than
pick one, use both at the right layer:

- **Structured columns** (`country_codes`, `region_states`, `school_ids`, age, dates) are
  the primary mechanism: they push targeting into an **indexable SQL WHERE clause** (you
  resolve with one query, not by loading every campaign and evaluating CEL), and they map
  to friendly admin pickers — essential for the eventual non-technical school authors.
  These cover the motivating cases (NZ Matariki, World-Cup-for-selected-schools) outright.
- **Optional `targeting_cel`** is an escape hatch for logic the columns can't express
  (e.g. `school.num_students > 200 && context.age in 8..10`), AND-gated after the SQL
  prefilter so it only runs on a handful of rows. Reuses the existing evaluator + the
  authoring/validation endpoint, and keeps a consistent mental model with flow conditions.

Notes / small gaps to handle in M1: CEL has no `now()`/`date()` — pass `now` and any
needed dates into the context dict (there's already a `days_since(iso)` helper); add
compiled-expression caching; assemble the targeting context as
`{school: {country, state, …}, user: {age, …}, now}`. (Aside: flow *connection* routing
today uses a JSONLogic subset, not this full evaluator — unifying them is out of scope.)

## 5. Book bias in recommendations

**Decision (v1): soft `BOOST` only.** Add an optional parameter to
`get_recommended_editions_from_mv`:
- `boost_work_ids: set[int]` (or `booklist_id`) → correlated `EXISTS` against
  `booklist_work_association`, adding a score tier (e.g. **+3**, between reading-ability
  and school-collection) — mirrors the existing school-collection `EXISTS` boost. Themed
  books surface more often but the full catalogue stays open (safer when a themed list is
  small).

The flow's recommendation node passes the active campaign's booklist through. The MV
itself needs no change (booklist membership is joined at query time).

`bias_mode=FILTER` (hard themed-only shelf) is **deferred** — the column is kept on the
schema for forward-compat, but only `BOOST` is implemented in v1.

## 6. Seasonality

`active_from` / `active_until` on the campaign drive auto-activation/expiry — no manual
toggling, no stale Matariki content in August. This is the one genuinely missing
primitive (nothing in the system is date-scoped today). Resolution filters on `now()`.

## 7. Access control & sharing — designed in from day one

Even though educator *authoring* ships later, the campaign's access model is correct from
the first migration so we never retrofit (and never repeat the flow/content visibility-ACL
gap). It composes the proven **BookList** ACL + **ChatTheme** query-filter patterns.

**Two complementary layers (both required — `__acl__` alone is per-object and can't gate a
list/resolve query):**

1. **Per-object `__acl__`** (governs edit/read on a single campaign), modelled on BookList:
   ```python
   def __acl__(self):
       policies = [
           (Allow, "role:admin", All),
           (Allow, f"user:{self.created_by}", All),       # owner
           (Allow, f"schooladmin:{self.school_id}", All),  # school admins
           (Allow, f"educator:{self.school_id}", All),     # school educators
       ]
       if self.school_id is not None:
           policies.append((Allow, f"student:{self.school_id}", "read"))
       if self.visibility in (PUBLIC, WRIVETED):
           policies.append((Allow, Authenticated, "read"))
       return policies
   ```
   Routes load the campaign then `Permission("update", get_campaign)` — exactly the
   booklist pattern.

2. **Query-level visibility filter** (governs list + the resolver), modelled on the working
   ChatTheme list/get: non-admins see `WRIVETED` ∪ `PUBLIC` ∪ (`SCHOOL`/`PRIVATE` where
   `school_id == caller's school`). The resolver's `visible_to(context)` clause (§4) is
   exactly this filter — so a session can only ever be *targeted* by a campaign it's allowed
   to receive (a school's PRIVATE/SCHOOL campaign never leaks to other schools).

**Sharing** reuses the booklist model: `visibility=PUBLIC` + unique `slug` + a public
read/list route; "use this campaign" = **clone** into your school (flow cloning already
exists in `flow_service.clone_flow`; extend to clone the campaign bundle — and decide
whether the cloned flow/booklist are copied or referenced). A curated/featured gallery is
a later nicety.

**Enablement is still phased** (the *model* above is built now; the surfaces open over time):
- **Phase A — Wriveted-authored** global/region campaigns via the admin UI
  (`visibility=WRIVETED`). Ships Matariki & World Cup.
- **Phase B — School-scoped authoring.** Educators create `visibility=SCHOOL`,
  `school_id=theirs`. Campaign ACL already supports this; remaining work is the educator
  authoring surface **and** closing the **FlowDefinition/CMSContent** visibility-ACL gap
  (campaigns are correct, but a school-authored *flow* they reference must also be
  access-correct) — see §8.
- **Phase C — Cross-school sharing** (PUBLIC + slug + clone), per above.

## 8. Risks / gaps to close
- **FlowDefinition/CMSContent visibility-ACL gap** — their `__acl__` ignores
  `visibility`/`school_id` (only ChatTheme & BookList enforce it). The *campaign* ACL is
  correct from day one, but **Phase B** (school-authored flows referenced by campaigns)
  must close this on the flow/content side before opening authoring. Not a blocker for
  Phase A (Wriveted-authored).
- **School FK — resolved:** campaign uses `schools.id` (int), aligning with ACL principals
  and the reader's session context (see §3 note). No reconciliation of the CMS uuid FK
  needed for this feature.
- **No first-class Region entity** — v1 uses `country_codes` + `region_states` (the latter
  matched against `School.info.location.state` JSONB; ensure schools are populated, else
  region targeting silently matches nothing). Add a Region model only if demand appears;
  NZ-wide = `{NZL}` suffices now.
- **CEL** — add `now`/dates to the eval context (no `now()`/`date()` builtins) and compiled-
  expression caching; keep `targeting_cel` optional so the common path is pure SQL.
- Resolution must stay async / off the event loop (no blocking SDK calls).

## 9. Delivery plan

**Decisions (owner, 2026-06-27):** build the **general framework first** (Matariki lands
when ready, not rushed); v1 supports the **full bundle** (flow + theme + book bias); book
bias is **soft BOOST** in v1.

Resolution lives transparently inside `/chat/start`, so the consumer app needs **no
per-campaign change** — this sidesteps the "which app renders flows" concern for v1.

Milestones (each a reviewable PR / small set of PRs):

- **M1 — Campaign entity + resolver (backend core).**
  `campaigns` table via alembic; Campaign model, repository, Pydantic schemas; targeting
  + precedence (school > region > country > global) + date-window resolver
  (`resolve_campaign(context)`); wire into `/chat/start` (explicit `flow_id` still
  overrides; otherwise resolve flow + theme). Async, no blocking calls. Wriveted-only
  admin CRUD endpoints. Unit + integration tests (precedence, windowing, fallback).
- **M2 — Book bias (BOOST).** Extend `get_recommended_editions_from_mv` with
  `boost_work_ids`/`booklist_id`; recommendation node reads the active campaign's booklist
  from session context; tests for the boosted ordering.
- **M3 — Admin authoring UI.** Campaigns list + form in the admin UI (targeting, date
  window, payload pickers for flow/theme/booklist), Wriveted-gated; per-campaign analytics
  via existing conversation-session/event data.
- **M4 — Seed the real campaigns.** Matariki (NZL, dated window, Māori-stories booklist,
  theme) and Football World Cup (sport booklist), authored via M3.

**Later (Phase B/C):** school-authored campaigns (`visibility=SCHOOL`) — *prerequisite:*
close the CMS visibility-ACL gap (§8); then clone-and-share public campaigns.

## 10. Resolved decisions
- ✅ Sequencing = framework-first · ✅ v1 = full bundle · ✅ bias = soft BOOST.
- ✅ Filtering = hybrid: structured columns (primary, SQL-prefilter) + optional `targeting_cel`.
- ✅ Access control & sharing designed in from the first migration (owner / school-scoped /
  public-shareable), via `__acl__` + query-level visibility filter.
- ✅ School FK = `schools.id` (int).
- ✅ Consumer-surface concern resolved by making resolution transparent in `/chat/start`.
