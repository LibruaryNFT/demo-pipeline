"""Title-card generation and drawtext escaping."""

from demo_pipeline import Branding, Encoding
from demo_pipeline.compose import (
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
