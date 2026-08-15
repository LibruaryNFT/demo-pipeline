"""demo-pipeline — narrated screen-recording demo videos for any web app.

Three stages: TTS narration, timed screen recording, ffmpeg composition.
You supply scenes and narration; the engine produces an MP4.

    from demo_pipeline import ProjectConfig, Scene, Branding, render

    render(ProjectConfig(
        name="My App",
        output_path="out/demo.mp4",
        start_url="https://example.com",
        branding=Branding(tagline="Does the thing", link="example.com"),
        scenes=[Scene(id="hook", narration="...", action="wait")],
    ))
"""

import asyncio
import logging
from pathlib import Path

from .actions import DEFAULT_ACTION_HANDLERS
from .audio import generate_audio_segments, get_duration
from .compose import compose_final, default_intro, default_outro
from .config import (
    BACKEND_PLAYWRIGHT,
    BACKEND_XVFB,
    Branding,
    Encoding,
    ProjectConfig,
    Scene,
    Timing,
    TitleCard,
)
from .recording import record_screen

logger = logging.getLogger(__name__)

__version__ = "0.1.0"

__all__ = [
    "BACKEND_PLAYWRIGHT",
    "BACKEND_XVFB",
    "Branding",
    "Encoding",
    "ProjectConfig",
    "Scene",
    "Timing",
    "TitleCard",
    "DEFAULT_ACTION_HANDLERS",
    "generate_audio_segments",
    "get_duration",
    "record_screen",
    "compose_final",
    "default_intro",
    "default_outro",
    "render",
    "render_async",
]


async def render_async(config: ProjectConfig) -> Path:
    """Run the full pipeline: narration -> screen recording -> compose."""
    logger.info("=" * 60)
    logger.info("demo-pipeline — %s (backend: %s)", config.name, config.backend)
    logger.info("=" * 60)

    logger.info("STAGE 1: narration (TTS)")
    segments = generate_audio_segments(config)

    logger.info("STAGE 2: screen recording")
    screen_video = await record_screen(config, segments)

    logger.info("STAGE 3: compose")
    final = compose_final(config, screen_video, segments)

    logger.info("DONE: %s", final)
    return final


def render(config: ProjectConfig) -> Path:
    """Synchronous wrapper around render_async."""
    return asyncio.run(render_async(config))
