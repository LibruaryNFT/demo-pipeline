"""Probe projection, page interception and golden diffing."""

import asyncio

import pytest

from demotape.probe import (
    Call,
    ProbePage,
    SceneOutcome,
    _CappedSleep,
    diff_golden,
    normalise_target,
)


class FakePage:
    """Stands in for a Playwright Page, with one selector that works."""

    def __init__(self, good="ok-selector"):
        self.good = good
        self.url = "https://app.example/start"
        self.evaluated = []

    async def click(self, selector, timeout=None):
        if selector != self.good:
            raise RuntimeError(f"no element for {selector}")
        return None

    async def goto(self, url, wait_until=None, timeout=None):
        self.url = url
        return None

    async def evaluate(self, js):
        self.evaluated.append(js)
        return None


class TestNormaliseTarget:
    def test_goto_keeps_path_only(self):
        assert normalise_target("goto", "https://app.example/a/b") == "/a/b"

    def test_goto_keeps_query_because_it_selects_content(self):
        got = normalise_target("goto", "https://app.example/r?collection=allday")
        assert got == "/r?collection=allday"

    def test_goto_bare_host_is_root(self):
        assert normalise_target("goto", "https://app.example") == "/"

    def test_host_is_dropped_so_staging_matches_production(self):
        prod = normalise_target("goto", "https://app.example/portfolio")
        staging = normalise_target("goto", "https://staging.app.example/portfolio")
        assert prod == staging

    def test_selectors_are_left_alone(self):
        sel = "table tbody tr:nth-child(2) td a"
        assert normalise_target("click", sel) == sel

    def test_selector_list_keeps_order_because_order_is_the_fallback_chain(self):
        assert normalise_target("click", ["a", "b"]) == "a | b"
        assert normalise_target("click", ["b", "a"]) != normalise_target("click", ["a", "b"])

    def test_none_is_empty(self):
        assert normalise_target("click", None) == ""


class TestProbePage:
    def test_successful_call_is_recorded(self):
        page = ProbePage(FakePage())
        asyncio.run(page.click("ok-selector"))
        assert page.calls == [Call("click", "ok-selector", ok=True)]

    def test_failure_is_recorded_and_re_raised(self):
        page = ProbePage(FakePage())
        with pytest.raises(RuntimeError):
            asyncio.run(page.click("gone"))
        assert page.calls == [Call("click", "gone", ok=False)]

    def test_a_handler_swallowing_the_error_still_leaves_the_evidence(self):
        """The whole point: `try/except` in project code hides the failure
        from the render but not from the probe."""
        page = ProbePage(FakePage())

        async def handler_with_fallback():
            try:
                await page.click("gone")
            except Exception:
                pass  # exactly what platform_tour.py does

        asyncio.run(handler_with_fallback())
        assert page.calls == [Call("click", "gone", ok=False)]

    def test_unwatched_methods_are_forwarded_untouched(self):
        raw = FakePage()
        page = ProbePage(raw)
        asyncio.run(page.evaluate("window.x = 1"))
        assert raw.evaluated == ["window.x = 1"]
        assert page.calls == []

    def test_goto_target_is_normalised_at_record_time(self):
        page = ProbePage(FakePage())
        asyncio.run(page.goto("https://app.example/deep/link?q=1"))
        assert page.calls == [Call("goto", "/deep/link?q=1", ok=True)]

    def test_take_calls_empties_the_buffer(self):
        page = ProbePage(FakePage())
        asyncio.run(page.click("ok-selector"))
        assert len(page.take_calls()) == 1
        assert page.take_calls() == []

    def test_url_passes_through(self):
        raw = FakePage()
        assert ProbePage(raw).url == raw.url


class TestCappedSleep:
    def test_long_sleeps_are_shortened(self):
        async def go():
            with _CappedSleep(0.01):
                await asyncio.sleep(30)

        # Would take half a minute unpatched; the test finishing is the assertion.
        asyncio.run(asyncio.wait_for(go(), timeout=5))

    def test_the_real_sleep_is_restored_afterwards(self):
        original = asyncio.sleep
        with _CappedSleep(0.01):
            assert asyncio.sleep is not original
        assert asyncio.sleep is original

    def test_restored_even_when_the_body_raises(self):
        original = asyncio.sleep
        with pytest.raises(ValueError):
            with _CappedSleep(0.01):
                raise ValueError
        assert asyncio.sleep is original


def golden(**scene_overrides) -> dict:
    scene = {"id": "a", "action": "click", "ok": True, "calls": [], "url": "/"}
    scene.update(scene_overrides)
    return {"version": 1, "name": "demo", "scenes": [scene]}


class TestDiffGolden:
    def test_identical_is_clean(self):
        assert diff_golden(golden(), golden()) == []

    def test_a_scene_that_stopped_working(self):
        (line,) = diff_golden(golden(), golden(ok=False))
        assert line == "scenes.a.ok: expected True, got False"

    def test_a_route_that_moved(self):
        (line,) = diff_golden(golden(), golden(url="/404"))
        assert "expected '/', got '/404'" in line

    def test_a_selector_that_stopped_resolving(self):
        before = golden(calls=[{"op": "click", "target": "#buy", "ok": True}])
        after = golden(calls=[{"op": "click", "target": "#buy", "ok": False}])
        (line,) = diff_golden(before, after)
        assert line.startswith("scenes.a.calls[0]:")

    def test_a_removed_scene(self):
        after = golden()
        after["scenes"] = []
        assert "scenes.a: expected present, got missing" in diff_golden(golden(), after)

    def test_an_added_scene(self):
        after = golden()
        after["scenes"].append({"id": "b", "action": "wait", "ok": True, "calls": [], "url": "/"})
        assert "scenes.b: expected missing, got present" in diff_golden(golden(), after)

    def test_reordering_is_reported_once_not_as_every_scene_renamed(self):
        def two(first, second):
            return {
                "version": 1,
                "name": "demo",
                "scenes": [
                    {"id": first, "action": "wait", "ok": True, "calls": [], "url": "/"},
                    {"id": second, "action": "wait", "ok": True, "calls": [], "url": "/"},
                ],
            }

        lines = diff_golden(two("a", "b"), two("b", "a"))
        assert lines == ["scenes order: expected ['a', 'b'], got ['b', 'a']"]

    def test_call_count_change_is_named_before_the_element_diffs(self):
        before = golden(calls=[{"op": "click", "target": "#a", "ok": True}])
        after = golden(calls=[])
        assert diff_golden(before, after)[0] == "scenes.a.calls: expected 1 call(s), got 0"

    def test_renaming_the_project_is_caught(self):
        after = golden()
        after["name"] = "other"
        assert "name: expected 'demo', got 'other'" in diff_golden(golden(), after)


class TestProjectionIsTimingFree:
    """The baseline must contain nothing that varies run to run, or it is
    noise and gets ignored, which is worse than not having it."""

    def test_scene_outcome_has_no_duration_or_timestamp_fields(self):
        keys = SceneOutcome(id="a", action="wait", ok=True).as_dict().keys()
        assert set(keys) == {"id", "action", "ok", "calls", "url"}

    def test_call_has_no_duration_field(self):
        assert set(Call("click", "#x", True).as_dict()) == {"op", "target", "ok"}
