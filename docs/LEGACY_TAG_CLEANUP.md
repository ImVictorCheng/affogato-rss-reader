# Dormant provenance cleanup workflow

The settings UI now manages tags with checkboxes, select all, invert selection,
and deletion. The 50-article preview and full-run approval are also dormant;
see [DORMANT_AUTO_TAG_PREVIEW.md](DORMANT_AUTO_TAG_PREVIEW.md). When restored,
they do not require a cleanup review. Deletion removes the selected tags and their article/feed
associations, keeping articles and feeds. Active brief schedule references
block the entire deletion batch; disabled schedules have deleted IDs removed.

The earlier inferred-association cleanup workflow is kept behind disabled
feature switches rather than commented out, so its code remains type-checked
and its backend behavior remains tested:

- `LEGACY_AUTO_TAG_CLEANUP_ENABLED` in `web/src/components/SettingsModal.tsx`
  controls the old UI and its review preflights.
- `LEGACY_AUTO_TAG_CLEANUP_ENABLED` in `backend/app/auto_tag.py` controls the
  cleanup routes and the review requirement before preview creation/approval.
- The existing cleanup helpers, API client methods, response models, styles,
  and tests are retained. Backend tests opt into the legacy switch explicitly.

To restore the workflow, enable both switches together, restore the cleanup
instructions in the preview panel, and add browser coverage for review,
stale snapshots, keeping manual links, and removing inferred links. Do not
enable only one switch: frontend and backend review requirements must agree.

Tag/taxonomy mutations still invalidate preview results, and the backend still
checks the policy version and article sample before approving a full run.
