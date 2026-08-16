"""Overlay script generation, call marshalling and per-scene zoom logic."""

import asyncio

from demo_pipeline import Overlay, ProjectConfig, Scene
from demo_pipeline.recording import overlay as ov
from demo_pipeline.recording.timeline import _apply_zoom


class FakePage:
    """Records what would have been evaluated, and can be told to fail."""

    def __init__(self, result=True, raises=False):
        self.result = result
        self.raises = raises
        self.evaluated: list[str] = []
        self.init_scripts: list[str] = []

    async def evaluate(self, expression):
        if self.raises:
            raise RuntimeError("page is navigating")
        self.evaluated.append(expression)
        return self.result

    async def add_init_script(self, script):
        self.init_scripts.append(script)


def config(**overlay_kwargs) -> ProjectConfig:
    return ProjectConfig(
        name="T",
        output_path="/tmp/t.mp4",
        start_url="https://example.test",
        scenes=[],
        overlay=Overlay(**overlay_kwargs),
    )


class TestBuildOverlayJs:
    def test_no_placeholder_survives_substitution(self):
        js = ov.build_overlay_js()
        assert "__NS__" not in js
        assert "__ACCENT__" not in js
        assert "__FONT__" not in js

    def test_namespace_is_applied(self):
        assert f"window.{ov.OVERLAY_NAMESPACE}" in ov.build_overlay_js()

    def test_accent_and_font_are_injected(self):
        js = ov.build_overlay_js(accent="#00ff00", font="Comic Sans")
        assert "#00ff00" in js
        assert "Comic Sans" in js

    def test_blank_font_falls_back_to_a_system_stack(self):
        assert "system-ui" in ov.build_overlay_js(font="")

    def test_root_is_attached_to_documentelement_not_body(self):
        """The single detail the whole design rests on: body is what zoom
        scales, so an overlay inside it would scale with the page."""
        js = ov.build_overlay_js()
        assert "(document.documentElement || document.body).appendChild(root)" in js

    def test_zoom_transforms_body(self):
        assert "b.style.transform = 'scale(' + scale + ')'" in ov.build_overlay_js()


class TestInstall:
    def test_disabled_injects_nothing(self):
        page = FakePage()
        asyncio.run(ov.install(page, config(enabled=False)))
        assert page.init_scripts == []
        assert page.evaluated == []

    def test_enabled_registers_for_future_documents_and_the_current_one(self):
        page = FakePage()
        asyncio.run(ov.install(page, config()))
        assert len(page.init_scripts) == 1
        # add_init_script does not touch the already-open page, so the same
        # source has to be evaluated directly as well.
        assert page.evaluated == page.init_scripts


class TestCallMarshalling:
    def test_caption_text_is_json_encoded(self):
        page = FakePage()
        asyncio.run(ov.caption(page, 'He said "hi" \\ then left'))
        assert page.evaluated[0].startswith(f"{ov.OVERLAY_NAMESPACE}.caption(")
        assert '"He said \\"hi\\" \\\\ then left"' in page.evaluated[0]

    def test_a_colon_needs_no_escaping_unlike_ffmpeg_drawtext(self):
        page = FakePage()
        asyncio.run(ov.caption(page, "Ratio: 2:1 at 100%"))
        assert "Ratio: 2:1 at 100%" in page.evaluated[0]

    def test_seconds_become_milliseconds(self):
        page = FakePage()
        asyncio.run(ov.caption(page, "x", 2.5))
        assert page.evaluated[0].endswith(", 2500)")

    def test_no_duration_means_hold_until_replaced(self):
        page = FakePage()
        asyncio.run(ov.caption(page, "x", None))
        assert page.evaluated[0].endswith(", 0)")

    def test_zoom_passes_selector_scale_and_duration(self):
        page = FakePage()
        asyncio.run(ov.zoom(page, "#chart", 1.8, 700))
        assert page.evaluated[0] == f'{ov.OVERLAY_NAMESPACE}.zoom("#chart", 1.8, 700)'

    def test_a_selector_containing_quotes_is_encoded(self):
        page = FakePage()
        asyncio.run(ov.zoom(page, 'a:has-text("Stats")', 1.5, 700))
        assert '"a:has-text(\\"Stats\\")"' in page.evaluated[0]


class TestFailureIsNeverFatal:
    """Losing a caption is cosmetic. Losing the render is not."""

    def test_a_raising_page_returns_false_rather_than_propagating(self):
        page = FakePage(raises=True)
        assert asyncio.run(ov.caption(page, "x")) is False
        assert asyncio.run(ov.zoom(page, "#a", 1.5, 700)) is False
        assert asyncio.run(ov.zoom_reset(page, 600)) is False
        assert asyncio.run(ov.caption_hide(page)) is False

    def test_a_missing_zoom_target_reports_false(self):
        page = FakePage(result=False)
        assert asyncio.run(ov.zoom(page, "#gone", 1.5, 700)) is False


class TestApplyZoom:
    """Whether the camera holds, moves, or pulls back between scenes."""

    def scene(self, **kw):
        base = {"id": "s", "narration": "n", "action": "wait"}
        base.update(kw)
        return Scene(**base)

    def test_a_scene_with_a_target_zooms_and_reports_active(self):
        page = FakePage()
        active = asyncio.run(_apply_zoom(page, self.scene(zoom="#a"), Overlay(), False))
        assert active is True
        assert ".zoom(" in page.evaluated[0]

    def test_a_scene_without_a_target_releases_a_previous_zoom(self):
        page = FakePage()
        active = asyncio.run(_apply_zoom(page, self.scene(), Overlay(), True))
        assert active is False
        assert ".zoomReset(" in page.evaluated[0]

    def test_no_zoom_and_none_active_does_nothing_at_all(self):
        page = FakePage()
        active = asyncio.run(_apply_zoom(page, self.scene(), Overlay(), False))
        assert active is False
        assert page.evaluated == []

    def test_consecutive_zooms_pan_rather_than_bouncing_out_and_back_in(self):
        page = FakePage()
        asyncio.run(_apply_zoom(page, self.scene(zoom="#b"), Overlay(), True))
        assert not any("zoomReset" in e for e in page.evaluated)

    def test_a_target_that_does_not_resolve_leaves_no_zoom_active(self):
        page = FakePage(result=False)
        active = asyncio.run(_apply_zoom(page, self.scene(zoom="#gone"), Overlay(), False))
        assert active is False

    def test_scene_scale_is_honoured(self):
        page = FakePage()
        asyncio.run(_apply_zoom(page, self.scene(zoom="#a", zoom_scale=3.0), Overlay(), False))
        assert ", 3.0, " in page.evaluated[0]


class TestSceneDefaults:
    def test_a_scene_has_no_caption_or_zoom_unless_asked(self):
        scene = Scene(id="a", narration="n", action="wait")
        assert scene.caption == ""
        assert scene.zoom == ""

    def test_overlay_is_on_by_default_but_inert_without_captions_or_zoom(self):
        cfg = config()
        assert cfg.overlay.enabled is True
