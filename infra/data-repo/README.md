# keirin-data (template)

Files in this directory are copied into the private data repository
`keirin-platform/keirin-data`. Collected data lives only there.

- `.github/workflows/collect.yml` — daily cron (03:30 JST) that calls the
  reusable workflow `keirin-platform/keirin-platform/.github/workflows/collect.yml@main`.
  Run it manually from the Actions tab with `date` / `to` to backfill a range.

- `streamlit_app.py` + `requirements.txt` + `.streamlit/config.toml` — the race
  card viewer on Streamlit Community Cloud. The viewer code is installed from
  keirin-platform at the commit pinned in `requirements.txt` (`@main` in this
  template; pin a SHA in the data repository). Each data commit updates the app.
  See `docs/deploy-streamlit.md` in keirin-platform.

Data layout: see `docs/data-schema.md` in keirin-platform.
