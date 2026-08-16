"""Probe stage — walk the scenes and record what the page actually did.

A render that exits 0 is not evidence the demo still demonstrates anything.
`run_scene_action` deliberately swallows failures so one dead selector costs
a single scene's choreography instead of the whole video, and project
handlers pile their own `try/except` fallbacks on top of that. Both are the
right call for keeping a render alive. Together they are also how a demo
rots in silence: the narration still says "drill into any edition" while the
picture sits on the page it was already showing, and nothing anywhere
reports a problem.

The probe drives the same scenes through the same handlers with the browser
open, but records nothing and generates no narration. Every call the scene
makes at the page — click, hover, goto, fill, press, wait_for_selector — is
intercepted and its outcome written down. The result is normalised to strip
anything that varies between runs, so it can be committed as a baseline and
compared later:

    python -m onetake.probe demos/tour.py --update-golden
    python -m onetake.probe demos/tour.py --golden

A mismatch means the flow changed — a renamed selector, a route that now
404s — rather than clock jitter. There are no timings in the projection at
all, by construction.

This is cheap in the two ways that matter: it spends nothing on TTS, and it
caps every sleep, so a three-minute demo probes in seconds. That is the
difference between a check you run on every commit and one you never run.
"""

import argparse
import asyncio
import importlib.util
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from .actions import resolve_handlers
from .config import ProjectConfig

logger = logging.getLogger(__name__)

GOLDEN_VERSION = 1

#: Page methods worth recording. Each is a point where a scene asserts
#: something about the DOM or the route, which is exactly where drift shows
#: up. Anything else a handler calls is passed straight through untouched.
WATCHED = ("click", "hover", "goto", "fill", "press", "wait_for_selector", "tap")

#: Sleeps are capped rather than removed. Zero would report false failures on
#: anything that needs a moment to render; Playwright's own auto-waiting
#: already covers the cases that matter, since `click` waits for its element
#: up to the configured timeout regardless of what we do here.
DEFAULT_SLEEP_CAP = 0.3


@dataclass
class Call:
    """One intercepted page call and whether it worked."""

    op: str
    target: str
    ok: bool

    def as_dict(self) -> dict:
        return {"op": self.op, "target": self.target, "ok": self.ok}


@dataclass
class SceneOutcome:
    """What one scene did, with nothing timing-dependent in it."""

    id: str
    action: str
    ok: bool
    calls: list[Call] = field(default_factory=list)
    url: str = ""

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "action": self.action,
            "ok": self.ok,
            "calls": [c.as_dict() for c in self.calls],
            "url": self.url,
        }


def normalise_target(op: str, value) -> str:
    """Reduce a call argument to the part that should be stable across runs.

    URLs keep path and query but lose scheme and host, so probing a staging
    deploy does not diff against a baseline captured on production. A list of
    selectors keeps its order, because the order is the fallback chain and a
    change to it is a real change.
    """
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return " | ".join(normalise_target(op, v) for v in value)
    text = str(value)
    if op == "goto":
        parts = urlsplit(text)
        path = parts.path or "/"
        return f"{path}?{parts.query}" if parts.query else path
    return text


class ProbePage:
    """A `Page` that records the calls scenes make at it.

    Wraps rather than subclasses, because Playwright's Page is generated and
    a project handler may reach for any attribute on it. Everything not in
    `WATCHED` is forwarded untouched, so a handler cannot tell the difference
    except that failures are noted before being re-raised.
    """

    def __init__(self, page):
        self._page = page
        self.calls: list[Call] = []

    def __getattr__(self, name):
        attr = getattr(self._page, name)
        if name not in WATCHED or not callable(attr):
            return attr

        async def recording(*args, **kwargs):
            target = normalise_target(
                name, args[0] if args else kwargs.get("url") or kwargs.get("selector")
            )
            try:
                result = await attr(*args, **kwargs)
            except Exception:
                self.calls.append(Call(name, target, ok=False))
                raise
            self.calls.append(Call(name, target, ok=True))
            return result

        return recording

    @property
    def url(self) -> str:
        return self._page.url

    def take_calls(self) -> list[Call]:
        calls, self.calls = self.calls, []
        return calls


