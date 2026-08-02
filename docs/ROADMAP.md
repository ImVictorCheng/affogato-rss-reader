# Development Roadmap

This document records planned work that is not yet implemented. Items do not
carry a release date unless one is stated explicitly.

## Native Windows, macOS, and Linux distributions

**Status:** Planned

Add supported native installation and lifecycle options in addition to the
current Docker Compose deployment. This is a complete distribution track, not
only a change to the database directory.

### Required scope

- Package signed, versioned Windows, macOS, and Linux artifacts with reproducible
  builds, provenance, checksums, and platform-specific installation testing.
- Support an interactive per-user application and a documented headless/service
  mode, including startup, shutdown, crash recovery, autostart, port selection,
  firewall guidance, and clean uninstall behavior.
- Use platform conventions for durable data, configuration, caches, logs, and
  secrets: LocalAppData/ProgramData and protected Windows credentials, macOS
  Application Support/Keychain, and XDG/system directories plus a secret store
  on Linux. Uninstall must preserve user data unless deletion is explicit.
- Provide safe import/migration from Docker volumes and older native layouts,
  with verified backups, integrity checks, rollback, and clear ownership and
  permission handling.
- Define native update channels, signing/notarization, rollback, release notes,
  proxy behavior, and offline/manual update paths independently of the Docker
  Socket updater.
- Decide and test the user experience for browser launch, tray/menu integration,
  service status, diagnostics, log collection, backup/restore, and recovery from
  a port conflict or damaged configuration.
- Run installation, upgrade, rollback, backup/restore, and uninstall matrices on
  supported Windows, macOS, and Linux versions before advertising native support.

### Acceptance criteria

- A new user can install, start, update, back up, restore, and uninstall without
  requiring Python, Node, or Docker.
- Updates preserve the database and secret-store relationship and can roll back
  without silently presenting an empty library.
- Native services run with least privilege and do not write into the installation
  directory.
- Platform CI exercises real packaged artifacts rather than source-only startup.

## Entry preprocessing cards

**Status:** Planned

Preprocess newly fetched entries in the background and persist a reusable,
structured "entry card" for later LLM features, especially brief generation.
The goal is to move repeated analysis out of the interactive brief-generation
path without lowering the quality ceiling of briefs.

### Required design constraints

- The original title and summary remain the canonical source. A card is a
  sidecar cache and must never replace, truncate, or overwrite source content.
- Input used to create a card must not be silently truncated. Oversized input
  must use lossless fragmentation or fail with an explicit, recoverable state.
- Brief generation must be able to read the complete original summary or
  translation. It must not depend exclusively on a lossy card.
- A missing, queued, stale, or failed card must not block a brief; the system
  falls back to the original source content.
- Cards are versioned by source-content hash, prompt/schema version, model, and
  connection configuration so stale results can be detected and rebuilt.
- Background processing exposes distinct queued, running, completed, failed,
  and stale states, with bounded automatic retry and manual retry.
- Slow or disconnected LLM connections must not occupy unbounded workers.
  Processing uses bounded concurrency, streaming activity updates where
  supported, a 30-second read-inactivity limit per wait, and durable progress.
- Brief generation uses a fixed entry snapshot. Entries fetched while a brief
  is running are handled by the next brief rather than changing retry batches.
- LLM and translation calls continue to use the existing operation log and
  redact credentials and other secrets.

### Intended brief workflow

1. Use cards for reusable classification, deduplication, ranking, and outline
   preparation.
2. Re-open complete source summaries or translations for entries selected for
   the brief and for any ambiguous card.
3. Preserve the existing lossless batching and resumable checkpoints when the
   complete input exceeds a model's context window.
4. Initially provide a quality-first full-source mode. An optional accelerated
   mode may use cards for stronger filtering, but it must be clearly labelled
   because summarization can omit information even when no text is truncated.

### Delivery stages

1. Add the card schema, source/version fingerprinting, processing states, and
   migrations.
2. Add the bounded background queue, automatic/manual retry, progress, logs,
   and stale-card rebuilding.
3. Add card inspection and status controls to the web interface.
4. Integrate cards into brief preparation with source fallback and fixed
   snapshots.
5. Compare latency, token cost, coverage, and brief quality against the current
   full-source workflow before enabling acceleration by default.

### Acceptance criteria

- Reusing completed cards materially reduces repeat brief preparation work.
- A card outage or backlog does not prevent full-source brief generation.
- Updating an entry invalidates only the affected card.
- Changing the card schema or prompt can rebuild cards without modifying source
  entries.
- Tests demonstrate that no source input is silently truncated and that full
  source content remains reachable throughout brief generation.
