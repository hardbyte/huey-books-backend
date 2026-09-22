# Critical reader journey release gate

The reader experience is the primary release criterion. HTTP health, migration success, API tests and mocked browser tests do not satisfy this gate.

`e2e-live` drives the deployed chat UI with a real browser against its real backend. It does not intercept or fabricate chat responses. It checks greeting input, age selection, reading-level carousel, preference choices, visible book recommendations, feedback submission, optional-joke exhaustion, and conversation completion. It covers young and older readers, mobile, desktop and short viewports.

Run against an explicitly selected UI:

```sh
cd e2e-live
npm ci
npx playwright install chromium
E2E_UI_URL=https://your-chat-ui.example npm test
```

`E2E_CHAT_PATH` can select a library-specific chat link. No production library identifiers belong in the repository. Runs create synthetic anonymous conversations, including normal recommendation and CMS queries. Do not run load tests against production.

## Release behavior

The backend pipeline captures explicit traffic targets for both public and internal services, verifies the existing reader experience, then revalidates those targets before production changes. Deployments explicitly promote their new revisions, including when an earlier rollback left traffic pinned. After deployment, it checks that both deployment steps succeeded, the candidate commit owns both services and receives all traffic. The same real-browser journeys must then pass. A missing or failed browser result fails the release and restores the recorded targets for both services. Partial deployment failures also reach recovery. The rollback checks release ownership and reconciliation state, uses an etag to reject concurrent updates, reads back the resulting traffic, and reports a failed build even if restoration succeeds.

For an already-broken production environment, an operator may explicitly set the build substitution `_CHAT_RECOVERY_MODE=true`. This allows an unsuccessful baseline browser run so a repair can deploy; it never skips candidate browser verification. The captured baseline is not known-good in this mode, so restoring it can only recover the prior state. The default is `false`. A canceled or timed-out build cannot guarantee cleanup; the independent controller described below is needed for that case.

The application schema is retained. Only backward-compatible migrations belong in an automatically reversible release; use expand/contract changes. A destructive migration needs a separate reviewed recovery plan. Traffic rollback cannot repair incompatible database changes or faulty shared CMS content.

The service pair is checked before either rollback begins, and each update checks ownership again. The two traffic updates are not atomic: a concurrent release or control-plane failure can prevent the second update after the first succeeds. Such a failure is reported and requires reconciliation; the controller must never overwrite newer ownership to force a matching pair.

Frontend hosting has its own real-browser gate against the built UI and real backend before publication. Keep the reader-journey contract aligned between repositories. Neither a skipped job nor an unavailable CI runner counts as a pass.

## Remaining safeguards

This post-deployment backend check bounds exposure; it does not prevent the first request reaching a faulty candidate. The next deployment architecture should build a matching preview UI against tagged, zero-traffic public/internal revisions and run these journeys before promotion. Each preview database needs the actual versioned flow graph, representative CMS pools and a synthetic catalogue/library; an empty migrated database is not a representative environment.

Extend the deterministic preview matrix to include library scope, all age boundaries, exhausted preference pools, spelling branches, absent or malformed CMS content, back/reload/restart, and both populated and intentionally empty recommendation outcomes. Assert prompts, choices, images and book cards in the DOM, and verify no duplicate or skipped user input. Keep age/visibility constraints intact.

A cloud-hosted scheduled real-browser synthetic and an independent missed-run alert are still needed for continuous protection after the build ends. Email alert delivery and a desktop task's configured schedule are not substitutes for a durable rollback controller. Persist a known-good release pair and use revision-specific failure evidence; never let a stale monitor roll back a newer release.
