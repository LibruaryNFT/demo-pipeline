"""Worked example — a narrated demo of a public site.

Run:
    OPENAI_API_KEY=... python examples/example_demo.py

This is the template to copy for a real demo. Everything product-specific
lives in this file; the engine stays generic. Swap the URL, the narration and
the action handlers, and you have your own video.
"""

import asyncio
import logging
from pathlib import Path

from demotape import Branding, ProjectConfig, Scene, Timing, TitleCard, render

logging.basicConfig(level=logging.INFO, format="%(message)s")

OUT_DIR = Path(__file__).parent / "output"
APP_URL = "https://example.com"


# ── Custom action handlers ─────────────────────────────────────────────────
#
# Built-in verbs (wait, scroll, navigate, click, hover, evaluate) cover most
# scenes.
# Define your own when a scene needs several steps. The signature is always
# (page, params, duration) -> seconds spent; the engine sleeps the remainder.


async def highlight_heading(page, params, duration):
    """Scroll the main heading into view and outline it briefly."""
    await page.evaluate(
        """
        const el = document.querySelector('h1');
        if (el) {
            el.scrollIntoView({behavior: 'smooth', block: 'center'});
            el.style.outline = '3px solid #50c878';
            el.style.transition = 'outline 0.4s ease';
        }
        """
    )
    await asyncio.sleep(1.5)
    return 1.5


# ── Scenes ─────────────────────────────────────────────────────────────────
#
# One scene per narration beat. Scene length is driven by how long the
# narration takes to speak, so write for the ear and let the timing follow.
# Scene ids are the audio cache key: change a narration string and delete the
# matching MP3 in the *_audio directory to force a regenerate.

SCENES = [
    Scene(
        id="hook",
        narration=(
            "Every product demo starts the same way. Someone opens a screen "
            "recorder, fumbles the first take, and records it again."
        ),
        action="wait",
    ),
    Scene(
        id="intro",
        narration=(
            "This pipeline does it differently. You write the narration and "
            "describe the scenes. It handles the voice, the browser, and the "
            "edit."
        ),
        action="highlight_heading",
    ),
    Scene(
        id="scroll",
        narration=(
            "Scenes drive the browser directly. Scroll, click, hover, "
            "navigate, or write your own action for anything more involved."
        ),
        action="scroll",
        action_params={"y": 400},
    ),
    Scene(
        id="close",
        narration=(
            "The result is a narrated video that renders the same way every "
            "time. Change a line, re-run it, and ship the new cut."
        ),
        action="wait",
    ),
]


CONFIG = ProjectConfig(
    name="demotape",
    output_path=OUT_DIR / "example_demo.mp4",
    start_url=APP_URL,
    # "playwright" needs no system setup and runs anywhere. Switch to "xvfb"
    # (Linux, plus a chrome_profile) when the demo must show a real extension
    # or a signed-in session.
    backend="playwright",
    branding=Branding(
        tagline="Narrated demo videos, rendered from a config file",
        author="Your Name",
        link="example.com",
        context="",  # e.g. a conference, launch or submission this cut is for
    ),
    scenes=SCENES,
    action_handlers={"highlight_heading": highlight_heading},
    # Defaults suit most apps. Override when yours is slower, or renders
    # after XHR rather than on DOMContentLoaded.
    timing=Timing(settle_s=2.0),
    # Custom cards override the branding-generated defaults entirely.
    intro=TitleCard(
        duration=3.5,
        lines=[
            {"text": "demotape", "color": "0x50c878", "size": 96, "y_offset": -40},
            {"text": "One config, one video", "color": "0xaaaaaa", "size": 36, "y_offset": 60},
        ],
    ),
)


if __name__ == "__main__":
    render(CONFIG)
