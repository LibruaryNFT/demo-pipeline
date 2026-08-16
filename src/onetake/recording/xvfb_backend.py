"""Linux recorder using Xvfb + system Chrome + Playwright CDP + ffmpeg x11grab.

Use this backend when the demo must show a real browser: a loaded extension,
a signed-in session, anything that headless Chromium cannot reproduce.

Three independent constraints forced this design, all of them still true:

1. Playwright's bundled Chromium refuses to run on some recent Linux
   distributions; the OS support check lives in the installer and cannot be
   bypassed. So we launch the system Chrome instead and attach over CDP
   rather than letting Playwright launch it.
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
from shutil import which

from ..config import ProjectConfig
from ..timelapse import Capture
from . import overlay as overlay_mod
from .timeline import apply_setup_js, play_scenes

logger = logging.getLogger(__name__)


def _resolve_chrome(config: ProjectConfig) -> str:
    """First configured Chrome binary present on PATH."""
    for candidate in config.chrome_binaries:
        found = which(candidate)
        if found:
            return found
    raise RuntimeError(
        "no Chrome binary found — looked for: "
        + ", ".join(config.chrome_binaries)
    )


def _kill_chrome(config: ProjectConfig, chrome_path: str) -> None:
    """Release any existing lock on the profile directory.

    Matched on the profile path rather than the binary path: it is the
    profile lock we care about, the binary may be chromium rather than
    Chrome, and matching the binary alone would kill the operator's own
    browser windows.
    """
    subprocess.run(
        ["pkill", "-TERM", "-f", f"--user-data-dir={config.chrome_profile}"],
        capture_output=True,
    )
    time.sleep(config.timing.chrome_kill_wait_s)


def _wait_for_cdp(cdp_url: str, timeout_s: float) -> bool:
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


def _spawn_xvfb(config: ProjectConfig) -> subprocess.Popen:
    if not which("Xvfb"):
        raise RuntimeError(
            "Xvfb not found on PATH — install it, or use backend='playwright'"
        )
    w, h = config.resolution
    proc = subprocess.Popen(
        [
            "Xvfb", config.display_num,
            "-screen", "0", f"{w}x{h}x24",
            "-nolisten", "tcp",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    time.sleep(config.timing.xvfb_startup_s)
    if proc.poll() is not None:
        raise RuntimeError(
            f"Xvfb failed to start on {config.display_num} "
            f"(exit code {proc.returncode}) — is that display already in use?"
        )
    return proc


def _launch_chrome(
    config: ProjectConfig, chrome_path: str, chrome_log: Path
) -> subprocess.Popen:
    env = os.environ.copy()
    env["DISPLAY"] = config.display_num
    env.pop("WAYLAND_DISPLAY", None)
    env["XDG_SESSION_TYPE"] = "x11"
    w, h = config.resolution
    return subprocess.Popen(
        [
            chrome_path,
            f"--user-data-dir={config.chrome_profile}",
            f"--remote-debugging-port={config.cdp_port}",
            f"--window-size={w},{h}",
            "--window-position=0,0",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-features=Translate",
            *config.chrome_flags,
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
    enc = config.encoding
    return subprocess.Popen(
        [
            config.ffmpeg, "-y", "-loglevel", "error",
            "-f", "x11grab",
            "-draw_mouse", "1" if config.draw_mouse else "0",
            "-video_size", f"{w}x{h}",
            "-framerate", str(config.framerate),
            "-i", config.display_num,
            "-t", f"{total_duration_s:.2f}",
            "-c:v", enc.video_codec,
            "-preset", enc.capture_preset,
            "-pix_fmt", enc.pixel_format,
            str(out_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


async def record(config: ProjectConfig, segments: list[dict]) -> Capture:
    """Record the screen half of the video. Returns the capture and its timings."""
    from playwright.async_api import async_playwright

    timing = config.timing
    config.work_dir.mkdir(parents=True, exist_ok=True)
    chrome_log = config.work_dir / "chrome.log"
    raw_video = config.work_dir / "screen_raw.mp4"

    total_duration = sum(s["duration"] for s in segments)
    logger.info("recording target duration: %.1fs", total_duration)

    chrome_path = _resolve_chrome(config)
    logger.info("using chrome binary: %s", chrome_path)

    _kill_chrome(config, chrome_path)
    xvfb = _spawn_xvfb(config)
    logger.info("Xvfb up on %s (PID %d)", config.display_num, xvfb.pid)

    chrome = None
    ffmpeg_proc = None
    try:
        chrome = _launch_chrome(config, chrome_path, chrome_log)
        logger.info("Chrome launched PID %d", chrome.pid)

        cdp_url = f"http://localhost:{config.cdp_port}"
        if not _wait_for_cdp(cdp_url, timing.cdp_timeout_s):
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
                await page.goto(config.start_url, wait_until=timing.wait_until)

            await page.wait_for_load_state(
                timing.wait_until, timeout=timing.navigate_ms
            )
            await asyncio.sleep(timing.startup_s)

            await overlay_mod.install(page, config)
            await apply_setup_js(page, config)

            # Start recording only once the page is settled, so the opening
            # frame is the app and not a white flash.
            ffmpeg_proc = _start_ffmpeg(config, total_duration, raw_video)
            recording_started = time.monotonic()
            logger.info(
                "ffmpeg recording started PID %d (capturing %s)",
                ffmpeg_proc.pid, config.display_num,
            )

            timeline = await play_scenes(page, config, segments)
            await browser.close()

        try:
            ffmpeg_proc.wait(timeout=timing.ffmpeg_flush_s)
        except subprocess.TimeoutExpired:
            ffmpeg_proc.kill()
            logger.warning("ffmpeg overran timeout; killed")

        if not raw_video.exists() or raw_video.stat().st_size < config.min_video_bytes:
            raise RuntimeError(f"recording produced no/tiny output: {raw_video}")

        logger.info(
            "screen recording done: %s (%d KB)",
            raw_video, raw_video.stat().st_size // 1024,
        )
        return Capture(
            path=raw_video,
            lead_in_s=max(timeline.t0 - recording_started, 0.0),
            scenes=timeline.scenes,
        )

    finally:
        if ffmpeg_proc and ffmpeg_proc.poll() is None:
            ffmpeg_proc.kill()
        if chrome and chrome.poll() is None:
            _kill_chrome(config, chrome_path)
        if xvfb.poll() is None:
            xvfb.terminate()
            try:
                xvfb.wait(timeout=3)
            except subprocess.TimeoutExpired:
                xvfb.kill()
