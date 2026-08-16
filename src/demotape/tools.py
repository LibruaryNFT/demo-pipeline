"""Locating ffmpeg and ffprobe, with a bundled fallback.

ffmpeg is the largest thing standing between someone and their first render.
It is a system package, it is absent from most minimal CI images, and on
Windows it is a manual download. A tool that cannot run until you have solved
that is a tool most people do not evaluate.

Resolution order, for both binaries:

1. whatever the config says, if it is not the default — an explicit choice
   always wins
2. the binary on PATH — a real system build is preferred, since it is likely
   newer, better optimised and more completely configured
3. a bundled build from `imageio-ffmpeg`, if that optional extra is installed

The bundled package ships **ffmpeg only, no ffprobe**, which matters because
the compose stage measures every scene's duration and the whole timeline is
built from those numbers. So there is a fallback for that too: ffmpeg prints
`Duration:` to stderr when asked to open a file with no output, and that line
is parseable. It is used only when a real ffprobe is unavailable, and it
raises rather than defaulting on failure — a wrong duration silently
desynchronises narration from picture for the whole video, which is far
worse than an error.

One honest caveat: the banner is printed to centiseconds, so this is less
precise than ffprobe. Measured against real files, 40.867007s reads back as
40.87 and 190.611995s as 190.61 — under 5ms out. Scene ends are absolute
offsets accumulated from these numbers, so a long demo can drift by a few
tens of milliseconds overall. That is inaudible for narration and nothing
here is frame-accurate to begin with, but it is a real difference and the
reason a system ffprobe is still preferred when one exists.
"""

import logging
import re
import subprocess
from shutil import which

logger = logging.getLogger(__name__)

DEFAULT_FFMPEG = "ffmpeg"
DEFAULT_FFPROBE = "ffprobe"

_DURATION = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")


def _bundled_ffmpeg() -> str | None:
    """Path to the imageio-ffmpeg binary, or None if it is not installed."""
    try:
        import imageio_ffmpeg
    except ImportError:
        return None
    try:
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e:  # missing binary for this platform, etc.
        logger.debug("imageio-ffmpeg present but unusable: %s", e)
        return None


def resolve_ffmpeg(configured: str = DEFAULT_FFMPEG) -> str:
    """Return the ffmpeg to use, preferring an explicit choice then PATH."""
    if configured != DEFAULT_FFMPEG:
        return configured
    if which(DEFAULT_FFMPEG):
        return DEFAULT_FFMPEG
    bundled = _bundled_ffmpeg()
    if bundled:
        logger.info("ffmpeg not on PATH — using the bundled build at %s", bundled)
        return bundled
    return DEFAULT_FFMPEG  # let the first call fail with a real error


def resolve_ffprobe(configured: str = DEFAULT_FFPROBE) -> str | None:
    """Return the ffprobe to use, or None if there is not one.

    None is a supported state, not a failure: callers fall back to parsing
    ffmpeg's own output.
    """
    if configured != DEFAULT_FFPROBE:
        return configured
    if which(DEFAULT_FFPROBE):
        return DEFAULT_FFPROBE
    return None


def parse_duration(stderr: str) -> float | None:
    """Pull a duration in seconds out of ffmpeg's banner, or None."""
    match = _DURATION.search(stderr)
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def duration_via_ffmpeg(ffmpeg: str, path) -> float:
    """Duration of a media file, read from ffmpeg's banner.

    ffmpeg exits non-zero here because no output file was given; the banner
    on stderr is the point, so the return code is ignored.
    """
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
    )
    seconds = parse_duration(result.stderr)
    if seconds is None:
        raise RuntimeError(
            f"could not read a duration for {path}. Install ffprobe, or pass "
            f"ffprobe= on the config. ffmpeg said:\n"
            + "\n".join(result.stderr.strip().splitlines()[-5:])
        )
    return seconds
