# Library-scoped Bookbot

Each library has a shareable `/chat/start/?library=<uuid>` link. The older
`?school=<uuid>` entry point remains an alias. These links select a catalogue,
not a student's school membership or permissions.

## Ownership and authorization

Library managers edit the reading experience through
`GET/PUT /v1/libraries/{library_uuid}/chat-settings`. Readers can inspect it;
updates require `manage_details`, remain blocked in View As, and use an
`expected_revision` to prevent overwriting another manager's changes.

Settings belong to the current library-workspace identity. During the education /
library schema transition this is the `School.school_uuid` compatibility key,
not an arbitrary row from the future canonical `libraries` table.

Creating an organisation or catalogue does not activate student chat. A manager
explicitly enables it. Existing active library sites retain reader access;
otherwise a current paid organisation subscription is required. The API reports
effective availability separately from the saved enabled setting, including after
a subscription lapses. Disabling applies to new chats; existing sessions retain
their starting policy.

## Recommendations and optional activities

- `library_only` uses all collections belonging to the selected library, never
  its sibling libraries. Broader fallback is disabled, including when no suitable
  labelled books are available.
- `prefer_library` retains the wider-catalogue fallback. Existing active sites
  default to this policy until explicitly changed; new inactive sites default to
  library-only recommendations.
- Jokes and spelling can be enabled independently. The server skips the deployed
  `huey-jokes` / `huey-spelling` subflows when disabled. CMS flow replacements must
  retain these feature identities to participate in these controls.

For library-targeted starts, the server selects the applicable campaign flow or
published `huey-bookbot` flow. A caller cannot choose an unrelated root flow with
the same library link. Global staff-owned flow authoring remains separate from
library-manager settings; these controls do not authorize arbitrary CMS edits.

## Session invariants

The server stores a policy snapshot in session metadata at creation. State
updates cannot replace its library, recommendation scope or optional-activity
settings. Internal recommendation actions take catalogue policy from that trusted
snapshot, not request templates or browser state.

`ConversationSession.school_id` retains the authenticated student's home-school
identity. `library_id` records the selected catalogue independently. Library
Insights uses `library_id`, falling back to `school_id` for older sessions;
privacy suppression and aggregate-only access are unchanged. Library selection
does not grant access to individual student records.

## Verification and rollout

Apply the additive settings and attribution migrations before the API; deploy the
student application before publishing the new admin links. Existing flows must
have their deployment `info.seed_key` values, including `huey-bookbot` and optional
subflows. The normal flow deployment script supplies them.

Exercise two disjoint catalogues as anonymous and authenticated students. Verify
correct books, an honest empty result, optional-activity combinations, different
librarian permissions, View As, concurrent settings edits, subscription expiry,
and same-page navigation between library links. Use disposable fixtures, not
customer collections, and include runtime, recommendation and Insights tests.
