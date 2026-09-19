# Catalogue search result contract

This document defines the target search contract. Implementation and migration changes must preserve authorization and response compatibility.

## Identity and coverage

Work search returns one result per `works.id`. All author and series associations contribute to a single document; response author/series arrays are unique by their own IDs. Deduplication happens before ranking, pagination and counts. Edition lookup remains edition/ISBN-oriented, and resolves to a work only when the result surface is explicitly work search. An edition with no work cannot silently become a fabricated work result.

Include works with missing authors, series or popularity. Missing author/series text becomes empty text; missing popularity becomes zero. Popularity is an optional ranking signal, never a join requirement. If retained, use a neutral nonzero multiplier such as `1 + log(1 + frequency)`; measure it separately from lexical ranking. A zero multiplier would erase all relevance for unheld works. Book recommendation eligibility (labels, age, availability rules) remains a separate contract.

Do not merge work IDs by normalized title. Different authors, editions grouped incorrectly upstream, translations and unrelated works can share a title. Catalogue identity repair requires its own evidence. Likewise, do not choose an arbitrary series row to eliminate duplicates: combine all series text.

Construct author names with `concat_ws` so a missing first or last name does not erase the other component. Use the same field text for native and BM25 comparisons. Native field weights and popularity are separate ranking experiments, not evidence about the index access method.

## Input intent

- An ISBN-shaped input uses normalized exact ISBN lookup first. It must not depend on token ranking or popularity, and must preserve edition identity where the API returns editions.
- Complete title, author and series queries use lexical retrieval. Keep existing Boolean/phrase syntax on the existing endpoint; a BM25 replacement must preserve it with an eligibility predicate or make a separately reviewed API change.
- Partial and misspelled input requires a prefix/substring or trigram path. BM25 token ranking alone does not promise either behavior.
- Empty and stopword-only inputs browse eligible works by popularity descending, then work ID. A nonempty unmatched query returns no lexical results. Do not fill a lexical result page with zero-score unrelated works.
- Scores use deterministic `work_id` tie-breaking. Counts are distinct eligible work counts, not candidate caps or raw join rows.

## Library scope

A scope is resolved and authorized by the application before retrieval. Use the collection ownership relationships defined by the application schema. Keep customer holdings and identifiers in private evaluation storage.

The target **owned** filter includes a work when any edition has an item with `copies_total > 0` in any collection belonging to that authorized school. Multiple editions, copies and collections do not multiply results. The optional **available now** filter additionally requires `copies_available > 0`; it is distinct from ownership. This makes the positive-copy condition explicit; the existing recommendation membership query checks item presence and is not being changed here.

Apply scope to the complete eligible relation before any top-k truncation. A fast global top-k followed by filtering is not an equivalent implementation. Compare indexed BM25 with exhaustive scoring of that same eligible set and reject underfilled pages or missing higher-scored results. A materialized eligible relation is one candidate for selective scopes; the measured planner determines the eventual SQL.

## Evaluation judgments

Evaluation sets must document provenance and intended use. Keep datasets, measurements, identifiers and operational observations outside this public repository. Synthetic fixtures may be committed. Every query records its provenance and intent. Title variants target all work IDs with the same normalized title; author and series queries target all works with that association. Topic tests judge explicit title mentions only. These are metadata-derived **silver judgments**, not human assessments of broader thematic relevance. Prefix/typo inputs have an explicitly nominated intended title.

Report results per intent family and library size. Missing targets in a library make a case ineligible for relevance averaging, not a failure. Still test that such queries never leak out-of-scope results. ISBN, empty input and accent handling are contract checks rather than reasons to choose a lexical ranking engine.

Before user-facing ranking rollout, supplement this regression set with blinded human judgments of pooled results for open-ended discovery queries and a consented/approved way to sample query intent. Do not infer a traffic-level conversion benefit from this offline set.
