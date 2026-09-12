# Engineering documentation

Use the document that owns the contract; avoid copying implementation settings,
test totals or rollout evidence into architecture descriptions.

| Question | Document |
| --- | --- |
| Where does application logic and data access belong? | [Service architecture](architecture-service-layer.md), [architecture alignment](architecture-alignment.md) |
| What changes are proposals rather than requirements? | [Architecture roadmap](architecture-roadmap.md) |
| How should tracing, metrics and logs work? | [Observability architecture](observability-architecture.md): implemented baseline and proposed OTel/Google target |
| What do usage/UX/business metrics mean, and what may we retain? | [Analytics proposal](analytics-proposal.md) |
| What does the educator dashboard actually promise? | [School Insights](school-insights.md) |
| Is session replay the same as distributed tracing? | [Session replay](design-session-replay.md): separate sensitive support feature with capture gaps |
| Who owns sites, education records and billing? | [Target schema](organisation-target-schema.md), [entitlements](organisation-entitlements.md), [billing](school-billing.md) |
| How are identifiers named? | [Identifier naming](identifier-naming.md) |
| How are agents and human book reviews supported? | [Librarian MCP](librarian-mcp.md), [AI-assisted labels](ai-assisted-labels.md) |

Historical decisions live in [ADR.md](ADR.md) and `adr/`. Read their status before
treating them as current policy. Proposals do not authorize infrastructure,
retention or production changes.

When replacing a document, move its surviving contract to the owning document,
update links and delete the old file in the same change. Git history preserves
the previous explanation; do not keep a deprecated redirect document. When
retiring code, remove its callers, interfaces, configuration and obsolete tests
together. Do not erase applied migrations or retained customer data as a cleanup.
