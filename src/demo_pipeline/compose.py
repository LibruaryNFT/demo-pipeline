"""Compose stage — mux narration + screen capture, add intro/outro, boost volume.

Public entry point: compose_final(config, screen_video, segments) -> Path.
"""

import logging
import subprocess
from pathlib import Path

from .audio import get_duration
from .config import Branding, Encoding, ProjectConfig, TitleCard

logger = logging.getLogger(__name__)


def _escape_drawtext(text: str) -> str:
    """Escape a string for ffmpeg's drawtext filter.

    drawtext parses backslash, single quote, colon and percent specially. An
    unescaped colon in a tagline silently truncates the whole filter chain,
    which surfaces as a title card with missing text rather than an error.
    """
    for char, replacement in (
        ("\\", r"\\"),
        ("'", r"\'"),
        (":", r"\:"),
        ("%", r"\%"),
    ):
        text = text.replace(char, replacement)
    return text


def _encode_args(enc: Encoding) -> list[str]:
    """Shared ffmpeg output flags, so every stage encodes identically.

    The concat in step 4 is stream-level, so a mismatch between the title
    cards and the main body shows up as a glitch at the seam rather than an
    error.
    """
    return [
        "-c:v", enc.video_codec,
        "-preset", enc.preset,
        "-crf", str(enc.crf),
        "-c:a", enc.audio_codec,
        "-b:a", enc.audio_bitrate,
        "-pix_fmt", enc.pixel_format,
    ]


def faststart_args(enc: Encoding) -> list[str]:
    """Streaming flags for the final mux, empty when disabled.

    Only the delivered file needs this. Applying it to intermediates would
    pay for a rewrite of files nobody ever streams.
    """
    return ["-movflags", "+faststart"] if enc.faststart else []


def fade_filter(clip_s: float, fade_in_frames: int, fade_out_s: float) -> str:
    """Build an ffmpeg fade chain that is safe for very short clips.

    A naive `st = clip - fade_out` goes negative once the clip is shorter
    than the fade, and ffmpeg rejects a negative start time outright. That
    only shows up on short demos, so it survives every long test. Clamp the
    fade to the clip and never start before zero.
    """
    out_dur = max(min(fade_out_s, clip_s / 2), 0.0)
    start = max(clip_s - out_dur, 0.0)
    parts = [f"fade=in:0:{fade_in_frames}"]
    if out_dur > 0:
        parts.append(f"fade=out:st={start:.2f}:d={out_dur:.2f}")
    return ",".join(parts)


def _card_lines(specs: list[tuple[str, str, int, int]]) -> list[dict]:
    """Build drawtext line dicts, dropping any whose text is blank."""
    return [
        {"text": text, "color": color, "size": size, "y_offset": y}
        for text, color, size, y in specs
        if text
    ]


def default_intro(name: str, branding: Branding) -> TitleCard:
    """Name over tagline, both optional."""
    return TitleCard(
        duration=4.0,
        lines=_card_lines([
            (name, branding.accent, 96, -40 if branding.tagline else 0),
            (branding.tagline, "0xaaaaaa", 36, 60),
        ]),
    )


def default_outro(
    name: str, branding: Branding, line_height_ratio: float = 1.6
) -> TitleCard:
    """Name, attribution, link and occasion — each rendered only if set.

    Line spacing scales with font size, so a four-line card and a one-line
    card are both centred and neither collides.
    """
    rows = [
        (name, branding.accent, 72),
        (f"Built by {branding.author}" if branding.author else "", "0xffffff", 32),
        (branding.link, "0xaaaaaa", 28),
        (branding.context, "0x666666", 24),
    ]
    present = [(text, color, size) for text, color, size in rows if text]

    heights = [size * line_height_ratio for _, _, size in present]

    # Walk down from the top of the stack, placing each line at its own
    # centre. Kept in floats so a single-line card lands exactly on zero.
    specs = []
    cursor = -sum(heights) / 2
    for (text, color, size), height in zip(present, heights, strict=True):
        specs.append((text, color, size, round(cursor + height / 2)))
        cursor += height
    return TitleCard(duration=5.0, lines=_card_lines(specs))


