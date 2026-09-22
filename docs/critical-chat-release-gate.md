# Critical reader journey release gate

The reader experience is the primary release criterion. HTTP health, migration success, API tests and mocked browser tests do not satisfy this gate.

`e2e-live` opens the deployed chat UI in Chromium and drives greeting input, age selection, reading-level selection, picture preferences, visible book recommendations, feedback, optional-joke exhaustion and conversation completion. It covers young and older readers, mobile, desktop and short viewports. Candidate verification forwards the browser's actual API requests to the native Cloud Run candidate tag; it never fabricates responses. Every chat response must come from that candidate. Service workers are disabled so they cannot bypass request routing.

```sh
cd e2e-live
npm ci
npx playwright install chromium
E2E_UI_URL=https://your-chat-ui.example npm test
```

`E2E_CHAT_PATH` can select a library-specific chat link. Keep production library identifiers, session data and incident evidence outside this public repository. Runs create synthetic anonymous conversations; this is functional verification, not a load test.

## Native rollout

Infrastructure defines a Cloud Deploy pipeline with development and production stages. Each stage coordinates the public and internal Cloud Run services in a multi-target rollout. Native automatic traffic control advances through 0%, 10%, 50% and 100%. The `can` tag identifies the candidate, and `old` identifies the prior revision during canary phases. Cloud Run requires at least three tag characters and a combined service/tag name length no greater than 46.

Cloud Build builds the backend and browser-verifier images once. Development migrations precede the development rollout. A successful development rollout is required before production migrations, billing reconciliation, database-role checks and promotion. Images are resolved to digests. Release creation freezes existing runtime settings into a private artifact bundle; Helm target parameters select the correct service definition for each child target. This preserves secrets references, resource limits, identity, Cloud SQL connections and application configuration without putting environment snapshots in the public repository.

Every phase runs the verification container on both child targets. Each verifier waits for both services to finish reconciliation with the phase's expected traffic percentage, verifies native candidate tags, revision identity and image digest, and drives the real UI against the candidate public API. It checks the paired service state again after the browser tests. Each awaited question must have its visible prompt and usable choices; picture choices must load, and a recommendation must display a returned book title. At 0%, readers still use the prior release. Production advancement waits two minutes after a successful phase. A failed deployment or browser verification triggers native repair automation, which creates a rollout of the last successful release at the stable phase. The build remains failed even when recovery succeeds.

Internal service calls use the canonical internal endpoint. During intermediate phases, calls can cross versions; releases must remain compatible with the preceding version. The final gate runs after both services reach 100% candidate traffic. Coordination is not an atomic transaction across two services.

The initial native deployment has no successful release to restore and can skip canary phases. Bootstrap with the currently healthy image, verify it, and establish a successful baseline before normal release promotion. Prove a deliberately failed UI verification and native rollback in development before production cutover.

## Recovery and boundaries

Cloud Deploy continues phase advancement and repair independently of the submitting build. Canceling a build is not a rollback request. Inspect its native rollout and automation runs. Manually retrying, canceling, ignoring or terminating a rollout job can abort repair automation; do not use those controls while expecting automatic recovery. If another rollout is pending, the configured repair refuses to overwrite it and requires reconciliation.

Traffic rollback retains the database schema and shared CMS data. Only backward-compatible migrations belong in automatically reversible releases. Use expand/contract changes; destructive migrations require a separate reviewed recovery plan. Flow configuration changes need their own validation because a code rollback cannot repair shared content.

Frontend hosting has a separate real-browser gate against the built UI and real backend before publication. Keep the reader-journey contracts aligned. Neither a skipped job nor an unavailable CI runner counts as a pass.

These are rollout gates, not continuous synthetic monitoring after release completion. Existing production monitoring remains necessary. Extend the UI matrix to cover library scope, all age boundaries, exhausted preference pools, spelling branches, missing or malformed CMS content, back/reload/restart, and populated and intentionally empty recommendation outcomes. Preserve age and visibility constraints.

Native configuration lives in the infrastructure repository. See Google's documentation for [Cloud Run canaries](https://docs.cloud.google.com/deploy/docs/deployment-strategies/canary/cloud-run), [parallel deployments](https://docs.cloud.google.com/deploy/docs/parallel), [verification](https://docs.cloud.google.com/deploy/docs/verify-deployment) and [repair automation](https://docs.cloud.google.com/deploy/docs/automation-rules).

## Submission lock

Cloud Build acquires a generation-conditional object in the private deployment bucket before either environment's migrations. It holds that lease until production verification succeeds. Every backend `main` push triggers the pipeline without path filters. Concurrent builds fail before migrations or release submission, so they cannot queue a rollout that would suppress native recovery. They are not retried automatically: after the active deployment completes, rerun the desired build. The lease has no automatic expiry or stealing mechanism. Ownership checks validate both the object generation and its stored build identifier; deletion is conditional on that generation.

A failed or canceled build leaves the lease in place. Before an operator removes it, inspect the owning build, both native target rollouts and repair automation, confirm no deployment or migration remains active, and verify the current reader experience. Remove only the observed object generation. Do not queue a manual rollout behind a failing canary while expecting automatic repair.


## Maintaining the gate

The deployment wrapper uses the Cloud SDK already present in Cloud Build. Native
Cloud Deploy owns rollout decisions and recovery; the wrapper only renders,
submits and observes releases. Python dataclasses and enums model the state we
own. Cloud Run manifests retain their provider fields instead of duplicating the
entire Google schema. Adding an orchestration framework or SDK would not remove
the need for these release-specific checks.

Offline tests exercise lost leases, conditional deletion, failed rendering,
production promotion without verified development, unknown rollout state,
traffic mismatch and malformed questions. Strict type checks cover the deployment
modules and browser verifier. These tests protect guard logic; only the real
browser journeys against the deployed backend satisfy release verification.

```sh
source scripts/setup-test-env.sh
uv run pytest app/tests/unit/test_cloud_deploy*.py
uv run mypy --strict deploy
cd e2e-live
npm ci
npm run check
```

Keep operator recovery instructions here, infrastructure settings in the IaC
repository, and incident evidence and environment snapshots in private storage.
