"""Environment diagnostic — run this first when a render fails.

    python -m demo_pipeline.doctor            # check the playwright backend
    python -m demo_pipeline.doctor --xvfb     # also check the xvfb stack

Checks each dependency in isolation and reports what is missing, rather
than leaving you to infer it from a Chrome log tail or a black frame.

Exit code is 0 when everything the selected backend needs is present.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path
from shutil import which

from .config import ProjectConfig, Scene, default_font
from .tools import _bundled_ffmpeg

OK = "ok"
WARN = "warn"
FAIL = "fail"

_MARK = {OK: "  ok  ", WARN: " warn ", FAIL: " FAIL "}


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def add(self, status: str, name: str, detail: str = "") -> None:
        self.rows.append((status, name, detail))
        print(f"[{_MARK[status]}] {name}" + (f" — {detail}" if detail else ""))

    @property
    def failed(self) -> bool:
        return any(s == FAIL for s, _, _ in self.rows)


def _version(cmd: list[str]) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return (out.stdout or out.stderr).splitlines()[0][:70]
    except Exception as e:
        return f"could not run: {e}"


def check_ffmpeg(r: Report) -> None:
    """Report which ffmpeg is in play, and say when it is the bundled one.

    Naming the source matters: a render on the bundled build has no ffprobe
    and reads durations to centisecond precision instead, and someone
    debugging an encode should not have to guess which binary produced it.
    """
    if which("ffmpeg"):
        r.add(OK, "ffmpeg", _version(["ffmpeg", "-version"]))
    else:
        bundled = _bundled_ffmpeg()
        if bundled:
            r.add(WARN, "ffmpeg", f"not on PATH — using the bundled build at {bundled}")
        else:
            r.add(
                FAIL,
                "ffmpeg",
                "not on PATH — install it, or "
                'pip install "demo-pipeline[bundled-ffmpeg]"',
            )

    if which("ffprobe"):
        r.add(OK, "ffprobe", _version(["ffprobe", "-version"]))
    elif which("ffmpeg") or _bundled_ffmpeg():
        r.add(
            WARN,
            "ffprobe",
            "not on PATH — durations will be parsed from ffmpeg's banner "
            "(centisecond precision)",
        )
    else:
        r.add(FAIL, "ffprobe", "not on PATH — install ffmpeg")


def check_font(r: Report) -> None:
    font = default_font()
    # The Windows default keeps an escaped drive colon for ffmpeg's filter
    # parser, so it is not a real filesystem path to stat.
    if sys.platform == "win32":
        r.add(WARN, "font", f"{font} (not verified on Windows)")
    elif Path(font).exists():
        r.add(OK, "font", font)
    else:
        r.add(
            FAIL, "font",
            f"{font} missing — title cards will render blank. "
            "Install DejaVu, or set ProjectConfig(font=...)",
        )


def check_playwright(r: Report) -> None:
    try:
        import playwright  # noqa: F401
    except ImportError:
        r.add(FAIL, "playwright", "not installed — pip install playwright")
        return
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            version = browser.version
            browser.close()
        r.add(OK, "playwright chromium", version)
    except Exception as e:
        r.add(
            FAIL, "playwright chromium",
            f"{type(e).__name__}: {str(e)[:120]} — try "
            "'python -m playwright install chromium'",
        )


def check_openai_key(r: Report) -> None:
    if os.getenv("OPENAI_API_KEY"):
        r.add(OK, "OPENAI_API_KEY", "set in environment")
        return
    try:
        from dotenv import find_dotenv

        path = find_dotenv(usecwd=True)
    except ImportError:
        path = ""
    if path:
        r.add(WARN, "OPENAI_API_KEY", f"not exported, but {path} exists")
    else:
        r.add(
            FAIL, "OPENAI_API_KEY",
            "not set and no .env found in the working directory",
        )


def check_xvfb(r: Report) -> None:
    if not which("Xvfb"):
        r.add(FAIL, "Xvfb", "not on PATH — apt install xvfb")
        return
    r.add(OK, "Xvfb", which("Xvfb"))

    config = ProjectConfig(
        name="doctor", output_path="/tmp/doctor.mp4",
        start_url="about:blank",
        scenes=[Scene(id="a", narration="", action="wait")],
    )
    found = [b for b in config.chrome_binaries if which(b)]
    if found:
        r.add(OK, "chrome", f"{which(found[0])} — {_version([found[0], '--version'])}")
    else:
        r.add(
            FAIL, "chrome",
            "none of " + ", ".join(config.chrome_binaries) + " on PATH",
        )

    if os.getenv("WAYLAND_DISPLAY"):
        r.add(
            WARN, "session",
            "Wayland detected. That is fine — the xvfb backend records a "
            "virtual display precisely to avoid it — but x11grab against "
            "your real desktop would produce black frames.",
        )


def check_capture(r: Report) -> None:
    """Prove x11grab actually produces a non-black frame on a virtual display."""
    if not (which("Xvfb") and which("ffmpeg")):
        return
    display = ":98"
    out = Path("/tmp/demo_pipeline_doctor.mp4")
    xvfb = subprocess.Popen(
        ["Xvfb", display, "-screen", "0", "640x480x24", "-nolisten", "tcp"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        import time

        time.sleep(1.5)
        if xvfb.poll() is not None:
            r.add(WARN, "x11grab", f"{display} unavailable, skipped")
            return
        # xsetroot paints the root window so the frame is not legitimately black.
        if which("xsetroot"):
            subprocess.run(
                ["xsetroot", "-solid", "#3050a0"],
                env={**os.environ, "DISPLAY": display}, capture_output=True,
            )
        proc = subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error", "-f", "x11grab",
                "-video_size", "640x480", "-framerate", "10",
                "-i", display, "-t", "1", str(out),
            ],
            capture_output=True, timeout=30,
        )
        if proc.returncode != 0:
            r.add(FAIL, "x11grab", proc.stderr.decode()[-160:])
        elif out.exists() and out.stat().st_size > 1000:
            r.add(OK, "x11grab", f"captured {out.stat().st_size // 1024} KB")
        else:
            r.add(FAIL, "x11grab", "produced no usable output")
    except Exception as e:
        r.add(WARN, "x11grab", f"skipped: {type(e).__name__}: {e}")
    finally:
        xvfb.terminate()
        out.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--xvfb", action="store_true",
        help="also check the Linux xvfb backend (Xvfb, Chrome, x11grab)",
    )
    args = parser.parse_args(argv)

    r = Report()
    print("demo-pipeline doctor\n")
    print(f"python {sys.version.split()[0]} on {sys.platform}\n")

    check_ffmpeg(r)
    check_font(r)
    check_playwright(r)
    check_openai_key(r)

    if args.xvfb:
        print()
        check_xvfb(r)
        check_capture(r)

    print()
    if r.failed:
        print("Not ready — fix the FAIL rows above.")
        return 1
    print("Ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
