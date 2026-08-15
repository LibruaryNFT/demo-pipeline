"""Configuration dataclasses for the demo pipeline.

A project supplies a ProjectConfig with a list of Scenes and optional
Branding / TitleCards. The engine consumes the config and produces an MP4.
"""

import platform
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

# Action handler signature: receives the Playwright Page, params dict, and
# scene duration; returns the time spent on the action (so the engine can
# sleep the remainder and keep audio + video locked together).
ActionHandler = Callable[..., Awaitable[float]]

# Recording backends. See recording/ for the implementations.
BACKEND_PLAYWRIGHT = "playwright"
BACKEND_XVFB = "xvfb"

_FONT_CANDIDATES = {
    "Linux": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    ],
    "Darwin": [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
    ],
    "Windows": [
        "C\\:/Windows/Fonts/arialbd.ttf",
        "C\\:/Windows/Fonts/arial.ttf",
    ],
}


def default_font() -> str:
    """Best-guess bold sans font for ffmpeg drawtext on this platform.

    Windows paths keep the escaped drive colon that ffmpeg's filter parser
    requires, so they are returned without an existence check.
    """
    system = platform.system()
    candidates = _FONT_CANDIDATES.get(system, _FONT_CANDIDATES["Linux"])
    if system == "Windows":
        return candidates[0]
    for path in candidates:
        if Path(path).exists():
            return path
    return candidates[0]


@dataclass
class Scene:
    """One narrated segment of the video."""

    id: str
    narration: str
    action: str  # resolved against built-in handlers, then action_handlers
    action_params: dict = field(default_factory=dict)


@dataclass
class TitleCard:
    """An intro or outro frame built by ffmpeg drawtext."""

    duration: float = 4.0
    bg_color: str = "0x0a0a0b"
    # Each line: {"text": str, "color": str, "size": int, "y_offset": int}
    lines: list[dict] = field(default_factory=list)


@dataclass
class Branding:
    """Optional metadata used to auto-generate intro and outro cards.

    Every field is optional. Whatever is set gets rendered; whatever is left
    blank is skipped, so a bare `Branding()` still yields a clean name-only
    card. `context` is a free-text line for whatever occasion the demo is for
    (a conference, a launch, a submission deadline, a client name).
    """

    tagline: str = ""
    author: str = ""
    link: str = ""
    context: str = ""
    accent: str = "0x50c878"


@dataclass
class Encoding:
    """ffmpeg encode settings and fade timings.

    Defaults target a quick demo: fast enough to iterate on, good enough to
    put in front of people. Raise `crf` for smaller files, lower it for
    sharper text.
    """

    video_codec: str = "libx264"
    preset: str = "medium"
    crf: int = 23
    pixel_format: str = "yuv420p"
    audio_codec: str = "aac"
    audio_bitrate: str = "192k"

    # The capture preset is separate: it runs live during recording, so it
    # trades file size for not dropping frames.
    capture_preset: str = "ultrafast"

    # OpenAI TTS output sits around -25dB, inaudible next to normal desktop
    # playback. Applied once at the end so title cards get it too.
    volume_boost_db: int = 15

    fade_in_frames: int = 30
    fade_out_s: float = 2.0
    card_fade_out_s: float = 1.0

    # Vertical space each title-card line occupies, as a multiple of its own
    # font size. A fixed pixel step collides when a large name sits above a
    # small byline.
    line_height_ratio: float = 1.6


@dataclass
class Timing:
    """Waits and timeouts, in seconds unless the name says milliseconds.

    Defaults suit a typical SPA on a decent connection. Bump `page_load_s`
    and `settle_s` for a heavy app, or drop them to tighten a quick demo.
    """

    # How to decide a navigation is done. "domcontentloaded" is fast and
    # works for most apps; "networkidle" waits for XHR to quiesce, which
    # suits data-heavy dashboards but hangs on pages that poll.
    wait_until: str = "domcontentloaded"

    page_load_ms: int = 30000
    navigate_ms: int = 15000
    selector_ms: int = 5000

    # Pause after the initial load, before the first scene runs.
    startup_s: float = 3.0
    # Pause after a navigation or click, to let the UI settle.
    settle_s: float = 2.5
    # Pause after a scroll completes.
    scroll_s: float = 1.2
    # Tail recorded after the last scene, so the video does not cut dead.
    tail_s: float = 2.0

    # xvfb backend only.
    cdp_timeout_s: float = 25.0
    xvfb_startup_s: float = 1.5
    chrome_kill_wait_s: float = 2.0
    ffmpeg_flush_s: float = 15.0


@dataclass
class ProjectConfig:
    """Full configuration for a single demo render."""

    # Identity
    name: str
    output_path: Path

    # What to record
    start_url: str
    scenes: list[Scene]

    # Recording backend. "playwright" works everywhere and needs no setup.
    # "xvfb" is Linux-only but records a real browser profile, which is what
    # you want when the demo must show a real extension or signed-in session.
    backend: str = BACKEND_PLAYWRIGHT

    # Presentation
    branding: Branding = field(default_factory=Branding)
    intro: TitleCard | None = None
    outro: TitleCard | None = None

    # Behaviour
    action_handlers: dict[str, ActionHandler] = field(default_factory=dict)
    # Arbitrary JS evaluated once after the page loads. Typical use is stubbing
    # a browser extension or API so the demo shows a populated state without
    # real credentials.
    setup_js: str | None = None

    # xvfb backend only — a persistent Chrome profile directory so an
    # extension or logged-in session survives across runs.
    chrome_profile: Path | None = None

    # Working directories — created if missing, derived from output_path
    audio_dir: Path | None = None
    work_dir: Path | None = None

    # Rendering knobs
    resolution: tuple[int, int] = (1920, 1080)
    framerate: int = 30
    encoding: Encoding = field(default_factory=Encoding)
    timing: Timing = field(default_factory=Timing)

    # xvfb backend only. Both must be unique per concurrent render on the
    # same machine, or two runs will fight over the display and the port.
    display_num: str = ":99"
    cdp_port: int = 9222

    # TTS settings (OpenAI)
    tts_voice: str = "onyx"
    tts_speed: float = 0.92
    tts_model: str = "tts-1-hd"

    # A cached narration MP3 smaller than this is treated as a failed write
    # and regenerated rather than reused.
    min_audio_bytes: int = 1000
    # A capture smaller than this means the recorder produced nothing usable.
    min_video_bytes: int = 10_000

    # Tool paths
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    font: str = field(default_factory=default_font)
    # Checked in order; the first one on PATH is used. xvfb backend only.
    chrome_binaries: tuple[str, ...] = (
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
    )

    def __post_init__(self) -> None:
        self.output_path = Path(self.output_path)
        if self.chrome_profile is not None:
            self.chrome_profile = Path(self.chrome_profile)

        out_parent = self.output_path.parent
        if self.audio_dir is None:
            self.audio_dir = out_parent / f"{self.output_path.stem}_audio"
        if self.work_dir is None:
            self.work_dir = out_parent / f"{self.output_path.stem}_work"
        self.audio_dir = Path(self.audio_dir)
        self.work_dir = Path(self.work_dir)

        if self.backend not in (BACKEND_PLAYWRIGHT, BACKEND_XVFB):
            raise ValueError(
                f"unknown backend {self.backend!r} — "
                f"expected {BACKEND_PLAYWRIGHT!r} or {BACKEND_XVFB!r}"
            )
        if self.backend == BACKEND_XVFB and self.chrome_profile is None:
            raise ValueError(
                "backend='xvfb' requires chrome_profile — point it at a "
                "dedicated Chrome user-data-dir (not your default profile)"
            )
