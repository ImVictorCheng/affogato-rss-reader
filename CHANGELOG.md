# Changelog

All notable changes are documented here. This project follows Semantic Versioning.

## [Unreleased]

## [0.3.1] - 2026-08-02

### Added

- Automatic owner bootstrap with a random one-time initial password and an
  activation flow that replaces and removes the bootstrap credential
- Configurable backup count/byte limits and SQLite WAL/log rotation controls
- A local release preflight covering the Python matrix, Web suite,
  multi-architecture images, vulnerability scan, container smoke tests,
  release bundle generation, and source SBOM generation

### Changed

- Listen on all interfaces by default so remote Docker hosts are reachable;
  internet-facing deployments still require an HTTPS reverse proxy
- Standardize the Compose project, volumes, database, and backup resources on
  the `affogato-rss-reader` prefix
- Backups are written atomically, integrity-checked, and pruned by age, count,
  and total size
- Pytest rejects temporary roots placed inside application data directories
- CI and local preflight now share the same container smoke test and release
  bundle implementation

### Fixed

- Use BusyBox-compatible license verification in Alpine release images instead
  of the unsupported GNU `grep --quiet` spelling

## [0.3.0] - 2026-08-01

### Added

- Startup and daily 05:00 GitHub Release checks with automatic, digest-verified
  Compose asset downloads
- An in-app update prompt and owner-confirmed one-click install/restart with a
  required pre-update database backup and automatic rollback attempt
- An isolated, network-disabled update helper; only that helper receives the
  Docker daemon socket and the Web application never does
- An application-wide proxy route used by update checks, downloads, and other
  network features without a dedicated override

### Changed

- Switching from one feed to another always opens the article list at the top

## [0.2.0] - 2026-08-01

### Added

- Durable brief-generation checkpoints with automatic transient-error retries and
  user-triggered resume from the first unfinished batch
- Live brief progress for batching, consolidation, and final generation, including
  received-character updates while an LLM stream remains active
- In-app and host-readable JSON Lines logs for every LLM and translation call
- Brief deletion and a focused dialog for viewing, editing, or restoring generation rules
- Distinct pending, running, completed, and failed translation states with targeted retry

### Changed

- Brief LLM requests use streaming responses, a 30-second read-inactivity timeout,
  and exponential retry for network errors, rate limits, and server failures
- LLM translation uses the same streaming timeout and retry policy; all translation
  providers now checkpoint completed text chunks so retries skip finished work
- Brief input construction preserves complete source summaries and never silently
  truncates source content
- Settings loads diagnostic logs on demand and reads only the requested log tail,
  keeping the page responsive as logs grow
- Brief generation rules no longer occupy permanent workspace space

### Fixed

- Preserve completed brief batches after a connection failure so retry work can resume
- Keep incomplete and failed translations distinguishable from genuinely empty summaries
- Align brief toolbar controls and clear stale reader-navigation highlighting while
  the Briefs workspace is active
- Avoid scheduling the same translation row for deletion twice while merging
  duplicate entries
- Attempt the daily backup before optional network and LLM maintenance phases,
  and continue remaining phases after an isolated failure
- Pass documented retry and system-proxy settings through Compose, initialize
  the host call-log directory for the non-root container, and document trusted
  HTTPS reverse-proxy settings
- Include the full MIT license text in the published container image
- Keep the reader usable when the optional Briefs bundle cannot be loaded, with
  reliable reload and return-to-reader recovery actions
- Reject OPML documents containing DTDs or entities, and bound outline nesting
  before applying any imported data
- Pin GitHub Actions and container bases to immutable revisions, require the full
  CI suite before tagged releases, normalize GHCR image names, and scan both
  published architectures for High and Critical vulnerabilities
- Use a smaller Python 3.14 Alpine runtime and an `lxml` HTML parser so untrusted
  feed markup does not reach the vulnerable standard-library parser

## [0.1.0] - 2026-07-29

### Added

- Generic RSS 2.0 and Atom reader with an empty first-run library
- Feed discovery, editable source classification, folder sorting, tags, and OPML round trips
- Optional domain spaces with inherited membership and `ANY`/`ALL` filtering
- Cross-device read, starred, later, and archived states
- Full-field SQLite search with short Chinese substring fallback
- Optional Custom LLM, DeepL, Google Cloud, and Google GTX translation providers
- Reusable encrypted OpenAI-compatible LLM connections
- Per-feed, per-LLM, and per-translation-provider custom, system, or direct proxy routing
- Customizable generated branding, resizable reading panes, and categorized Settings pages
- Daily, weekly, monthly, and yearly LLM briefs with editable rules and schedules
- Owner and explicit authentication-free deployment modes
- Responsive bilingual web UI, OpenAPI, CLI, Compose, and multi-architecture release workflow

### Fixed

- Preserve feed and LLM feature relationships during SQLite schema upgrades
- Repair recoverable arXiv source relationships from category metadata
- Ensure direct network mode ignores proxy environment variables
- Batch large brief inputs and allow longer LLM summary responses
