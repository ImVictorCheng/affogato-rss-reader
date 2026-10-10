# Dormant auto-tag preview workflow

The 50-article trial and full-run approval are temporarily disabled. The UI,
API client methods, backend routes, sampling/classification/approval helpers,
styles, database models, and historical preview rows remain available for
restoration. The frontend does not load or poll preview data while disabled;
the API routes return 404, and queued preview jobs cannot call the LLM.

Automatic tagging can be enabled directly with a configured LLM connection.
Stored `auto_tag_preview_required` markers remain intact but do not block
tagging while the feature is disabled. Completed articles retain their results
when the policy or topic library changes, including new topic promotions and
alias updates. They are not marked as needing a rebuild, even if their stored
policy version is old or missing. The status response keeps `outdated_count: 0`
and `needs_rebuild: false` for client compatibility; no historical data rewrite
is needed. New articles and changed article content still enter the normal
queue. The call estimate covers the remaining queue instead of an unrequested
full rebuild.

Active candidates appear after the tag settings in a responsive card grid.
Manual promotion is available independently of the dormant preview workflow:
it skips the support threshold, transfers aliases, and attaches the formal tag
to valid supporting articles under the existing limits and suppressions. It
does not call the LLM or requeue completed articles. Promotion history stays
in the application log; repeated requests return the same tag.

To restore the workflow:

1. Set `AUTO_TAG_PREVIEW_ENABLED = True` in `backend/app/auto_tag.py`.
2. Build the frontend with `VITE_AUTO_TAG_PREVIEW_ENABLED=true` (the check is
   in `web/src/features.ts`). Enable both sides together.
3. Run the retained preview unit/guardrail tests and browser tests. Preview
   browser cases opt in with the same Vite environment variable; ordinary
   browser runs test the default disabled state instead.
4. Rebuild and deploy. Old preview data is retained, but approval still
   validates the current policy version, taxonomy, and article sample.

The independent provenance cleanup switches remain disabled. See
[LEGACY_TAG_CLEANUP.md](LEGACY_TAG_CLEANUP.md) before enabling that workflow.
