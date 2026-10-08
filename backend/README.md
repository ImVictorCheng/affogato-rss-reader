# Affogato RSS Reader backend

FastAPI, SQLAlchemy, synchronization, translation, brief scheduling, and CLI
services for Affogato RSS Reader. The supported v0.5.0 distribution is the container and
Compose release package; this wheel is an internal image build artifact.

## Governed automatic tagging

Automatic tagging classifies batches of up to 10 entries against at most 100
approved tags and 50 active proposals. The complete prompt is bounded to about
24,000 characters; article payloads contain only a batch-local ordinal, title,
and summary. Authors, feeds, domains, and RSS categories are used only for local
candidate ranking.

Valid results contain zero to three structured topic references at confidence
0.80 or higher. New English canonical topics remain proposals until at least 10
distinct Works support them within 365 days. Promotion, alias registration, and
backfill of source-hash-matching entries run in one transaction. Entry-tag
provenance separates `manual`, `auto`, and migrated `legacy` sources, while an
owner removal creates a suppression so later automatic runs cannot restore it.

The workflow is gated by a non-mutating 50-entry preview. Approval reuses valid
preview results and queues the remaining history. Content changes requeue only
the affected entry; model, policy, or controlled-taxonomy changes are reported
as needing rebuild and never silently trigger full-history LLM calls.