class _CappedSleep:
    """Replace `asyncio.sleep` with a capped version for the probe's duration.

    Handlers sleep to let the picture settle for the camera. There is no
    camera here, and a demo that waits three minutes to answer "did the
    selectors resolve" is a check nobody runs. Patching the module attribute
    is blunt and reaches Playwright's internals too, which is why the cap is
    a small positive number rather than zero.
    """

    def __init__(self, cap: float):
        self.cap = cap
        self._real = asyncio.sleep

    def __enter__(self):
        real, cap = self._real, self.cap

        async def capped(delay, *args, **kwargs):
            if isinstance(delay, (int, float)) and delay > cap:
                delay = cap
            return await real(delay, *args, **kwargs)

        asyncio.sleep = capped
        return self

    def __exit__(self, *exc):
        asyncio.sleep = self._real
        return False


async def probe_async(
    config: ProjectConfig, sleep_cap: float = DEFAULT_SLEEP_CAP
) -> dict:
    """Walk every scene with a live browser, recording outcomes only."""
    from playwright.async_api import async_playwright

    from .recording.timeline import apply_setup_js

    handlers = resolve_handlers(config)
    timing = config.timing
    width, height = config.resolution
    outcomes: list[SceneOutcome] = []

    with _CappedSleep(sleep_cap):
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": width, "height": height}
            )
            raw = await context.new_page()
            page = ProbePage(raw)

            await raw.goto(
                config.start_url,
                wait_until=timing.wait_until,
                timeout=timing.page_load_ms,
            )
            await apply_setup_js(page, config)
            page.take_calls()

            for scene in config.scenes:
                url_before = page.url
                failure: Exception | None = None
                handler = handlers.get(scene.action)
                try:
                    if handler is None:
                        raise RuntimeError(f"no handler for action {scene.action!r}")
                    # Deliberately NOT run_scene_action: its whole job is to
                    # swallow the exception this probe exists to surface.
                    from .actions import _takes_timing

                    if _takes_timing(handler):
                        await handler(page, scene.action_params, 0.0, timing)
                    else:
                        await handler(page, scene.action_params, 0.0)
                except Exception as e:
                    failure = e
                    logger.warning("scene %s: %s", scene.id, e)

                if config.setup_js and page.url != url_before:
                    await apply_setup_js(page, config)

                outcomes.append(
                    SceneOutcome(
                        id=scene.id,
                        action=scene.action,
                        ok=failure is None,
                        calls=page.take_calls(),
                        url=normalise_target("goto", page.url),
                    )
                )

            await context.close()
            await browser.close()

    return build_golden(config, outcomes)


def probe(config: ProjectConfig, sleep_cap: float = DEFAULT_SLEEP_CAP) -> dict:
    """Synchronous wrapper around :func:`probe_async`."""
    return asyncio.run(probe_async(config, sleep_cap))


def build_golden(config: ProjectConfig, outcomes: list[SceneOutcome]) -> dict:
    return {
        "version": GOLDEN_VERSION,
        "name": config.name,
        "scenes": [o.as_dict() for o in outcomes],
    }


def golden_path(config: ProjectConfig) -> Path:
    return config.output_path.parent / f"{config.output_path.stem}.golden.json"


def write_golden(config: ProjectConfig, golden: dict) -> Path:
    path = golden_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(golden, indent=2) + "\n")
    return path


