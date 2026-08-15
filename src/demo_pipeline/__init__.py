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
import io
import logging
import sys
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


def _fix_windows_console() -> None:
    """Make stdout/stderr UTF-8 on Windows.

    Narration is prose and routinely contains curly quotes and dashes. The
    default Windows console codec cannot encode them, so logging a scene
    would raise UnicodeEncodeError mid-render.
    """
    if sys.platform != "win32":
        return
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        if isinstance(stream, io.TextIOWrapper) and stream.encoding == "utf-8":
            continue
        if hasattr(stream, "buffer"):
            setattr(
                sys, name,
                io.TextIOWrapper(stream.buffer, encoding="utf-8", errors="replace"),
            )


def _load_env() -> None:
    """Load a .env from the working directory, if python-dotenv is present.

    Resolved explicitly from the current working directory rather than via
    `load_dotenv()`'s default. That default walks up from the *calling*
    module's file, which once this package is installed is somewhere in
    site-packages, so it silently searches the wrong tree and finds nothing.

    Existing environment variables win over the file, so an exported key
    always beats a stale .env.
    """
    try:
        from dotenv import find_dotenv, load_dotenv
    except ImportError:
        return
    path = find_dotenv(usecwd=True)
    if path:
        load_dotenv(path)
        logger.debug("loaded environment from %s", path)


async def render_async(config: ProjectConfig, load_env: bool = True) -> Path:
    """Run the full pipeline: narration -> screen recording -> compose.

    `load_env` reads a .env from the working directory before starting, so
    OPENAI_API_KEY can live in a file rather than the shell. Pass False when
    the caller manages its own configuration and does not want the process
    environment touched.
    """
    _fix_windows_console()
    if load_env:
        _load_env()

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


def render(config: ProjectConfig, load_env: bool = True) -> Path:
    """Synchronous wrapper around render_async."""
    return asyncio.run(render_async(config, load_env=load_env))
