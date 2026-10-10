# Development Roadmap

This document records planned work that is not yet implemented. Items do not
carry a release date unless one is stated explicitly.

## 0.6.0: LLM Chat

**Status:** Planned

**Target version:** 0.6.0

**Added:** 2026-10-10

Add an LLM Chat workspace for multi-turn conversations, with optional article
context for questions, explanations, and follow-up discussion while reading.

### Initial scope

- Add a Chat workspace alongside Reader and Briefs, with desktop and mobile
  layouts, a conversation list, and a message composer.
- Reuse configured LLM connections and allow Chat to select its own connection
  independently of translation, automatic tagging, and brief generation.
- Support multi-turn conversations with streamed replies, Markdown rendering,
  copying replies, stopping generation, and explicit retry after failure.
- Persist conversations and messages locally so history survives reloads and
  application restarts and is accessible from the owner's other devices.
  Support creating, renaming, and deleting conversations.
- Allow starting a conversation from article details or attaching the current
  article to a conversation. Show the selected context before sending: the
  stored title and summary, with an explicit option to use an available
  translation. Article context is optional for ordinary chat.
- Link article-backed answers to the attached entry and its original source.
  Make clear when only a feed summary is available; responses must not imply
  that the full article or paper was read.

### Design constraints

- Add a dedicated Chat feature binding while retaining the existing encrypted
  secret storage, connection-specific proxy routing, owner authentication, and
  CSRF protection for mutations. Provider requests run through the backend.
- Show which connection/model and article context a request will use. Send
  conversation content and explicitly selected article context only; attaching
  one entry must not implicitly upload the library.
- Define a context budget for history and article content. Never silently
  truncate either: when the budget is exceeded, explain the limit and offer
  explicit context reduction or a new conversation before sending.
- Save the article-content snapshot and connection/model used for each turn so
  later feed updates or settings changes do not rewrite earlier context.
  Missing or failed preprocessing cards must not block article chat.
- Track generating, completed, cancelled, failed, and interrupted replies.
  Preserve partial replies with their status; failed or stopped output must
  not be treated as a completed assistant turn in subsequent requests.
- Reuse bounded LLM concurrency and cancellation. Apply a 30-second upstream
  read-inactivity limit, handle browser disconnects and application restarts,
  and make retries explicit once partial output has been received.
- Make submissions idempotent and prevent concurrent generation in the same
  conversation from duplicating messages or corrupting turn order.
- Record Chat calls in the existing operation log with credential redaction.
  Include conversation data in the existing backup/restore workflow and define
  deletion of messages and stored context together with their conversation.

### Delivery stages

1. Define conversation, message, context-snapshot, and generation-state models;
   add migrations and the independent Chat connection binding.
2. Add authenticated conversation APIs and streaming generation with ordered,
   idempotent turns, cancellation, failure recovery, and operation logging.
3. Build the Chat workspace, conversation management, streamed message display,
   and responsive desktop/mobile interaction.
4. Add the article-detail entry point, explicit context selection, and source
   links without depending on entry preprocessing cards.
5. Verify upgrades, backup/restore, context limits, and provider failure paths;
   update user and backend documentation before preparing the 0.6.0 release.

### Acceptance criteria

- With a Chat connection configured, the owner can start a conversation, ask a
  follow-up question, and see replies arrive incrementally.
- Reloading or restarting preserves conversations, messages, and reply states;
  interrupted generation is recoverable without duplicate turns.
- Article chat sends only the displayed context snapshot and provides links
  back to its sources; context-limit errors never silently discard input.
- Stop and retry work for partial output, timeouts, rate limits, and provider
  failures. A failed Chat request does not block reading or other LLM features.
- API and web tests cover authentication, CSRF, duplicate submissions, streamed
  output, cancellation, history persistence, context limits, and secret
  redaction; E2E covers a multi-turn conversation and an article question.
- Upgrading an existing installation preserves its feeds, entries, settings,
  and other LLM feature bindings, and backups restore Chat history correctly.

## Dates for entries from non-arXiv feeds

**Status:** Implemented (2026-10-10)

**Added:** 2026-10-09

- [x] Display a date for every entry from subscription feeds other than arXiv,
  consistent with arXiv entries. Use the RSS/Atom entry's update date, falling
  back to publication or clearly labeled collection dates.

The generic parser already maps item-level Dublin Core `dc:date` and Atom
`updated` to source update dates; standard publication dates and the original
collection date provide fallbacks. Feed/channel dates are not used for articles.
Refreshes backfill date metadata without reprocessing translations or tags.

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
