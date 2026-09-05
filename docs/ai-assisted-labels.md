# AI-assisted labels and teacher review

`AI_ASSISTED` describes labels proposed through an external AI-assisted research workflow. It has authority 4, below `EDUCATOR` (4.5) and `HUMAN` (5), and belongs in the AI-labelled review queue. Model names must not be encoded in the source enum. Legacy GPT4 and VERTEXAI sources remain readable.

New research metadata can be stored at `labelset.info.ai_assistance`. Teacher reviews accept the same `ai_assistance` structure, stored in the review assessment alongside its server-assigned reviewer identity and timestamps:

```json
{
  "schema_version": 1,
  "model": "exact model identifier, if known",
  "provider": "provider, if known",
  "run": "research run identifier",
  "prompt_version": "teacher-review-v1",
  "sources": ["https://publisher.example/book"],
  "rationale": "Evidence for the proposed labels",
  "uncertainties": "Edition identity or conflicting guidance",
  "generated_at": "2026-09-06T00:00:00Z"
}
```

Fields other than schema_version are optional. Unknown model identifiers are left unset, never inferred. Sources allow HTTP(S) URLs only. Metadata is bounded and is a submitted research claim, not provider attestation. Do not store API keys, private conversations, student data or hidden reasoning. A concise evidence-based rationale is sufficient.

Existing `info.reviewed_ai_labelling` records retain their full proposal, before-image, research model, cross-check and parent decision. The admin UI reads both formats. An AI cross-check is not a human review. Submitting a real educator review applies EDUCATOR authority; staff reviews apply HUMAN authority. Partial staff reviews do not mark a book checked. Source metadata and review approval are separate.

## Deployment and backfill

1. Apply the additive `a1c2e3f40013` enum migration. It does not reclassify data.
2. Deploy the API to every serving public/internal revision before writing AI_ASSISTED values; older application enums cannot deserialize the new value.
3. Deploy the admin UI. Research labels only retrieves a prompt; it does not enqueue or call an AI provider. Teachers review shared labels via the review endpoint, not the staff-only catalogue editor/delete routes.
4. Run `uv run python -m scripts.reclassify_reviewed_ai_labels` using the intended database configuration. Default is a transaction rollback. It requires exactly 200 distinct works from the two audited Chennai runs, model provenance and before-images.
5. Inspect its receipt, then rerun with `--commit`. Only OTHER fields actually assigned by those research runs are reclassified. Existing age/summary sources, restrictions, evidence, attribution and checked flags are preserved. Repeated runs are no-ops. Keep the receipt with the operational audit artifacts.

The enum downgrade retains the additive value to avoid rebuilding dependent columns. To roll back to older application code after the data backfill, first conditionally undo only the receipt's origin changes where the origin is still AI_ASSISTED, checking for intervening reviews. Never blanket-update all AI_ASSISTED or OTHER records.
