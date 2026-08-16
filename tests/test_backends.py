"""Backend selection and the xvfb command lines.

The xvfb backend spawns real processes, so these tests assert on the
argument vectors it builds rather than running them.
"""

from pathlib import Path

from onetake.recording import xvfb_backend

from .test_config import make_config


def xvfb_config(**overrides):
    kwargs = {
        "backend": "xvfb",
        "chrome_profile": "/tmp/demo-profile",
    }
    kwargs.update(overrides)
    return make_config(**kwargs)


class TestFfmpegCommand:
    def test_mouse_is_hidden_by_default(self):
        # On a virtual display the pointer never moves, so leaving it on
        # parks a stray arrow in the middle of every frame.
        args = _ffmpeg_args(xvfb_config())
        assert args[args.index("-draw_mouse") + 1] == "0"

    def test_mouse_can_be_shown(self):
        args = _ffmpeg_args(xvfb_config(draw_mouse=True))
        assert args[args.index("-draw_mouse") + 1] == "1"

    def test_captures_the_configured_display(self):
        args = _ffmpeg_args(xvfb_config(display_num=":42"))
        assert ":42" in args

    def test_uses_the_capture_preset_not_the_final_one(self):
        # Capture runs live and must not drop frames; the final encode is a
        # separate, slower pass in compose.
        config = xvfb_config()
        args = _ffmpeg_args(config)
        assert config.encoding.capture_preset in args


class TestChromeCommand:
    def test_extra_flags_are_appended(self):
        args = _chrome_args(xvfb_config(chrome_flags=("--kiosk",)))
        assert "--kiosk" in args

    def test_start_url_is_last(self):
        config = xvfb_config(chrome_flags=("--kiosk",))
        args = _chrome_args(config)
        assert args[-1] == config.start_url

    def test_profile_is_passed(self):
        config = xvfb_config()
        args = _chrome_args(config)
        assert f"--user-data-dir={config.chrome_profile}" in args

    def test_no_flags_by_default(self):
        assert "--kiosk" not in _chrome_args(xvfb_config())


class TestChromeResolution:
    def test_reports_every_candidate_when_none_found(self, monkeypatch):
        monkeypatch.setattr(xvfb_backend, "which", lambda _: None)
        config = xvfb_config(chrome_binaries=("nope-1", "nope-2"))
        try:
            xvfb_backend._resolve_chrome(config)
        except RuntimeError as e:
            assert "nope-1" in str(e) and "nope-2" in str(e)
        else:
            raise AssertionError("expected RuntimeError")

    def test_picks_the_first_binary_on_path(self, monkeypatch):
        monkeypatch.setattr(
            xvfb_backend, "which",
            lambda name: "/usr/bin/second" if name == "second" else None,
        )
        config = xvfb_config(chrome_binaries=("first", "second"))
        assert xvfb_backend._resolve_chrome(config) == "/usr/bin/second"


class TestProfileLock:
    def test_kill_matches_the_profile_not_the_binary(self, monkeypatch):
        # Regression: this used to pkill a hardcoded Chrome path, which both
        # missed chromium and would have killed the operator's own windows.
        captured = {}
        monkeypatch.setattr(
            xvfb_backend.subprocess, "run",
            lambda cmd, **kw: captured.setdefault("cmd", cmd),
        )
        monkeypatch.setattr(xvfb_backend.time, "sleep", lambda _: None)
        config = xvfb_config(chrome_profile="/tmp/unique-profile")
        xvfb_backend._kill_chrome(config, "/usr/bin/chromium")
        assert "--user-data-dir=/tmp/unique-profile" in captured["cmd"]
        assert not any("/opt/google" in str(part) for part in captured["cmd"])


# Helpers that mirror the backend's argument construction without spawning.


def _ffmpeg_args(config):
    import subprocess

    captured = {}

    class FakePopen:
        def __init__(self, cmd, **kw):
            captured["cmd"] = cmd

    real = subprocess.Popen
    subprocess.Popen = FakePopen
    try:
        xvfb_backend._start_ffmpeg(config, 10.0, Path("/tmp/out.mp4"))
    finally:
        subprocess.Popen = real
    return captured["cmd"]


def _chrome_args(config):
    import subprocess
    import tempfile

    captured = {}

    class FakePopen:
        def __init__(self, cmd, **kw):
            captured["cmd"] = cmd

    real_popen = subprocess.Popen
    subprocess.Popen = FakePopen
    try:
        # _launch_chrome opens the log file for real, so give it a live path.
        with tempfile.NamedTemporaryFile(suffix=".log") as log:
            xvfb_backend._launch_chrome(config, "/usr/bin/chrome", Path(log.name))
    finally:
        subprocess.Popen = real_popen
    return captured["cmd"]


class TestFramerateHonesty:
    """framerate only applies to xvfb; the other backend must say so."""

    def test_default_framerate_is_shared_not_duplicated(self):
        from onetake.config import DEFAULT_FRAMERATE
        from onetake.recording import playwright_backend

        assert playwright_backend.DEFAULT_FRAMERATE == DEFAULT_FRAMERATE
        assert make_config().framerate == DEFAULT_FRAMERATE

    def test_xvfb_passes_framerate_to_ffmpeg(self):
        args = _ffmpeg_args(xvfb_config(framerate=60))
        assert args[args.index("-framerate") + 1] == "60"

    async def test_playwright_warns_when_framerate_is_set(self, caplog):
        # Playwright's recorder has no framerate control, so a configured
        # value cannot be honoured. Silently ignoring it is the bug.
        from onetake.recording import playwright_backend

        config = make_config(framerate=60)
        with caplog.at_level("WARNING"):
            try:
                await playwright_backend.record(config, [])
            except Exception:
                pass  # no browser here; we only care about the warning
        assert any("framerate=60 ignored" in r.message for r in caplog.records)
