# Separate library management from legacy school authority

Organisation and library management use explicit scoped memberships while existing School records retain reader, billing, and public-link identity. Library is initially an interface over that storage, not a second identity table: duplicating records would introduce drift before any legacy consumer is migrated. A default collection preserves singular integrations while additional collections remain independent; organisation membership never manufactures the broad legacy school-administrator principal.
