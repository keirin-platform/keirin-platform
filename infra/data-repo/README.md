# keirin-data (template)

Files in this directory are copied into the private data repository
`keirin-platform/keirin-data`. Collected data lives only there.

- `.github/workflows/collect.yml` — daily cron (03:30 JST) that calls the
  reusable workflow `keirin-platform/keirin-platform/.github/workflows/collect.yml@main`.
  Run it manually from the Actions tab with `date` / `to` to backfill a range.

Data layout: see `docs/data-schema.md` in keirin-platform.
