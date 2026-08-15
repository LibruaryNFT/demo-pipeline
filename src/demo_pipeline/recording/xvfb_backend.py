"""Linux recorder using Xvfb + system Chrome + Playwright CDP + ffmpeg x11grab.

Use this backend when the demo must show a real browser: a loaded extension,
a signed-in session, anything that headless Chromium cannot reproduce.

Three independent constraints forced this design, all of them still true:

1. Playwright's bundled Chromium refuses to run on some recent Linux
   distributions; the OS support check lives in the installer and cannot be
   bypassed. So we launch the system `google-chrome` instead and attach over
   CDP rather than letting Playwright launch it.
2. Real browser extensions do not load in legacy headless mode.
3. GNOME Wayland blocks `ffmpeg -f x11grab` from capturing XWayland clients
   even with Chrome forced to `--ozone-platform=x11`; the captured frame
   comes out solid black. Xvfb is a real X server with no Wayland involved,
   so x11grab works against it.

Bonus: because the display is virtual, recordings are deterministic. No
notification popup or stray window can wander into frame.

Chrome 148+ refuses a remote-debugging port on the default profile, so
`chrome_profile` must point somewhere other than your real user-data-dir.
"""

import asyncio
import logging
import os
import subprocess
import time
from pathlib import Path

from ..actions import resolve_handlers, run_scene_action
from ..config import ProjectConfig

logger = logging.getLogger(__name__)

CHROME_BINARIES = ("google-chrome", "google-chrome-stable", "chromium")
_CHROME_PATTERN = "/opt/google/chrome/chrome"


def _kill_chrome() -> None:
    """Release any existing lock on the profile directory."""
    subprocess.run(
        ["pkill", "-TERM", "-f", _CHROME_PATTERN], capture_output=True
    )
    time.sleep(2)


def _wait_for_cdp(cdp_url: str, timeout_s: float = 25) -> bool:
    import httpx

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            r = httpx.get(f"{cdp_url}/json/version", timeout=1.0)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def _spawn_xvfb(display: str, width: int, height: int) -> subprocess.Popen:
    proc = subprocess.Popen(
        [
            "Xvfb", display,
            "-screen", "0", f"{width}x{height}x24",
            "-nolisten", "tcp",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    time.sleep(1.5)
    if proc.poll() is not None:
        raise RuntimeError(f"Xvfb failed to start (exit code {proc.returncode})")
    return proc


def _resolve_chrome() -> str:
    from shutil import which

    for candidate in CHROME_BINARIES:
        if which(candidate):
            return candidate
    raise RuntimeError(
        "no Chrome binary found — looked for: " + ", ".join(CHROME_BINARIES)
    )


def _launch_chrome(config: ProjectConfig, chrome_log: Path) -> subprocess.Popen:
    env = os.environ.copy()
    env["DISPLAY"] = config.display_num
    env.pop("WAYLAND_DISPLAY", None)
    env["XDG_SESSION_TYPE"] = "x11"
    w, h = config.resolution
    return subprocess.Popen(
        [
            _resolve_chrome(),
            f"--user-data-dir={config.chrome_profile}",
            f"--remote-debugging-port={config.cdp_port}",
            f"--window-size={w},{h}",
            "--window-position=0,0",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-features=Translate",
            config.start_url,
        ],
        env=env,
        stdout=open(chrome_log, "w"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def _start_ffmpeg(
    config: ProjectConfig, total_duration_s: float, out_path: Path
) -> subprocess.Popen:
    w, h = config.resolution
    return subprocess.Popen(
        [
            config.ffmpeg, "-y", "-loglevel", "error",
            "-f", "x11grab",
            "-video_size", f"{w}x{h}",
            "-framerate", str(config.framerate),
            "-i", config.display_num,
            "-t", f"{total_duration_s:.2f}",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-pix_fmt", "yuv420p",
            str(out_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


async def record(config: ProjectConfig, segments: list[dict]) -> Path:
    """Record the screen half of the video. Returns the captured .mp4 path."""
    from playwright.async_api import async_playwright

    config.work_dir.mkdir(parents=True, exist_ok=True)
    chrome_log = config.work_dir / "chrome.log"
    raw_video = config.work_dir / "screen_raw.mp4"

    total_duration = sum(s["duration"] for s in segments)
    logger.info("recording target duration: %.1fs", total_duration)

    _kill_chrome()
    xvfb = _spawn_xvfb(config.display_num, *config.resolution)
    logger.info("Xvfb up on %s (PID %d)", config.display_num, xvfb.pid)

    chrome = None
    ffmpeg_proc = None
    try:
        chrome = _launch_chrome(config, chrome_log)
        logger.info("Chrome launched PID %d", chrome.pid)

        cdp_url = f"http://localhost:{config.cdp_port}"
        if not _wait_for_cdp(cdp_url):
            tail = chrome_log.read_text()[-2000:]
            raise RuntimeError(f"CDP never came up. Chrome log tail:\n{tail}")
        logger.info("CDP up at %s", cdp_url)

        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(cdp_url)
            ctx = browser.contexts[0]

            page = None
            for pg in ctx.pages:
                if config.start_url.rstrip("/") in pg.url:
                    page = pg
                    break
            if page is None:
                page = ctx.pages[0]
                await page.goto(config.start_url, wait_until="domcontentloaded")

            await page.wait_for_load_state("domcontentloaded", timeout=15000)
            await asyncio.sleep(3)

            if config.setup_js:
                await page.evaluate(config.setup_js)
                await asyncio.sleep(0.5)

            handlers = resolve_handlers(config)

            # Start recording only once the page is settled, so the intro
            # frame is the app and not a white flash.
            ffmpeg_proc = _start_ffmpeg(config, total_duration, raw_video)
            logger.info(
                "ffmpeg recording started PID %d (capturing %s)",
                ffmpeg_proc.pid, config.display_num,
            )

            t0 = time.monotonic()
            elapsed = 0.0
            for i, seg in enumerate(segments):
                scene = seg["scene"]
                dur = seg["duration"]
                scene_end = elapsed + dur
                logger.info(
                    "[%d/%d] %s -> %s (%.1fs)",
                    i + 1, len(segments), scene.id, scene.action, dur,
                )

                await run_scene_action(handlers, page, scene, dur)

                wait_for = (t0 + scene_end) - time.monotonic()
                if wait_for > 0:
                    await asyncio.sleep(wait_for)
                elapsed = scene_end

            await browser.close()

        try:
            ffmpeg_proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            ffmpeg_proc.kill()
            logger.warning("ffmpeg overran timeout; killed")

        if not raw_video.exists() or raw_video.stat().st_size < 10_000:
            raise RuntimeError(f"recording produced no/tiny output: {raw_video}")

        logger.info(
            "screen recording done: %s (%d KB)",
            raw_video, raw_video.stat().st_size // 1024,
        )
        return raw_video

    finally:
        if ffmpeg_proc and ffmpeg_proc.poll() is None:
            ffmpeg_proc.kill()
        if chrome and chrome.poll() is None:
            subprocess.run(
                ["pkill", "-TERM", "-f", _CHROME_PATTERN], capture_output=True
            )
        if xvfb.poll() is None:
            xvfb.terminate()
            try:
                xvfb.wait(timeout=3)
            except subprocess.TimeoutExpired:
                xvfb.kill()
