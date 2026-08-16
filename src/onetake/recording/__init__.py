"""Recording stage — dispatches to the configured backend.

Both backends expose the same coroutine:

    record(config, segments) -> Capture

`segments` is the output of audio.generate_audio_segments(). The recording
matches the cumulative duration of those segments so narration and picture
stay in sync.

The `Capture` carries the video path plus where each scene actually landed,
which is what lets compose remap an overrun rather than trim it.
"""

from ..config import BACKEND_XVFB, ProjectConfig
from ..timelapse import Capture

__all__ = ["record_screen"]


async def record_screen(config: ProjectConfig, segments: list[dict]) -> Capture:
    """Record the screen half of the video using the configured backend."""
    if config.backend == BACKEND_XVFB:
        from . import xvfb_backend

        return await xvfb_backend.record(config, segments)

    from . import playwright_backend

    return await playwright_backend.record(config, segments)
