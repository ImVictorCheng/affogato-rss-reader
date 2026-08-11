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

Automatic installation and rollback are disabled in 0.4.0. Updates are
performed manually from the verified Release assets. For a migration from
0.3.1 or earlier, remove the old privileged updater before replacing Compose:

```console
docker compose stop updater
docker compose rm -f updater
docker compose ps -a updater
```

The new updater profile is opt-in; omitting it does not remove an existing
container. Keep the prior image and backup until the health check and database
are verified.

The helper intentionally updates the reader first and does not replace its own
Update checks and downloads remain available, but installation always follows
the manual commands above.

Database migrations run during application initialization. Keep the prior image
and backup until the health check and library are verified.

SQLite uses a 1,000-page automatic WAL checkpoint and a 64 MiB journal-size
limit by default. Backups also request a non-blocking passive checkpoint. The
limit controls retained WAL size when readers permit a reset; it is not a hard
cap while a long-running transaction is active.
