"""Derived outputs: a GIF for READMEs, and alternate aspect ratios.

Both work on the finished MP4, so neither touches the recorder and neither
can break a render. Re-shaping is a filter graph over a file that already
exists, not a second capture.

**Why a GIF at all.** GitHub autoplays GIFs inline in a README and does not
autoplay MP4s. That is the entire reason, and it is enough of one: a demo
tool whose output cannot be seen without a click is losing most of its
audience at the first screen.

**Why the shapes are derived rather than re-recorded.** Rendering the app at
a narrow viewport would change its responsive layout, so the vertical cut
would show a different product from the landscape one. Letterboxing the
landscape capture over a blurred copy of itself keeps the demo identical
and needs no cooperation from the app under test.

Note that `vertical` is much slower than the other two: the blurred backdrop
is a full-frame box blur at 1080x1920, applied per frame. On a several-minute
demo expect minutes, not seconds. Nothing is wrong when it looks stuck.
"""

import logging
import subprocess
from pathlib import Path

from .config import ProjectConfig

logger = logging.getLogger(__name__)

#: Video filter graph per shape. `[v]` is the final labelled video pad.
#: `setsar=1` on each because a non-square sample aspect ratio survives the
#: scale and shows up as a subtly stretched picture on some players.
SHAPES = {
    "landscape": (
        "[0:v]scale=1280:720:force_original_aspect_ratio=decrease,"
        "pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1[v]"
    ),
    "square": "[0:v]crop=ih:ih:(iw-ih)/2:0,scale=1080:1080,setsar=1[v]",
    "vertical": (
        "[0:v]split=2[bg][fg];"
        "[bg]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,boxblur=24:4[bgb];"
        "[fg]scale=1080:-2[fgs];"
        "[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1[v]"
    ),
}


def _run(ffmpeg: str, args: list[str]) -> None:
    result = subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", *args],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        tail = "\n".join(result.stderr.strip().splitlines()[-8:])
        raise RuntimeError(f"ffmpeg exited {result.returncode}:\n{tail}")


def export_gif(
    config: ProjectConfig,
    source: Path | None = None,
    out: Path | None = None,
    width: int = 960,
    fps: int = 12,
    start: float | None = None,
    duration: float | None = None,
) -> Path:
    """Convert the video to a README-ready GIF.

    Two-pass palette generation, with ordered Bayer dithering rather than
    the default error diffusion. On flat UI colours error diffusion produces
    a visible shimmer across areas that should be perfectly still — the
    dither pattern changes frame to frame even where the pixels do not.

    `start` and `duration` cut a span out of the video. A whole narrated
    demo makes an enormous and unwatchable GIF; ten seconds of the part that
    reads well makes a good one.
    """
    source = Path(source or config.output_path)
    if not source.exists():
        raise FileNotFoundError(f"no video at {source} — render it first")
    out = Path(out or source.with_suffix(".gif"))
    out.parent.mkdir(parents=True, exist_ok=True)

    work = config.work_dir
    work.mkdir(parents=True, exist_ok=True)
    palette = work / "palette.png"

    span: list[str] = []
    if start is not None:
        span += ["-ss", str(start)]
    if duration is not None:
        span += ["-t", str(duration)]

    frames = f"fps={fps},scale={width}:-1:flags=lanczos"
    # stats_mode=diff weights the palette toward what actually changes, which
    # keeps a mostly-static UI from spending its 256 colours on the backdrop.
    _run(config.ffmpeg, [*span, "-i", str(source),
                         "-vf", f"{frames},palettegen=stats_mode=diff", str(palette)])
    _run(config.ffmpeg, [*span, "-i", str(source), "-i", str(palette),
                         "-lavfi",
                         f"{frames}[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=5",
                         "-loop", "0", str(out)])
    palette.unlink(missing_ok=True)

    size_mb = out.stat().st_size / 1024 / 1024
    logger.info("gif: %s (%.1f MB, %dpx @ %dfps)", out, size_mb, width, fps)
    return out


def export_shape(
    config: ProjectConfig,
    shape: str,
    source: Path | None = None,
    out: Path | None = None,
) -> Path:
    """Re-frame the finished video as landscape, square or vertical."""
    if shape not in SHAPES:
        raise ValueError(
            f"unknown shape {shape!r} — expected one of {', '.join(sorted(SHAPES))}"
        )
    source = Path(source or config.output_path)
    if not source.exists():
        raise FileNotFoundError(f"no video at {source} — render it first")
    out = Path(out or source.with_name(f"{source.stem}.{shape}{source.suffix}"))
    out.parent.mkdir(parents=True, exist_ok=True)

    enc = config.encoding
    _run(config.ffmpeg, [
        "-i", str(source),
        "-filter_complex", SHAPES[shape],
        "-map", "[v]",
        # Audio is unchanged by re-framing, so copy it rather than paying to
        # re-encode and lose a generation.
        "-map", "0:a?", "-c:a", "copy",
        "-c:v", enc.video_codec,
        "-preset", enc.preset,
        "-crf", str(enc.crf),
        "-pix_fmt", enc.pixel_format,
        *(["-movflags", "+faststart"] if enc.faststart else []),
        str(out),
    ])
    logger.info("%s: %s", shape, out)
    return out
