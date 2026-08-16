"""Config dataclass behaviour: defaults, derived paths, backend validation."""

from pathlib import Path

import pytest

from demotape import (
    BACKEND_PLAYWRIGHT,
    BACKEND_XVFB,
    Branding,
    ProjectConfig,
    Scene,
    TitleCard,
)
from demotape.config import default_font


def make_config(**overrides) -> ProjectConfig:
    kwargs = {
        "name": "Test App",
        "output_path": "out/demo.mp4",
        "start_url": "https://example.com",
        "scenes": [Scene(id="hook", narration="Hello.", action="wait")],
    }
    kwargs.update(overrides)
    return ProjectConfig(**kwargs)


class TestScene:
    def test_creation(self):
        scene = Scene(id="hook", narration="Hello.", action="wait")
        assert scene.id == "hook"
        assert scene.action_params == {}

    def test_with_params(self):
        scene = Scene(
            id="scroll_down",
            narration="Scrolling.",
            action="scroll",
            action_params={"y": 800},
        )
        assert scene.action_params["y"] == 800

    def test_params_are_not_shared_between_instances(self):
        a = Scene(id="a", narration="", action="wait")
        b = Scene(id="b", narration="", action="wait")
        a.action_params["x"] = 1
        assert b.action_params == {}


class TestTitleCard:
    def test_defaults(self):
        card = TitleCard()
        assert card.duration == 4.0
        assert card.bg_color == "0x0a0a0b"
        assert card.lines == []


class TestBranding:
    def test_all_fields_optional(self):
        branding = Branding()
        assert branding.tagline == ""
        assert branding.author == ""
        assert branding.link == ""
        assert branding.context == ""

    def test_accent_has_default(self):
        assert Branding().accent.startswith("0x")


class TestProjectConfig:
    def test_derives_audio_and_work_dirs_from_output(self):
        config = make_config(output_path="/tmp/renders/launch.mp4")
        assert config.audio_dir == Path("/tmp/renders/launch_audio")
        assert config.work_dir == Path("/tmp/renders/launch_work")

    def test_explicit_dirs_are_respected(self):
        config = make_config(audio_dir="/tmp/a", work_dir="/tmp/w")
        assert config.audio_dir == Path("/tmp/a")
        assert config.work_dir == Path("/tmp/w")

    def test_coerces_str_paths_to_path(self):
        config = make_config()
        assert isinstance(config.output_path, Path)

    def test_default_backend_is_playwright(self):
        assert make_config().backend == BACKEND_PLAYWRIGHT

    def test_playwright_backend_needs_no_chrome_profile(self):
        config = make_config()
        assert config.chrome_profile is None

    def test_rejects_unknown_backend(self):
        with pytest.raises(ValueError, match="unknown backend"):
            make_config(backend="obs")

    def test_xvfb_backend_requires_chrome_profile(self):
        with pytest.raises(ValueError, match="requires chrome_profile"):
            make_config(backend=BACKEND_XVFB)

    def test_xvfb_backend_accepts_chrome_profile(self):
        config = make_config(backend=BACKEND_XVFB, chrome_profile="/tmp/profile")
        assert config.chrome_profile == Path("/tmp/profile")

    def test_default_branding_is_empty(self):
        assert make_config().branding.tagline == ""

    def test_resolution_and_tts_defaults(self):
        config = make_config()
        assert config.resolution == (1920, 1080)
        assert config.tts_model == "tts-1-hd"


class TestDefaultFont:
    def test_returns_a_non_empty_string(self):
        assert default_font()
