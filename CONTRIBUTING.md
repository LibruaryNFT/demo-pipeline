# Contributing

Contributions are welcome. This is a small, focused tool and the bar is
mostly "does it still do one thing well".

## Getting set up

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m playwright install chromium
.venv/bin/python -m demo_pipeline.doctor
```

The doctor tells you what is missing before you waste a render finding out.

## Before opening a pull request

```bash
.venv/bin/ruff check .
.venv/bin/python -m pytest tests/ -q
.venv/bin/python scripts/scan_secrets.py --selftest
.venv/bin/python scripts/scan_secrets.py
```

CI runs exactly these. The test suite touches no network and spawns no
browser, so it finishes in about a second — there is no reason to skip it.

For anything that changes what lands on screen, also render the example and
actually look at the output:

```bash
.venv/bin/python examples/example_demo.py
```

A render that exits 0 is not evidence the video is right. Several bugs in
this project's history produced a perfectly successful render of a broken
video: a missing font renders blank title cards, a Wayland-captured frame
comes out solid black, and a negative fade start killed only short clips.
Pull a frame out with `ffmpeg -ss <t> -i out.mp4 -frames:v 1 f.png` and look
at it.

## What makes a good change

- **Keep the engine generic.** Anything specific to one product — narration,
  selectors, credentials, branding — belongs in the caller's config, not in
  here. That separation is why the package is reusable at all.
- **New settings go on `Encoding` or `Timing`,** with a default that suits a
  quick demo. Avoid introducing a constant in the render path; that is the
  class of thing this project has repeatedly had to undo.
- **Do not let a knob lie.** If a setting cannot be honoured on a backend,
  warn rather than ignoring it. `framerate` on the playwright backend is the
  worked example.
- **Tests are cheap here.** If you fix a bug, add the case that would have
  caught it. Prefer a test that fails for the original reason over one that
  merely covers the line.

## Reporting a bug

Include the output of `python -m demo_pipeline.doctor` (add `--xvfb` if you
are using that backend), your OS, and which backend you are on. Those three
answer most questions immediately.

If a render fails, the tail of the log names the stage — narration, screen
recording, or compose — and that is usually enough to localise it. For xvfb
failures, `chrome.log` in the work directory is often the real story.

## Platform help wanted

The `playwright` backend is cross-platform by construction but has only been
exercised on Linux. macOS and Windows reports are genuinely useful; the font
path in `config.default_font()` is the most likely thing to need adjusting.

## Licence

By contributing you agree that your contributions are licensed under the
Apache License 2.0, the same terms as the rest of the project. Please keep
the `NOTICE` file intact in derivative works.