def _build_title_card(config: ProjectConfig, card: TitleCard, out: Path) -> None:
    w, h = config.resolution

    filters = []
    for line in card.lines:
        y_expr = f"(h-text_h)/2+{line.get('y_offset', 0)}"
        filters.append(
            f"drawtext=text='{_escape_drawtext(line['text'])}'"
            f":fontcolor={line.get('color', '0xffffff')}"
            f":fontsize={line.get('size', 36)}"
            f":fontfile='{config.font}'"
            f":x=(w-text_w)/2:y={y_expr}"
        )
    enc = config.encoding
    filters.append(
        fade_filter(card.duration, enc.fade_in_frames, enc.card_fade_out_s)
    )
    vf = ",".join(filters)

    subprocess.run(
        [
            config.ffmpeg, "-y", "-loglevel", "error",
            "-f", "lavfi",
            "-i", f"color=c={card.bg_color}:s={w}x{h}:d={card.duration}",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
            "-vf", vf,
            *_encode_args(enc),
            "-shortest", "-t", str(card.duration),
            str(out),
        ],
        check=True,
        capture_output=True,
    )


def compose_final(
    config: ProjectConfig, screen_video: Path, segments: list[dict]
) -> Path:
    """Combine narration + screen capture + intro/outro into the final mp4."""
    work = config.work_dir
    enc = config.encoding
    work.mkdir(parents=True, exist_ok=True)

    # 1. Concat narration MP3 segments
    concat_file = work / "concat.txt"
    with open(concat_file, "w") as f:
        for seg in segments:
            # ffmpeg's concat demuxer wants forward slashes on every platform.
            path = str(seg["audio_path"]).replace("\\", "/")
            f.write(f"file '{path}'\n")
    combined_audio = work / "combined.mp3"
    subprocess.run(
        [
            config.ffmpeg, "-y", "-loglevel", "error",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_file),
            "-c", "copy",
            str(combined_audio),
        ],
        check=True,
        capture_output=True,
    )
    audio_dur = get_duration(config.ffprobe, combined_audio)
    logger.info("combined audio: %.1fs", audio_dur)

    # 2. Mux narration onto the screen capture, with fades
    raw_merged = work / "merged.mp4"
    w, h = config.resolution
    subprocess.run(
        [
            config.ffmpeg, "-y", "-loglevel", "error",
            "-i", str(screen_video),
            "-i", str(combined_audio),
            *_encode_args(enc),
            "-t", str(audio_dur),
            "-vf",
            f"scale={w}:{h},"
            + fade_filter(audio_dur, enc.fade_in_frames, enc.fade_out_s),
            "-shortest",
            str(raw_merged),
        ],
        check=True,
        capture_output=True,
    )

    # 3. Build intro + outro cards
    intro_card = config.intro or default_intro(config.name, config.branding)
    outro_card = config.outro or default_outro(
        config.name, config.branding, enc.line_height_ratio
    )
    intro_mp4 = work / "intro.mp4"
    outro_mp4 = work / "outro.mp4"
    _build_title_card(config, intro_card, intro_mp4)
    _build_title_card(config, outro_card, outro_mp4)

    # 4. Concat intro + main + outro
    pre_boost = work / "pre_boost.mp4"
    subprocess.run(
        [
            config.ffmpeg, "-y", "-loglevel", "error",
            "-i", str(intro_mp4),
            "-i", str(raw_merged),
            "-i", str(outro_mp4),
            "-filter_complex",
            "[0:v][0:a][1:v][1:a][2:v][2:a]concat=n=3:v=1:a=1[outv][outa]",
            "-map", "[outv]", "-map", "[outa]",
            *_encode_args(enc),
            str(pre_boost),
        ],
        check=True,
        capture_output=True,
    )

    # 5. Boost volume
    final = config.output_path
    final.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            config.ffmpeg, "-y", "-loglevel", "error",
            "-i", str(pre_boost),
            "-c:v", "copy",
            "-af", f"volume={enc.volume_boost_db}dB",
            "-c:a", enc.audio_codec,
            *faststart_args(enc),
            str(final),
        ],
        check=True,
        capture_output=True,
    )

    # 6. Cleanup intermediates
    for f in (
        concat_file, combined_audio, raw_merged, intro_mp4, outro_mp4, pre_boost
    ):
        f.unlink(missing_ok=True)

    final_dur = get_duration(config.ffprobe, final)
    size_mb = final.stat().st_size / 1024 / 1024
    logger.info("final video: %s (%.1f MB, %.0fs)", final, size_mb, final_dur)
    return final
