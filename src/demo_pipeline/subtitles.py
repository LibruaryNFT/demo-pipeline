"""WebVTT caption track, written alongside the video.

A narrated video should ship captions. It is an accessibility expectation
for spoken content, it makes the video usable with the sound off, and
platforms will index the text.

This is unusually cheap here because both inputs already exist and are
already exact: each scene's narration is the cue text, and each scene's
measured audio length is the cue duration. Those are the same numbers the
recorder sequences against, so the captions cannot drift from the picture
without the picture drifting from itself.

The one thing that must not be got wrong is the offset. Cues are timed
against the *finished* video, which opens with a title card, so every cue
sits `intro.duration` later than its position in the narration. Accumulate
from the same place the compose stage does or the whole track is early by
the length of the card.
"""

from .config import ProjectConfig


def format_timestamp(seconds: float) -> str:
    """WebVTT wants `HH:MM:SS.mmm`, always zero-padded."""
    if seconds < 0:
        seconds = 0.0
    total_ms = round(seconds * 1000)
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def build_vtt(segments: list[dict], offset: float = 0.0) -> str:
    """Render segments as a WebVTT document, one cue per scene.

    `offset` is where the narration starts in the finished video — the
    duration of whatever precedes it, which is normally the intro card.

    Scenes with blank narration produce no cue. An empty cue is not neutral:
    players show it as a flicker of empty caption box.
    """
    lines = ["WEBVTT", ""]
    cursor = offset

    for segment in segments:
        scene = segment["scene"]
        duration = segment["duration"]
        start, end = cursor, cursor + duration
        cursor = end

        text = " ".join(scene.narration.split())
        if not text:
            continue

        lines.append(scene.id)
        lines.append(f"{format_timestamp(start)} --> {format_timestamp(end)}")
        lines.append(text)
        lines.append("")

    return "\n".join(lines)


def write_vtt(config: ProjectConfig, segments: list[dict], offset: float) -> "object":
    """Write `<output>.vtt` next to the video and return its path."""
    path = config.output_path.with_suffix(".vtt")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_vtt(segments, offset), encoding="utf-8")
    return path
