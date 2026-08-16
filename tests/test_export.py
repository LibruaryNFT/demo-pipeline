"""Derived outputs: shape filter graphs and path/argument handling."""


import pytest

from onetake import Scene
from onetake.export import SHAPES, export_gif, export_shape

from .test_config import make_config


def config(tmp_path, **kw):
    return make_config(
        output_path=tmp_path / "demo.mp4",
        scenes=[Scene(id="a", narration="n", action="wait")],
        **kw,
    )


class TestShapeFilters:
    def test_the_three_documented_shapes_exist(self):
        assert set(SHAPES) == {"landscape", "square", "vertical"}

    @pytest.mark.parametrize("shape", sorted(SHAPES))
    def test_every_graph_ends_on_the_labelled_pad_that_is_mapped(self, shape):
        assert SHAPES[shape].endswith("[v]")

    @pytest.mark.parametrize("shape", sorted(SHAPES))
    def test_every_graph_normalises_sample_aspect_ratio(self, shape):
        """A non-square SAR survives the scale and shows up as a subtly
        stretched picture on some players."""
        assert "setsar=1" in SHAPES[shape]

    def test_vertical_letterboxes_over_a_blurred_copy(self):
        """Re-rendering the app at a narrow viewport would change its
        responsive layout, so the vertical cut would show a different
        product. Blurred fill keeps the demo identical."""
        graph = SHAPES["vertical"]
        assert "split=2" in graph
        assert "boxblur" in graph
        assert "overlay=" in graph

    def test_square_crops_from_the_centre(self):
        assert "crop=ih:ih:(iw-ih)/2:0" in SHAPES["square"]

    def test_landscape_pads_rather_than_cropping_so_nothing_is_lost(self):
        assert "force_original_aspect_ratio=decrease" in SHAPES["landscape"]
        assert "pad=" in SHAPES["landscape"]


class TestExportShapeGuards:
    def test_an_unknown_shape_lists_the_valid_ones(self, tmp_path):
        cfg = config(tmp_path)
        cfg.output_path.write_bytes(b"x")
        with pytest.raises(ValueError, match="landscape, square, vertical"):
            export_shape(cfg, "widescreen")

    def test_a_missing_source_says_to_render_first(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="render it first"):
            export_shape(config(tmp_path), "square")

    def test_the_shape_is_checked_before_the_file(self, tmp_path):
        """A typo should be reported as a typo, not as a missing render."""
        with pytest.raises(ValueError):
            export_shape(config(tmp_path), "nope")


class TestExportGifGuards:
    def test_a_missing_source_says_to_render_first(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="render it first"):
            export_gif(config(tmp_path))


class TestDefaultOutputPaths:
    def test_gif_sits_next_to_the_video(self, tmp_path):
        cfg = config(tmp_path)
        assert cfg.output_path.with_suffix(".gif") == tmp_path / "demo.gif"

    def test_shape_names_go_in_the_filename_not_a_subdirectory(self, tmp_path):
        cfg = config(tmp_path)
        source = cfg.output_path
        expected = source.with_name(f"{source.stem}.vertical{source.suffix}")
        assert expected == tmp_path / "demo.vertical.mp4"
        assert expected.parent == source.parent
