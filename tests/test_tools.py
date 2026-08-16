"""ffmpeg/ffprobe resolution and the duration fallback."""

import pytest

from demotape import tools


class TestParseDuration:
    def test_reads_the_banner_line(self):
        banner = "  Duration: 00:03:10.61, start: 0.000000, bitrate: 375 kb/s"
        assert tools.parse_duration(banner) == pytest.approx(190.61)

    def test_hours_are_counted(self):
        assert tools.parse_duration("Duration: 01:02:03.50,") == pytest.approx(3723.5)

    def test_whole_seconds_parse(self):
        assert tools.parse_duration("Duration: 00:00:07.00,") == pytest.approx(7.0)

    def test_no_duration_line_is_none_not_zero(self):
        """Zero would be accepted downstream and produce an empty video."""
        assert tools.parse_duration("ffmpeg version 6.0\nInvalid data found") is None

    def test_empty_output_is_none(self):
        assert tools.parse_duration("") is None


class TestResolveFfmpeg:
    def test_an_explicit_path_is_returned_untouched(self):
        assert tools.resolve_ffmpeg("/opt/custom/ffmpeg") == "/opt/custom/ffmpeg"

    def test_path_is_preferred_over_the_bundled_build(self, monkeypatch):
        monkeypatch.setattr(tools, "which", lambda name: "/usr/bin/ffmpeg")
        monkeypatch.setattr(tools, "_bundled_ffmpeg", lambda: "/bundled/ffmpeg")
        assert tools.resolve_ffmpeg() == "ffmpeg"

    def test_bundled_is_used_when_path_has_nothing(self, monkeypatch):
        monkeypatch.setattr(tools, "which", lambda name: None)
        monkeypatch.setattr(tools, "_bundled_ffmpeg", lambda: "/bundled/ffmpeg")
        assert tools.resolve_ffmpeg() == "/bundled/ffmpeg"

    def test_falls_back_to_the_bare_name_so_the_error_names_ffmpeg(self, monkeypatch):
        monkeypatch.setattr(tools, "which", lambda name: None)
        monkeypatch.setattr(tools, "_bundled_ffmpeg", lambda: None)
        assert tools.resolve_ffmpeg() == "ffmpeg"


class TestResolveFfprobe:
    def test_an_explicit_path_is_returned_untouched(self):
        assert tools.resolve_ffprobe("/opt/custom/ffprobe") == "/opt/custom/ffprobe"

    def test_path_is_used_when_present(self, monkeypatch):
        monkeypatch.setattr(tools, "which", lambda name: "/usr/bin/ffprobe")
        assert tools.resolve_ffprobe() == "ffprobe"

    def test_absent_ffprobe_is_none_not_an_error(self, monkeypatch):
        """None is a supported state: durations come from ffmpeg instead."""
        monkeypatch.setattr(tools, "which", lambda name: None)
        assert tools.resolve_ffprobe() is None


class TestBundledLookup:
    def test_a_missing_package_is_not_an_error(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def no_imageio(name, *args, **kwargs):
            if name == "imageio_ffmpeg":
                raise ImportError(name)
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", no_imageio)
        assert tools._bundled_ffmpeg() is None
