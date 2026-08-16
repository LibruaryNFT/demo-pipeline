"""Scene sequencing and setup_js re-injection."""

from demotape import Scene
from demotape.recording.timeline import play_scenes

from .test_actions import FakePage
from .test_config import make_config

SETUP_JS = "window.__DEMO__ = true;"


def segments(*scenes):
    return [{"scene": s, "duration": 0.0} for s in scenes]


class TestSetupJsReinjection:
    async def test_reinjected_when_a_handler_navigates(self):
        # A page load tears down injected state. The navigation may come
        # from a project handler doing its own goto, so the engine detects
        # it by URL rather than by which action ran.
        page = FakePage(url="https://example.com")

        async def go_elsewhere(page, params, duration):
            await page.goto("https://example.com/next")
            return 0.0

        config = make_config(
            setup_js=SETUP_JS, action_handlers={"go": go_elsewhere}
        )
        await play_scenes(
            page, config, segments(Scene(id="s", narration="", action="go"))
        )
        assert page.evaluated == [SETUP_JS]

    async def test_not_reinjected_when_the_url_is_unchanged(self):
        page = FakePage(url="https://example.com")
        config = make_config(setup_js=SETUP_JS)
        await play_scenes(
            page, config, segments(Scene(id="s", narration="", action="wait"))
        )
        assert page.evaluated == []

    async def test_no_injection_when_setup_js_is_unset(self):
        page = FakePage(url="https://example.com")

        async def go_elsewhere(page, params, duration):
            await page.goto("https://example.com/next")
            return 0.0

        config = make_config(action_handlers={"go": go_elsewhere})
        await play_scenes(
            page, config, segments(Scene(id="s", narration="", action="go"))
        )
        assert page.evaluated == []

    async def test_reinjected_once_per_navigating_scene(self):
        page = FakePage(url="https://example.com")
        counter = {"n": 0}

        async def go_elsewhere(page, params, duration):
            counter["n"] += 1
            await page.goto(f"https://example.com/{counter['n']}")
            return 0.0

        config = make_config(
            setup_js=SETUP_JS, action_handlers={"go": go_elsewhere}
        )
        await play_scenes(
            page,
            config,
            segments(
                Scene(id="a", narration="", action="go"),
                Scene(id="b", narration="", action="go"),
            ),
        )
        assert page.evaluated == [SETUP_JS, SETUP_JS]


class TestSceneSequencing:
    async def test_every_scene_runs(self):
        page = FakePage()
        ran = []

        async def record_it(page, params, duration):
            ran.append(params["id"])
            return 0.0

        config = make_config(action_handlers={"note": record_it})
        await play_scenes(
            page,
            config,
            segments(
                Scene(id="a", narration="", action="note", action_params={"id": "a"}),
                Scene(id="b", narration="", action="note", action_params={"id": "b"}),
                Scene(id="c", narration="", action="note", action_params={"id": "c"}),
            ),
        )
        assert ran == ["a", "b", "c"]

    async def test_a_failing_scene_does_not_stop_the_rest(self):
        page = FakePage()
        ran = []

        async def flaky(page, params, duration):
            if params.get("boom"):
                raise RuntimeError("nope")
            ran.append(params["id"])
            return 0.0

        config = make_config(action_handlers={"note": flaky})
        await play_scenes(
            page,
            config,
            segments(
                Scene(id="a", narration="", action="note", action_params={"id": "a"}),
                Scene(id="b", narration="", action="note", action_params={"boom": True}),
                Scene(id="c", narration="", action="note", action_params={"id": "c"}),
            ),
        )
        assert ran == ["a", "c"]


class TestMeasuredTimings:
    """play_scenes reports where each scene landed, so compose can remap an
    overrun rather than trim it off the end."""

    async def test_one_timing_per_scene_in_order(self):
        page = FakePage(url="https://example.com")
        scenes = [
            Scene(id="a", narration="", action="wait"),
            Scene(id="b", narration="", action="wait"),
        ]
        timeline = await play_scenes(page, make_config(), segments(*scenes))
        assert [t.id for t in timeline.scenes] == ["a", "b"]

    async def test_target_windows_come_from_the_narration_durations(self):
        page = FakePage(url="https://example.com")
        segs = [
            {"scene": Scene(id="a", narration="", action="wait"), "duration": 0.0},
            {"scene": Scene(id="b", narration="", action="wait"), "duration": 0.0},
        ]
        # Durations are zero so the test does not sleep; the targets are
        # still the cumulative sum, which is what the remap divides by.
        timeline = await play_scenes(page, make_config(), segs)
        assert [(t.target_start, t.target_end) for t in timeline.scenes] == [
            (0.0, 0.0), (0.0, 0.0),
        ]

    async def test_actual_spans_are_contiguous(self):
        """A gap between two scenes would be recorded footage the remap has
        no slot for, which shifts everything after it."""
        page = FakePage(url="https://example.com")
        scenes = [Scene(id=str(i), narration="", action="wait") for i in range(4)]
        timeline = await play_scenes(page, make_config(), segments(*scenes))
        for earlier, later in zip(timeline.scenes, timeline.scenes[1:], strict=False):
            assert earlier.actual_end == later.actual_start

    async def test_the_first_scene_starts_at_zero(self):
        page = FakePage(url="https://example.com")
        timeline = await play_scenes(
            page, make_config(), segments(Scene(id="a", narration="", action="wait"))
        )
        assert timeline.scenes[0].actual_start == 0.0

    async def test_t0_is_a_monotonic_reading_the_backends_can_subtract(self):
        import time

        before = time.monotonic()
        page = FakePage(url="https://example.com")
        timeline = await play_scenes(
            page, make_config(), segments(Scene(id="a", narration="", action="wait"))
        )
        assert before <= timeline.t0 <= time.monotonic()

    async def test_no_scenes_produces_an_empty_timeline_not_a_crash(self):
        page = FakePage(url="https://example.com")
        timeline = await play_scenes(page, make_config(), [])
        assert timeline.scenes == ()
