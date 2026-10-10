# Affogato RSS Reader backend

FastAPI, SQLAlchemy, synchronization, translation, brief scheduling, and CLI
services for Affogato RSS Reader. The supported v0.5.1 distribution is the container and
Compose release package; this wheel is an internal image build artifact.

## Governed automatic tagging

Automatic tagging classifies batches of up to 10 entries against at most 100
approved tags and 50 active proposals. The complete prompt is bounded to about
24,000 characters; article payloads contain only a batch-local ordinal, title,
and summary. Authors, feeds, domains, and RSS categories are used only for local
candidate ranking.

Valid results contain zero to three structured topic references at confidence
0.80 or higher; an empty result still counts as completed classification. Closed
mode selects only existing auto-assignable tags. Threshold mode keeps new
English canonical topics as proposals, automatically promoting them by default
after support from at least 10 distinct Works within 365 days. A Work identifies
a deduplicated work, so entries associated with the same Work count once toward
the threshold.

`POST /api/v1/auto-tag/proposals/{proposal_id}/promote` allows manual promotion
without the support threshold or an LLM call. Promotion, alias registration,
and backfill of source-hash-matching entries run in one transaction. Backfill
respects the automatic-tag limit and owner-removal suppressions. Repeated
promotion requests return the existing promoted tag. Promotion events are
written to the backend application log after commit; settings shows active
candidates after the formal tag controls.

Entry-tag provenance separates `manual`, `auto`, and migrated `legacy` sources.
Automatic replacement removes only automatic sources, preserving manual links;
owner removal creates a suppression so later automatic runs cannot restore the
tag. Settings offers explicit tag selection and deletion with select-all and
invert-selection controls. Deletion removes tag associations without deleting
entries or feeds. A reference from an enabled brief schedule blocks the entire
deletion batch; disabled schedules have deleted tag IDs removed.

With an LLM connection configured, automatic tagging can be enabled directly.
The 50-entry trial, full-run approval, and inferred legacy-association cleanup
are dormant behind disabled feature switches. Their UI, routes, helpers, data,
and tests remain available for restoration; disabled preview routes return 404
and queued preview jobs cannot call the LLM. See
[DORMANT_AUTO_TAG_PREVIEW.md](../docs/DORMANT_AUTO_TAG_PREVIEW.md) and
[LEGACY_TAG_CLEANUP.md](../docs/LEGACY_TAG_CLEANUP.md).

New entries and content changes queue classification for the affected entries.
Completed results remain valid after model, policy, tag-library, promotion, or
alias changes, including records with an old or missing policy version. The
status response retains `outdated_count: 0` and `needs_rebuild: false` for client
compatibility. Estimated calls cover remaining uncompleted or changed entries
in batches of up to 10. Policy checks still guard in-flight results and the
retained preview approval workflow.

## Article-specific tag weights

Migration `0016_entry_tag_weights` adds a nullable `EntryTag.weight` override.
Existing associations and provenance are preserved. The effective weight is
`COALESCE(EntryTag.weight, MAX(EntryTagSource.confidence), 1)` for each entry-tag
association. Entry responses expose `tags[].weight` and sort tags by descending
weight, then case-insensitive name. The frontend uses the same weight-first
rule for article details and the two tag badges beside domains on article cards.

`PUT /api/v1/entries/{entry_id}/tags/order` accepts `{"tag_ids": [...]}` containing
every currently attached tag exactly once, in the desired order. The route
requires owner authentication and CSRF validation, locks against concurrent tag
mutations, and rejects a changed tag set with HTTP 409. It saves integer weights
from N down to 1 and returns the updated entry. Sorting affects only that entry
and leaves source confidence unchanged. Tag merges preserve the higher explicit
weight when both tags have a weight on the same article.

## Entry dates

Article lists and details prefer the source-provided update date, then the
publication date, then a labeled collection date. RSS Dublin Core `dc:date` and
Atom `updated` are mapped to the entry's source-update field. Synchronization
refreshes date metadata even when content is unchanged; a date-only change does
not invalidate translations or queue automatic retagging.
