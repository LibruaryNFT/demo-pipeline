"""Cross-platform recorder using Playwright's built-in video capture.

Runs headless Chromium with `record_video_dir` and lets Playwright write the
frames. Works on Linux, macOS and Windows with no system setup beyond
`playwright install chromium`.

Tradeoff: headless Chromium will not load real browser extensions, and it is
a throwaway profile so there is no signed-in session. If the demo has to show
either of those, use the xvfb backend instead.
"""

import asyncio
import logging
import time

from ..config import DEFAULT_FRAMERATE, ProjectConfig
from ..timelapse import Capture
from . import overlay as overlay_mod
from .timeline import apply_setup_js, play_scenes

logger = logging.getLogger(__name__)


async def record(config: ProjectConfig, segments: list[dict]) -> Capture:
    """Record the screen half of the video. Returns the capture and its timings."""
    from playwright.async_api import async_playwright

    w, h = config.resolution
    timing = config.timing
    video_dir = config.work_dir / "screen_recording"
    video_dir.mkdir(parents=True, exist_ok=True)

    # Clear stale captures so this run's output is identifiable.
    for stale in video_dir.glob("*.webm"):
        stale.unlink()

    total_duration = sum(s["duration"] for s in segments)
    logger.info("recording target duration: %.1fs", total_duration)

    # Playwright's recorder has no framerate setting, so a configured value
    # cannot be honoured here. Say so instead of silently ignoring it.
    if config.framerate != DEFAULT_FRAMERATE:
        logger.warning(
            "framerate=%d ignored: the playwright backend records at "
            "Playwright's own rate. Use backend='xvfb' to control framerate.",
            config.framerate,
        )

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": w, "height": h},
            record_video_dir=str(video_dir),
            record_video_size={"width": w, "height": h},
        )
        page = await context.new_page()
        # Playwright begins writing frames when the page opens, so everything
        # from here to the first scene — the initial load and the settle —
        # is footage that precedes t0.
        recording_started = time.monotonic()

        await page.goto(
            config.start_url,
            wait_until=timing.wait_until,
            timeout=timing.page_load_ms,
        )
        await asyncio.sleep(timing.startup_s)

        await overlay_mod.install(page, config)
        await apply_setup_js(page, config)
        timeline = await play_scenes(page, config, segments)

        await asyncio.sleep(timing.tail_s)
        await context.close()
        await browser.close()

    videos = list(video_dir.glob("*.webm"))
    if not videos:
        raise RuntimeError(f"recording produced no video file in {video_dir}")
    logger.info("screen recording done: %s", videos[0])
    return Capture(
        path=videos[0],
        lead_in_s=max(timeline.t0 - recording_started, 0.0),
        scenes=timeline.scenes,
    )
