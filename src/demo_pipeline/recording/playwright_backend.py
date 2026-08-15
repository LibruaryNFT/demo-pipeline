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
from pathlib import Path

from ..config import DEFAULT_FRAMERATE, ProjectConfig
from .timeline import apply_setup_js, play_scenes

logger = logging.getLogger(__name__)


async def record(config: ProjectConfig, segments: list[dict]) -> Path:
    """Record the screen half of the video. Returns the captured video path."""
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

        await page.goto(
            config.start_url,
            wait_until=timing.wait_until,
            timeout=timing.page_load_ms,
        )
        await asyncio.sleep(timing.startup_s)

        await apply_setup_js(page, config)
        await play_scenes(page, config, segments)

        await asyncio.sleep(timing.tail_s)
        await context.close()
        await browser.close()

    videos = list(video_dir.glob("*.webm"))
    if not videos:
        raise RuntimeError(f"recording produced no video file in {video_dir}")
    logger.info("screen recording done: %s", videos[0])
    return videos[0]
