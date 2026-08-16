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

# Only meaningful on the xvfb backend; see ProjectConfig.framerate.
DEFAULT_FRAMERATE = 30

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

    # On-screen text, drawn by the browser for this scene's duration. This is
    # deliberately not the narration: spoken lines read as full sentences and
    # captions read as short phrases, so forcing one to serve as the other
    # makes both worse. Leave blank for no caption.
    caption: str = ""

    # CSS selector to zoom toward while this scene plays, and how far. A
    # 1920x1080 capture of a dense screen is unreadable in a README embed or
    # on a phone, and zooming is what makes the thing the narration is
    # describing legible. The zoom releases when the next scene begins,
    # unless that scene zooms somewhere else.
    zoom: str = ""
    zoom_scale: float = 1.6


@dataclass
class TitleCard:
    """An intro or outro frame built by ffmpeg drawtext."""

    duration: float = 4.0
    bg_color: str = "0x0a0a0b"
    # Each line: {"text": str, "color": str, "size": int, "y_offset": int}
    lines: list[dict] = field(default_factory=list)


@dataclass
class Overlay:
    """Captions and zoom, drawn in the page and captured by the recorder.

    Off costs nothing: with `enabled=False` no script is injected at all, so
    a demo that uses neither caption nor zoom runs exactly as it did before
    these existed.

    Note this covers only what Playwright does not already do. From 1.62 its
    own `page.screencast.show_actions()` draws a cursor, a click pulse, an
    element highlight and an action label straight into the capture — use
    that rather than asking for it here.
    """

    enabled: bool = True

    accent: str = "#e91e63"
    # Blank means a system font stack, which is what you want: it is the
    # browser resolving a font it definitely has, not ffmpeg being handed a
    # path that may not exist on the machine running the render.
    font: str = ""

    # How long the zoom and un-zoom take. Long enough to read as a camera
    # move rather than a cut, short enough not to eat the scene.
    zoom_ms: int = 700
    zoom_reset_ms: int = 600

    # Hold a caption for its scene and no longer. Set False to leave it up
    # until the next caption replaces it.
    caption_per_scene: bool = True


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

    # Move the moov atom to the front of the finished mp4. Without it a
    # browser has to download the whole file before it can start playing,
    # which is the difference between a demo that plays on click and one that
    # sits on a spinner. Costs a second pass over the output and nothing else,
    # and only the final file gets it — intermediates are never streamed.
    faststart: bool = True


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
    overlay: Overlay = field(default_factory=Overlay)

    # Behaviour
    action_handlers: dict[str, ActionHandler] = field(default_factory=dict)
    # Arbitrary JS evaluated once after the page loads. Typical use is stubbing
    # a browser extension or API so the demo shows a populated state without
    # real credentials.
    setup_js: str | None = None

    # xvfb backend only — a persistent Chrome profile directory so an
    # extension or logged-in session survives across runs.
    chrome_profile: Path | None = None
    # Extra flags appended to the Chrome command line. The xvfb backend
    # records the whole browser window, so this is where you control what
    # that window looks like:
    #   "--kiosk"           fullscreen, no tab strip or address bar
    #   "--hide-scrollbars" drop the scrollbar from the capture
    # Leave empty to show the browser frame, which reads as more real when
    # the demo is about a browser extension.
    chrome_flags: tuple[str, ...] = ()
    # Whether the mouse pointer appears in the capture. Off by default: on a
    # virtual display the pointer never moves, so it just parks a stray arrow
    # in the middle of the frame.
    draw_mouse: bool = False

    # Working directories — created if missing, derived from output_path
    audio_dir: Path | None = None
    work_dir: Path | None = None

    # Rendering knobs
    resolution: tuple[int, int] = (1920, 1080)
    # xvfb backend only. Playwright's recorder exposes no framerate control,
    # so on that backend the capture rate is whatever Playwright chooses
    # (25fps in practice) and this value is ignored. The engine warns rather
    # than pretending it applied.
    framerate: int = DEFAULT_FRAMERATE
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
