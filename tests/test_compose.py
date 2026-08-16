"""Title-card generation and drawtext escaping."""

from onetake import Branding, Encoding
from onetake.compose import (
    _escape_drawtext,
    default_intro,
    default_outro,
    faststart_args,
)


def texts(card) -> list[str]:
    return [line["text"] for line in card.lines]


class TestDefaultIntro:
    def test_name_only_when_no_tagline(self):
        card = default_intro("My App", Branding())
        assert texts(card) == ["My App"]

    def test_includes_tagline_when_set(self):
        card = default_intro("My App", Branding(tagline="Does the thing"))
        assert texts(card) == ["My App", "Does the thing"]

    def test_uses_accent_colour_for_name(self):
        card = default_intro("My App", Branding(accent="0xff0000"))
        assert card.lines[0]["color"] == "0xff0000"


class TestDefaultOutro:
    def test_name_only_when_branding_is_empty(self):
        card = default_outro("My App", Branding())
        assert texts(card) == ["My App"]

    def test_renders_every_field_when_all_set(self):
        card = default_outro(
            "My App",
            Branding(author="A Team", link="example.com", context="Demo Day"),
        )
        assert texts(card) == [
            "My App",
            "Built by A Team",
            "example.com",
            "Demo Day",
        ]

    def test_skips_blank_fields(self):
        card = default_outro("My App", Branding(link="example.com"))
        assert texts(card) == ["My App", "example.com"]

    def test_single_line_card_is_centred(self):
        card = default_outro("My App", Branding())
        assert card.lines[0]["y_offset"] == 0

    def test_lines_are_ordered_top_to_bottom(self):
        card = default_outro(
            "My App",
            Branding(author="A Team", link="example.com", context="Demo Day"),
        )
        offsets = [line["y_offset"] for line in card.lines]
        assert offsets == sorted(offsets)

    def test_lines_do_not_collide(self):
        # Regression: a fixed pixel step let the 72pt name overlap the 32pt
        # byline beneath it. Each gap must clear the taller line's height.
        card = default_outro(
            "My App",
            Branding(author="A Team", link="example.com", context="Demo Day"),
        )
        lines = card.lines
        for upper, lower in zip(lines, lines[1:], strict=False):
            gap = lower["y_offset"] - upper["y_offset"]
            assert gap >= max(upper["size"], lower["size"]) * 0.75, (
                f"{upper['text']!r} and {lower['text']!r} are {gap}px apart"
            )

    def test_stack_stays_roughly_centred(self):
        card = default_outro(
            "My App", Branding(author="A Team", link="example.com")
        )
        offsets = [line["y_offset"] for line in card.lines]
        midpoint = (offsets[0] + offsets[-1]) / 2
        assert abs(midpoint) < 40


class TestEscapeDrawtext:
    def test_plain_text_is_unchanged(self):
        assert _escape_drawtext("Hello world") == "Hello world"

    def test_escapes_colon(self):
        # An unescaped colon truncates the ffmpeg filter chain silently.
        assert _escape_drawtext("Ratio 3:1") == r"Ratio 3\:1"

    def test_escapes_single_quote(self):
        assert _escape_drawtext("It's here") == r"It\'s here"

    def test_escapes_percent(self):
        assert _escape_drawtext("100% done") == r"100\% done"

    def test_escapes_backslash_first(self):
        # Backslash must be escaped before the others, or their escapes get
        # double-escaped in turn.
        assert _escape_drawtext("a\\b") == r"a\\b"

    def test_branding_with_punctuation_survives(self):
        card = default_intro("Acme", Branding(tagline="Fast, cheap: pick two"))
        escaped = _escape_drawtext(card.lines[1]["text"])
        assert r"\:" in escaped


class TestFaststart:
    """The moov atom belongs at the front of anything served over HTTP.

    Without it a browser buffers the entire file before the first frame, so
    a linked demo looks broken rather than slow.
    """

    def test_on_by_default(self):
        assert faststart_args(Encoding()) == ["-movflags", "+faststart"]

    def test_can_be_turned_off(self):
        assert faststart_args(Encoding(faststart=False)) == []


class TestVideoArgs:
    """Step 2's video half: a plain filter chain, or a rate remap when one
    is asked for and the timings justify it."""

    def args(self, *, timelapse: bool, scenes=(), lead_in=0.0):
        from pathlib import Path

        from onetake import Capture
        from onetake.compose import _video_args

        from .test_config import make_config

        config = make_config()
        config.timing.timelapse = timelapse
        capture = Capture(
            path=Path("/tmp/x.mp4"), lead_in_s=lead_in, scenes=tuple(scenes)
        )
        return _video_args(config, capture, audio_dur=15.0)

    def overran(self):
        from onetake import SceneTiming

        return [
            SceneTiming("a", 0.0, 5.0, 0.0, 5.0),
            SceneTiming("b", 5.0, 10.0, 5.0, 12.0),
            SceneTiming("c", 10.0, 15.0, 12.0, 15.0),
        ]

    def test_off_by_default_is_a_plain_vf_chain(self):
        args = self.args(timelapse=False, scenes=self.overran())
        assert args[0] == "-vf"
        assert "scale=" in args[1]

    def test_an_on_time_render_stays_on_the_plain_chain(self):
        from onetake import SceneTiming

        on_time = [SceneTiming("a", 0.0, 5.0, 0.0, 5.0)]
        assert self.args(timelapse=True, scenes=on_time)[0] == "-vf"

    def test_an_overrun_switches_to_a_filter_complex(self):
        args = self.args(timelapse=True, scenes=self.overran())
        assert args[0] == "-filter_complex"
        assert "trim=" in args[1]

    def test_the_remap_feeds_the_same_scale_and_fade_as_the_plain_path(self):
        plain = self.args(timelapse=False, scenes=self.overran())[1]
        remapped = self.args(timelapse=True, scenes=self.overran())[1]
        assert remapped.endswith(f";[tl]{plain}[v]")

    def test_naming_the_video_output_forces_the_audio_map_too(self):
        """ffmpeg stops choosing streams once the graph has a named output.
        Without an explicit 1:a the narration is silently dropped."""
        args = self.args(timelapse=True, scenes=self.overran())
        assert args[2:] == ["-map", "[v]", "-map", "1:a"]

    def test_a_capture_with_no_timings_cannot_remap(self):
        assert self.args(timelapse=True, scenes=[])[0] == "-vf"
