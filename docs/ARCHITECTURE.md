# Architecture

The FastAPI process owns the HTTP API, scheduler, synchronization, translation,
brief generation, and CLI service layer. React is built into static assets and
served from the same origin. SQLAlchemy stores all durable state in SQLite with
WAL enabled.

Core relationships:

- A work is globally deduplicated by base arXiv ID, normalized DOI, canonical
  URL, then feed GUID.
- A work may have multiple entries, including source revisions.
- Entry-to-feed relationships retain provenance and first-seen timestamps.
- Effective domain membership is the union of source domains and manually
  assigned entry domains.
- Reading state is server-owned; local storage only holds device preferences.
- Briefs are idempotent snapshots of matching first-seen windows.
- A maintenance pass attempts the daily backup first, then runs synchronization,
  translation, and scheduled briefs as isolated phases. A failed phase is
  recorded without preventing the remaining phases from running.
- Update checks run in a separate scheduler phase at startup and after 05:00
  local time once per day. GitHub metadata and the Compose asset are fetched
  through the application-wide proxy route and verified before state changes.
- The reader writes fixed-schema download/install requests to a dedicated named
  volume. A separate helper validates the repository, semantic version, release
  asset digest, Compose allowlist, immutable OCI digests, and image labels. Only
  that helper receives the Docker Socket; the Web-facing reader never does.
