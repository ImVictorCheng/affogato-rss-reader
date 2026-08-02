# Contributing

Issues and focused pull requests are welcome.

1. Create a branch from `main`.
2. Keep changes generic; source-specific adapters must remain optional.
3. Add or update tests for behavior changes.
4. Run backend tests, Vitest, the production web build, and relevant Playwright tests.
5. Update `CHANGELOG.md` for user-visible changes.

Use Python 3.12, Node 22, conventional readable commit messages, and avoid
committing databases, `.env`, generated web assets, or dependency directories.
All repository text files must be saved as UTF-8 without a BOM. Run
`python scripts/check_utf8.py` before committing; CI runs the same validation.

All dropdown controls must use the shared components in
`web/src/components/Common.tsx`: use `SelectMenu` for fixed choices and
`ComboBox` when users may enter a custom value. Do not add native `select`,
`datalist`, or `input[list]` controls; their platform menus cannot match the
application design. Preserve the shared `app-dropdown` menu structure and
styles instead of creating a screen-specific dropdown. Run
`npm run check:ui` from `web/` before committing; CI enforces this convention.

By contributing, you agree that your contribution is licensed under MIT.
