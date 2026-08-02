# Backup, restore, and upgrade

## Create a backup

```console
docker compose exec reader affogato-rss-reader backup
```

Automatic backups are integrity-checked and written atomically. By default the
application keeps backups for at most 30 days, at most 14 files, and under a
2 GiB soft total. The two newest verified backups are retained even when the
byte limit is exceeded. Backup filenames use the `affogato-rss-reader-*` prefix.

For an independent copy, stop the service and archive the
`affogato-rss-reader-data` Compose volume. SQLite uses WAL mode, so do not copy
only the main database while the service is writing unless the CLI has produced
the backup.

API keys saved in Settings are encrypted. Docker stores the encryption master
key in the separate `affogato-rss-reader-secrets` volume. Back up that volume separately
and protect it at least as carefully as the database; a database backup without
the matching master key cannot restore saved API keys. Do not put both exports
in the same unencrypted archive.

To rotate the master key, point `AFFOGATO_RSS_READER_SECRET_KEY_FILE` at a new path,
include the old path in `AFFOGATO_RSS_READER_SECRET_KEY_PREVIOUS_FILES`, restart, and run:

```console
docker compose exec reader affogato-rss-reader secrets rotate
```

After verifying saved connections, remove the previous-key setting and securely
archive or destroy the old key.

## Restore

1. Run `docker compose down`.
2. Keep a copy of the current data and secrets volumes.
3. Replace `/app/data/affogato-rss-reader.db` with the selected backup inside the volume.
4. Remove stale `affogato-rss-reader.db-wal` and `affogato-rss-reader.db-shm` only while the
   service is stopped.
5. Restore the matching `/app/secrets/master.key` when restoring onto a new
   Docker host.
6. Run `docker compose up -d` and verify `/api/v1/health`.

## Upgrade

Create a backup, read `CHANGELOG.md`, pull the new image, and recreate the
service:

```console
docker compose pull
docker compose up -d
```

The Compose project and all persistent resources use the `affogato-rss-reader`
prefix. The SQLite database path is `/app/data/affogato-rss-reader.db`.

Release Compose deployments can instead use the in-app update prompt. The
application checks on startup and daily at 05:00, downloads and verifies the
new release Compose asset, then waits for the owner to choose **Install and
restart**. A fresh SQLite backup is mandatory before the install request is
handed to the isolated update helper. If the new container does not become
healthy, the helper restores the previous Compose file and attempts to recreate
the prior reader image.

The helper intentionally updates the reader first and does not replace its own
running container. The new Compose file is written to the release directory, so
the helper image is refreshed the next time `docker compose up -d` is run or the
Compose project is recreated. Stop the `updater` service if the deployment does
not permit Docker Socket access; update checks and downloads continue, while
installation falls back to the manual commands above.

Database migrations run during application initialization. Keep the prior image
and backup until the health check and library are verified.

SQLite uses a 1,000-page automatic WAL checkpoint and a 64 MiB journal-size
limit by default. Backups also request a non-blocking passive checkpoint. The
limit controls retained WAL size when readers permit a reset; it is not a hard
cap while a long-running transaction is active.
