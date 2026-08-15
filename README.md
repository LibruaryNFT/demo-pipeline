# demo-pipeline

Narrated screen-recording demo videos for any web app, rendered from a config file.

You write the narration and describe the scenes. The pipeline generates the voiceover, drives a real browser through your app in time with it, and cuts the result together with intro and outro cards. Re-running produces the same video, so changing one line of narration means re-rendering rather than re-recording.

Built for product demos, launch clips, onboarding walkthroughs, conference submissions, and anything else where you would otherwise open a screen recorder and fumble the first take.

## Status

Working and used, but young. What has actually been exercised, so you know where the edges are:

- Both backends verified end to end on Linux, output inspected frame by frame.
- The `playwright` backend is cross-platform *by construction* (Playwright's own recorder plus ffmpeg, with per-OS font defaults). It has **not** been run on macOS or Windows. If you are the first, expect the font path to be the thing that needs attention.
- Demos have been rendered against simple public pages. A heavy SPA will likely want `timing.wait_until="networkidle"` and a larger `settle_s`.
- Narration costs money. OpenAI TTS is billed per character, so a 60-second script is fractions of a cent, but a render loop with the cache disabled is not free.

## How it works

Three stages. Each is independently usable if you only need part of it.

```
[narration text]          [scenes + actions]         [branding / cards]
       |                          |                          |
       v                          v                          v
   audio.py                  recording/                  compose.py
   ---------                 ----------                  ----------
   OpenAI TTS                browser automation          ffmpeg concat
   cached per scene          timed to the audio          title cards
   -> .mp3 per scene         -> screen capture           -> final .mp4
```

Scene length is driven by how long its narration takes to speak. The recorder runs each scene's action, then holds until that scene's absolute end time, so a slow click steals from its own scene rather than pushing everything after it out of sync.

## Install

Requires Python 3.10+, `ffmpeg` and `ffprobe` on PATH, and an OpenAI API key for the narration.

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m playwright install chromium
export OPENAI_API_KEY=...    # or put it in .env (gitignored)
```

That is everything the default `playwright` backend needs. The `xvfb` backend additionally wants a virtual X server and a real Chrome, which is Linux only:

```bash
sudo apt install xvfb ffmpeg      # plus Google Chrome from its own package
```

Check your setup before rendering anything:

```bash
.venv/bin/python -m demo_pipeline.doctor          # playwright backend
.venv/bin/python -m demo_pipeline.doctor --xvfb   # also the xvfb stack
```

It checks each dependency in isolation — ffmpeg, the drawtext font, Chromium, the API key, and for `--xvfb` that x11grab really captures a non-black frame — and exits non-zero if anything the backend needs is missing. Run it first when a render fails; it turns "ffmpeg exited 222" into a named missing dependency.

Then render the example, which exercises TTS, the browser and ffmpeg end to end against `example.com`:

```bash
.venv/bin/python examples/example_demo.py
```

## Quick start

```python
from demo_pipeline import Branding, ProjectConfig, Scene, render

render(ProjectConfig(
    name="My App",
    output_path="out/demo.mp4",
    start_url="https://myapp.example",
    branding=Branding(tagline="Does the thing", author="Me", link="myapp.example"),
    scenes=[
        Scene(id="hook",   narration="Here is the problem.",  action="wait"),
        Scene(id="tour",   narration="Here is the fix.",      action="scroll", action_params={"y": 600}),
        Scene(id="close",  narration="Try it today.",         action="wait"),
    ],
))
```

A complete worked example, including a custom action handler, is in [examples/example_demo.py](examples/example_demo.py). Run it against `example.com` to check your setup end to end.

## Recording backends

Set `backend=` on the config.

| | `playwright` (default) | `xvfb` |
|---|---|---|
| Platforms | Linux, macOS, Windows (only Linux tested) | Linux only |
| Setup | `playwright install chromium` | Xvfb, system Chrome, a dedicated profile |
| Browser | headless Chromium, throwaway profile | real Chrome, persistent profile |
| Extensions | not supported | supported |
| Signed-in sessions | via `setup_js` stubbing | real, survives across runs |
| **What lands in frame** | **page viewport only** | **the whole browser window** |

That last row is the one that surprises people. `playwright` records the viewport, so the video is pure app. `xvfb` records the X display, so the tab strip and address bar are in the shot. That reads as more authentic when the demo is about a browser extension, and as clutter otherwise. To get a clean frame:

```python
ProjectConfig(backend="xvfb", chrome_flags=("--kiosk", "--hide-scrollbars"), ...)
```

The mouse pointer is hidden by default (`draw_mouse=False`), because on a virtual display it never moves and would just park a stray arrow mid-frame. Set it `True` if you are demonstrating something cursor-related.

Start with `playwright`. It needs no system setup and covers most demos.

Reach for `xvfb` when the demo has to show something headless Chromium cannot reproduce: a browser extension, or a genuinely signed-in session. It exists because of three constraints that are worth knowing before you debug it:

1. Playwright's bundled Chromium refuses to run on some recent Linux distributions, and the check is in the installer. So `xvfb` launches the system Chrome and attaches over CDP instead of letting Playwright launch it.
2. Real browser extensions do not load in legacy headless mode.
3. GNOME Wayland blocks `ffmpeg -f x11grab` from capturing XWayland clients even with Chrome forced to `--ozone-platform=x11`; the frame comes out solid black. Xvfb is a real X server with no Wayland involved, so x11grab works against it.

A virtual display also makes recordings deterministic, since no notification popup can wander into frame.

`xvfb` requires `chrome_profile`. Chrome 148+ refuses a remote-debugging port on the default profile, so point it somewhere else:

```python
ProjectConfig(
    backend="xvfb",
    chrome_profile="~/.config/chrome-demo-profile",
    ...
)
```

## Scenes and actions

A scene names its action as a string. The engine resolves built-ins first, then your `action_handlers` overrides, so you can replace a built-in or add your own verb.

| Action | Params | Behaviour |
|---|---|---|
| `wait` | | Hold the current view for the scene's duration |
| `scroll` | `y`, `relative: bool`, `behavior`, `wait` | Scroll to (or by) a y offset |
| `navigate` | `url`, `wait_until`, `settle`, `timeout_ms` | Go to a URL, wait for load, settle |
| `click` | `selector: str \| list`, `settle`, `timeout_ms` | Click by selector; a list is tried in order until one works |
| `hover` | `selector`, `settle`, `timeout_ms` | Hover without clicking, for drawing attention to a control you do not want to fire |
| `evaluate` | `js: str`, `settle` | Run arbitrary JS, without defining a handler |

Every param falls back to the project's `Timing` defaults, so a single slow scene can be tuned in place rather than by slowing the whole render:

```python
Scene(id="load", narration="...", action="navigate",
      action_params={"url": "/reports", "settle": 6.0})
```

`click` accepting a list matters more than it looks. A UI that renders a control as a button in one state and a tab in another needs candidates, and that pattern showed up in every real demo we checked:

```python
Scene(id="flip", narration="...", action="click",
      action_params={"selector": [
          "button:has-text('Burn')",
          "[role='tab']:has-text('Redeem')",
      ]})
```

Custom handlers are async with signature `(page, params, duration) -> float`, returning the seconds spent on the active part. `page` is a Playwright Page under both backends.

```python
async def open_settings(page, params, duration):
    await page.click("button:has-text('Settings')")
    await page.wait_for_selector(".settings-panel")
    return 2.0

ProjectConfig(..., action_handlers={"open_settings": open_settings})
```

Prefer text-based selectors like `button:has-text('Save')` over class selectors. The rendered UI is the source of truth and `:has-text` survives most class renames.

A handler that throws is logged and skipped. One broken selector costs that scene's choreography, not the whole render.

## Showing populated state without real credentials

`setup_js` is arbitrary JavaScript evaluated once after the page loads. Use it to stub an API, seed local storage, or mock a browser extension so the demo shows a full, realistic screen without you signing in as anyone.

```python
ProjectConfig(
    setup_js="window.__API_MOCK__ = { user: 'Demo User', items: 42 };",
    ...
)
```

A page load tears injected state down, so the engine re-applies `setup_js` whenever a scene changes the page URL. That covers both a built-in `navigate` and a custom handler doing its own `page.goto`, without the handler needing to know anything about it.

## Branding and title cards

`Branding` auto-generates the intro and outro. Every field is optional and blank fields are skipped, so a bare `Branding()` still gives you a clean name-only card.

```python
Branding(
    tagline="Does the thing",   # intro, under the name
    author="Your Name",         # outro, rendered as "Built by ..."
    link="myapp.example",       # outro
    context="Internal Demo",    # outro, free text for the occasion
    accent="0x50c878",          # heading colour
)
```

Line spacing scales with font size, so cards with one line and four lines are both centred and neither collides.

For full control, pass `intro=` / `outro=` as `TitleCard` objects and the generated defaults are bypassed entirely.

## Tuning

Defaults target a quick demo: fast to iterate on, good enough to put in front of people. Nothing is baked in, so when a default does not suit, override it rather than patching the engine.

```python
from demo_pipeline import Encoding, ProjectConfig, Timing

ProjectConfig(
    ...,
    encoding=Encoding(crf=18, preset="slow", volume_boost_db=12),
    timing=Timing(wait_until="networkidle", settle_s=4.0, page_load_ms=60000),
)
```

`Encoding` covers codecs, `crf`, preset, audio bitrate, the volume boost, fade timings, and title-card line spacing. `Timing` covers every wait and timeout, including the xvfb backend's CDP and Xvfb startup budgets.

Two settings worth knowing about:

- **`timing.wait_until`** defaults to `domcontentloaded`, which is fast and correct for most apps. Use `networkidle` for a data-heavy dashboard that renders after XHR, but not for a page that polls, because it will never go idle.
- **`display_num` and `cdp_port`** (xvfb only) must be unique per concurrent render on one machine, or two runs will fight over the display and the port.

Tool paths (`ffmpeg`, `ffprobe`, `font`) and the Chrome binary search order (`chrome_binaries`) are config too, so a machine with unusual paths needs no code change.

## Narration caching

Each scene's MP3 is cached on disk as `seg_NN_<scene_id>.mp3` in the `*_audio` directory next to your output. The filename is the whole cache key, so **if you change a scene's narration text, delete its MP3** or the old audio will be reused.

Iterating on choreography while leaving narration alone costs nothing in API calls.

## Development

```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/ruff check .
```

The test suite covers config validation, title-card layout, drawtext escaping, ffprobe parsing, the audio cache, and action dispatch. It does not touch the network or spawn a browser, so it runs in about a second.

For an end-to-end check that does exercise TTS, the browser and ffmpeg, run the example.

## Layout

```
src/demo_pipeline/
├── config.py                     ProjectConfig, Scene, TitleCard, Branding, Encoding, Timing
├── audio.py                      TTS narration + per-scene cache
├── actions.py                    built-in scene actions, handler dispatch
├── compose.py                    ffmpeg mux, title cards, volume boost
├── doctor.py                     environment diagnostic (python -m demo_pipeline.doctor)
└── recording/
    ├── __init__.py               backend dispatch
    ├── timeline.py               scene sequencing, shared by both backends
    ├── playwright_backend.py     cross-platform, headless
    └── xvfb_backend.py           Linux, real Chrome over CDP
```

The backends differ only in how they capture frames. Scene sequencing lives in `timeline.py`, so the two cannot drift apart.
