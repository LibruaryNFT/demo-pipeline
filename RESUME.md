# Resume — demotape

**Active, and at a clean stopping point.** Narrated screen-recording demo videos from a config
file. Extracted 2026-08-15 from `eco/pipelines/video`; renamed `demo-pipeline` → `onetake` →
`demotape` on 2026-08-16. Both old GitHub URLs 301 to the current one; the Python module path
does not redirect, so any venv pinned to the old name needs a reinstall.

State as of 2026-08-16: 273 tests, 0 open issues, 0 open PRs, `CI` and `Dogfood` green on main.
The full 12-item roadmap is shipped and closed.

Consumers are rewired and merged: eco (#1359, #1360 — `requirements-mcp.txt`, the hackathon
imports, the runbook, CLAUDE.md) and vaultopolis (#477 — `demos/platform_tour.py`).
eco#1344 is closed.

## The one open item

**Not on PyPI, and the name is unclaimed.** `.github/workflows/publish.yml` is ready and uses
Trusted Publishing, so there is no token to create or store. It needs one web form on pypi.org
that only Justin can fill in — the five values it wants are in that workflow's header comment.
Until then, install is from git.

## Worth knowing before changing things

- `timing.timelapse` is off by default. It re-encodes the body and changes what the picture
  shows, and it declines entirely rather than half-applying — see `src/demotape/timelapse.py`.
- `doctor` treats a missing `OPENAI_API_KEY` as a **warning**, not a failure. It was a failure
  until the Docker image first ran and proved that wrong. Do not "fix" it back.
- `Dogfood` builds the image and runs `action.yml`. Both were shipped unexecuted for a while and
  the first real run found a bug; keep them in the required set.

Read first: `README.md`.
