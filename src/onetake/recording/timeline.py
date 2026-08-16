"""Scene playback, shared by both recording backends.

The two backends differ only in how they capture frames. Sequencing scenes
against the narration is identical, so it lives here and neither backend
drifts from the other.
"""

import asyncio
import logging
import time

from ..actions import resolve_handlers, run_scene_action
from ..timelapse import SceneTiming
from . import overlay as overlay_mod

logger = logging.getLogger(__name__)


class Timeline:
    """What play_scenes measured: when it started, and where each scene fell.

    `t0` is a `time.monotonic()` reading, so it is only meaningful against
    another one — the backends subtract their own recording start from it to
    work out how much footage precedes the first scene.
    """

    def __init__(self, t0: float, scenes: list[SceneTiming]):
        self.t0 = t0
        self.scenes = tuple(scenes)


async def apply_setup_js(page, config) -> None:
    """Evaluate the project's setup_js, if any, tolerating failure."""
    if not config.setup_js:
        return
    try:
        await page.evaluate(config.setup_js)
    except Exception as e:
        logger.warning("setup_js injection failed: %s", e)


async def play_scenes(page, config, segments: list[dict]) -> Timeline:
    """Run every scene against an absolute timeline.

    Each scene ends at a fixed offset from t0, so a slow action steals from
    its own scene rather than pushing everything after it out of sync with
    the narration.

    Returns where every scene actually landed. Nothing here changes based on
    it — the sequencing is the same whether or not anything reads the
    result — but it is what lets compose remap an overrun instead of
    trimming it off the end.

    `setup_js` is re-applied whenever a scene changes the page URL. A page
    load tears down injected state, and the navigation may come from a
    built-in action or from a project handler doing its own `page.goto` —
    comparing the URL catches both without the handler needing to know.
    """
    handlers = resolve_handlers(config)
    timing = config.timing
    ov = config.overlay

    t0 = time.monotonic()
    elapsed = 0.0
    cursor = 0.0
    zoomed = False
    timings: list[SceneTiming] = []
    for i, seg in enumerate(segments):
        scene = seg["scene"]
        dur = seg["duration"]
        scene_end = elapsed + dur
        logger.info(
            "[%d/%d] %s -> %s (%.1fs)",
            i + 1, len(segments), scene.id, scene.action, dur,
        )

        url_before = _safe_url(page)
        await run_scene_action(handlers, page, scene, dur, timing)
        if config.setup_js and _safe_url(page) != url_before:
            await apply_setup_js(page, config)

        # Overlay work happens after the action, so a zoom target that the
        # action navigated to or scrolled into view actually exists by the
        # time it is measured.
        if ov.enabled:
            zoomed = await _apply_zoom(page, scene, ov, zoomed)
            if scene.caption:
                hold = dur if ov.caption_per_scene else None
                await overlay_mod.caption(page, scene.caption, hold)

        wait_for = (t0 + scene_end) - time.monotonic()
        if wait_for > 0:
            await asyncio.sleep(wait_for)
        else:
            logger.warning(
                "scene %s overran its narration by %.1fs", scene.id, -wait_for
            )

        # Taken after the wait, and carried forward as the next scene's
        # start, so the spans are exactly contiguous. A gap between them
        # would be footage the remap has no slot for.
        now = time.monotonic() - t0
        timings.append(
            SceneTiming(
                id=scene.id,
                target_start=elapsed,
                target_end=scene_end,
                actual_start=cursor,
                actual_end=now,
            )
        )
        cursor = now
        elapsed = scene_end

    if ov.enabled and zoomed:
        await overlay_mod.zoom_reset(page, ov.zoom_reset_ms)

    return Timeline(t0, timings)


async def _apply_zoom(page, scene, ov, zoomed: bool) -> bool:
    """Zoom to this scene's target, or release a zoom the last one left.

    Returns whether a zoom is active afterwards. Releasing only when the next
    scene does not zoom keeps the camera in close and pans between adjacent
    targets, instead of bouncing all the way out and back in.
    """
    if scene.zoom:
        ok = await overlay_mod.zoom(page, scene.zoom, scene.zoom_scale, ov.zoom_ms)
        if not ok:
            logger.warning("scene %s: zoom target not found: %s", scene.id, scene.zoom)
        return ok
    if zoomed:
        await overlay_mod.zoom_reset(page, ov.zoom_reset_ms)
    return False


def _safe_url(page) -> str | None:
    """page.url can raise if the page is mid-navigation or closed."""
    try:
        return page.url
    except Exception:
        return None
