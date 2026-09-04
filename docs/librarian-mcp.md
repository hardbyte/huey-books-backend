# Librarian MCP

Huey Books exposes catalogue operations to staff-owned assistants. No model runs
inside the MCP server. Staff supply their own research tools and approve proposed
changes. Book labels belong to the shared catalogue; imports affect one school.

## Interface and research

Ten tools cover identity, school selection, vocabulary, search, book details,
recommendations, collection pagination, ISBN import and labelling. Three prompts
guide research/labelling, reading lists and imports. Vocabulary is loaded on demand.
The tools/list schema measured 5,994 JSON bytes (about 1,500 tokens using a rough
four-characters-per-token estimate; actual tokenizer counts differ).

Keep explicit, narrowly named tools at this scale. A search/execute meta-interface
would add another model round trip without much schema saving. Bound result counts,
return work identifiers for follow-up reads, and keep long research instructions in
prompts rather than every tool description. Reconsider discovery if the surface
grows substantially.

The design follows [Stripe's remote MCP connection model](https://docs.stripe.com/mcp):
staff connect an existing assistant rather than purchasing an embedded assistant.
OAuth configuration differs by client. OpenCode 1.x uses a remote entry under `mcp`
and `opencode mcp auth`; the [current OpenCode documentation](https://opencode.ai/v2/docs/mcp-servers)
also describes a newer configuration layout. The admin panel labels its example
with the version and always provides the endpoint separately.

[FastMCP's OAuth proxy guidance](https://gofastmcp.com/v2/servers/auth/oauth-proxy)
requires client consent to prevent confused-deputy attacks. Keep built-in consent
enabled: the upstream school picker knows the proxy client, not the assistant's
downstream client identity, and cannot supply the proxy's browser-binding cookie.
Staff therefore approve the connecting client before selecting a school.

Research prompts require publisher/author/library/review evidence, real citations,
honest uncertainty and separation of retrieved content from instructions. Hue
describes reading experience; reading ability describes decoding difficulty.

## Deployment invariants

- `MCP_ENABLED=false` leaves the REST entrypoint unchanged. OAuth tokens are rejected
  by REST authentication.
- Serve the MCP host at its origin root; `/mcp` and both well-known discovery routes
  must resolve without cross-origin redirects.
- Use stateless Streamable HTTP across Cloud Run instances. The current school is
  persisted per OAuth grant in the shared encrypted Postgres store. Storage failure
  fails the call instead of silently changing its school.
- Every school-scoped call checks current membership and token scopes. Live Wriveted
  admins may select any school; ordinary staff remain confined to their grant.
- ISBN-only import preserves existing holding totals and availability, including
  zero available copies. Results report added/existing/invalid/duplicate input.
- School-only recommendations filter by live holdings at work level, including
  alternate editions; broader catalogue recommendations require `school_only=false`.
- Label arguments are validated before any mutation. The existing origin hierarchy
  can preserve higher-authority labels; the tool returns the resulting labelset.
- Migration `a1c2e3f40012` owns the key-value schema; runtime does no DDL. Apply
  `pgroles.yaml` privileges/default privileges before enabling the service.
- Set a persistent signing key and `OAUTH_ALLOW_EPHEMERAL_KEY=False`. Preserve
  `SECRET_KEY` and the proxy client secret so encrypted state and proxy tokens survive
  restarts. Deployments use `--update-secrets` to retain OAuth bindings.

## Validation

```bash
uv run ruff check app/
uv run pytest app/tests/unit/ -q
COMPOSE_PROJECT_NAME=librarian-mcp-tests POSTGRES_HOST_PORT=55432 \
  LOCAL_BUILD_ONLY=1 bash scripts/integration-tests.sh -q
```

`test_mcp_operations.py` covers existing holding preservation, retry counts,
cross-instance encrypted storage, the restricted runtime role, and real MCP client
imports/permission denial. OAuth token-flow tests cover PKCE, rotation and replay.
Unit protocol tests verify stateless HTTP and client consent/browser binding.
Recommendation tests cover singleton results, strict school membership, alternate
held editions and the existing soft-scoring behavior for REST callers.

Admin consent checks: `npm run build` and
`npx playwright test --config playwright.oauth.config.ts`. Browser tests mock only
the account/consent API and exercise actual page navigation and request payloads.

OpenCode 1.18.27 successfully read staging identity, vocabulary, search results and
collection entries with its configured Grok model. The local Qwen3 14B model did
not select the requested tools; this is not a passing end-to-end result for Ollama.

## Production cutover — requires Brian's approval

Merging backend #739/#740 or admin #82/#83 triggers production deployments. Do not
merge as a supposedly dormant preparation step before approval.

1. Record current Cloud Run revisions, image digests, environment/secret bindings and
   Firebase live releases. Merge #739, retarget #740 to main if necessary, then merge
   #740 after its checks pass. Monitor migrations and both API deployments.
2. Merge admin #83 and #82; verify the combined live build, OAuth page and prompts.
3. Create Firebase site `hueybooks-mcp` and use `firebase.mcp.json` to route it to
   `wriveted-api` in `australia-southeast1`. Add the custom domain `mcp.hueybooks.com`
   through Firebase Hosting and apply the exact DNS records it supplies. Wait for
   certificate readiness. The site and DNS record did not yet exist at readiness review.
4. Configure the public production API using `--update-env-vars`/`--update-secrets`:

   | Setting | Production value |
   | --- | --- |
   | MCP_ENABLED | true |
   | MCP_HOST | mcp.hueybooks.com |
   | MCP_BASE_URL | https://mcp.hueybooks.com |
   | MCP_AUTHORIZE_URL | https://admin.hueybooks.com/oauth/authorize |
   | OAUTH_ISSUER | https://api.hueybooks.com |
   | OAUTH_API_AUDIENCE | https://api.hueybooks.com |
   | OAUTH_ALLOWED_REDIRECT_URIS | `["https://mcp.hueybooks.com/auth/callback"]` |
   | OAUTH_ALLOW_EPHEMERAL_KEY | False |
   | OAUTH_PRIVATE_KEY_PEM | Secret Manager: oauth-rs256-private-key |
   | OAUTH_MCP_CLIENT_SECRET | Secret Manager: mcp-oauth-client-secret |

   Pin secret versions selected at cutover. Do not expose values in logs or shell
   output. The internal API does not need MCP enabled.
5. Verify unauthenticated `/mcp` returns 401, discovery advertises the exact resource,
   a fresh client completes both consent steps, and an existing client survives a
   revision change. Check search/recommendations, then explicitly approved writes
   against a designated test school, including idempotent re-import.
6. Roll back by setting `MCP_ENABLED=false` first, then restore the recorded API and
   Firebase revisions as needed. Leave additive OAuth tables in place; dropping them
   destroys active grants and is unnecessary for application rollback.

Non-admin multi-school consent and a Huey Books connection-management page remain
separate follow-ups. The UI must not promise server-side revocation settings that
do not exist. Removing a client connection deletes its local credentials; it is
not equivalent to revoking already-copied credentials at the server.