def read_golden(config: ProjectConfig) -> dict | None:
    path = golden_path(config)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def diff_golden(expected: dict, actual: dict) -> list[str]:
    """Field-level differences, as `path: expected X, got Y` lines.

    Scenes are matched by id rather than position, so inserting a scene
    reports one addition instead of renaming every scene after it.
    """
    lines: list[str] = []

    for key in ("version", "name"):
        if expected.get(key) != actual.get(key):
            lines.append(f"{key}: expected {expected.get(key)!r}, got {actual.get(key)!r}")

    exp_scenes = {s["id"]: s for s in expected.get("scenes", [])}
    act_scenes = {s["id"]: s for s in actual.get("scenes", [])}

    for missing in exp_scenes.keys() - act_scenes.keys():
        lines.append(f"scenes.{missing}: expected present, got missing")
    for added in act_scenes.keys() - exp_scenes.keys():
        lines.append(f"scenes.{added}: expected missing, got present")

    exp_order = [s["id"] for s in expected.get("scenes", []) if s["id"] in act_scenes]
    act_order = [s["id"] for s in actual.get("scenes", []) if s["id"] in exp_scenes]
    if exp_order != act_order:
        lines.append(f"scenes order: expected {exp_order}, got {act_order}")

    for sid in exp_order:
        exp, act = exp_scenes[sid], act_scenes[sid]
        for key in ("action", "ok", "url"):
            if exp.get(key) != act.get(key):
                lines.append(
                    f"scenes.{sid}.{key}: expected {exp.get(key)!r}, got {act.get(key)!r}"
                )
        e_calls, a_calls = exp.get("calls", []), act.get("calls", [])
        if len(e_calls) != len(a_calls):
            lines.append(
                f"scenes.{sid}.calls: expected {len(e_calls)} call(s), got {len(a_calls)}"
            )
        for i, (e, a) in enumerate(zip(e_calls, a_calls, strict=False)):
            if e != a:
                lines.append(f"scenes.{sid}.calls[{i}]: expected {e!r}, got {a!r}")

    return lines


def load_config(path: Path) -> ProjectConfig:
    """Import a demo script and return the ProjectConfig it defines.

    Importing runs the module, which is the same trust boundary as running
    the demo itself — the config is Python and always was.
    """
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    configs = [v for v in vars(module).values() if isinstance(v, ProjectConfig)]
    if not configs:
        raise RuntimeError(f"no ProjectConfig found in {path}")
    if len(configs) > 1:
        raise RuntimeError(
            f"{len(configs)} ProjectConfig objects in {path} — expected exactly one"
        )
    return configs[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m onetake.probe",
        description="Walk a demo's scenes and report what the page did. "
        "No video, no narration, no API key.",
    )
    parser.add_argument("config", type=Path, help="path to the demo script")
    parser.add_argument(
        "--update-golden", action="store_true", help="write the baseline and exit 0"
    )
    parser.add_argument(
        "--golden", action="store_true", help="compare against the baseline"
    )
    parser.add_argument(
        "--sleep-cap",
        type=float,
        default=DEFAULT_SLEEP_CAP,
        help=f"longest sleep a handler may take, in seconds (default {DEFAULT_SLEEP_CAP})",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    config = load_config(args.config)
    actual = probe(config, args.sleep_cap)

    failed = [s for s in actual["scenes"] if not s["ok"]]
    for scene in actual["scenes"]:
        bad = [c for c in scene["calls"] if not c["ok"]]
        mark = "ok " if scene["ok"] and not bad else "FAIL"
        print(f"  {mark}  {scene['id']:<24} {scene['action']:<24} {scene['url']}")
        for call in bad:
            print(f"          ! {call['op']} did not resolve: {call['target']}")

    if args.update_golden:
        path = write_golden(config, actual)
        print(f"\nwrote {path}")
        return 0

    if args.golden:
        expected = read_golden(config)
        if expected is None:
            print(f"\nno baseline at {golden_path(config)} — run --update-golden first")
            return 1
        differences = diff_golden(expected, actual)
        if differences:
            print(f"\n{len(differences)} difference(s) from the baseline:")
            for line in differences:
                print(f"  {line}")
            return 1
        print("\nmatches the baseline")
        return 0

    # Plain run: a swallowed failure is still a failure worth an exit code.
    broken = failed or [
        s for s in actual["scenes"] if any(not c["ok"] for c in s["calls"])
    ]
    if broken:
        print(f"\n{len(broken)} scene(s) did not do what they claim")
        return 1
    print("\nevery scene resolved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
