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
from pathlib import Path

from ..actions import resolve_handlers, run_scene_action
from ..config import ProjectConfig

logger = logging.getLogger(__name__)


async def record(config: ProjectConfig, segments: list[dict]) -> Path:
    """Record the screen half of the video. Returns the captured video path."""
    from playwright.async_api import async_playwright

    w, h = config.resolution
    video_dir = config.work_dir / "screen_recording"
    video_dir.mkdir(parents=True, exist_ok=True)

    # Clear stale captures so we can identify this run's output unambiguously.
    for stale in video_dir.glob("*.webm"):
        stale.unlink()

    total_duration = sum(s["duration"] for s in segments)
    logger.info("recording target duration: %.1fs", total_duration)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": w, "height": h},
            record_video_dir=str(video_dir),
            record_video_size={"width": w, "height": h},
        )
        page = await context.new_page()

        await page.goto(config.start_url, wait_until="networkidle", timeout=30000)
        await asyncio.sleep(2.0)

        if config.setup_js:
            await page.evaluate(config.setup_js)
            await asyncio.sleep(0.5)

        handlers = resolve_handlers(config)

        # Absolute timeline: each scene ends at a fixed offset from t0, so a
        # slow action steals from its own scene rather than shifting every
        # later scene out of sync with the narration.
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

            # A navigation tears down the injected state, so reapply it.
            if config.setup_js and scene.action == "navigate":
                try:
                    await page.evaluate(config.setup_js)
                except Exception as e:
                    logger.warning("setup_js re-injection failed: %s", e)

            wait_for = (t0 + scene_end) - time.monotonic()
            if wait_for > 0:
                await asyncio.sleep(wait_for)
            elapsed = scene_end

        await asyncio.sleep(2.0)
        await context.close()
        await browser.close()

    videos = list(video_dir.glob("*.webm"))
    if not videos:
        raise RuntimeError(
            f"recording produced no video file in {video_dir}"
        )
    logger.info("screen recording done: %s", videos[0])
    return videos[0]
