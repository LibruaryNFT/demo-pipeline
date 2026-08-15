"""Built-in action handlers, handler resolution and dispatch."""

from unittest.mock import AsyncMock

import pytest

from demo_pipeline import Scene
from demo_pipeline.actions import (
    DEFAULT_ACTION_HANDLERS,
    action_click,
    action_wait,
    resolve_handlers,
    run_scene_action,
)
from demo_pipeline.config import Timing

from .test_config import make_config

TIMING = Timing()


class FakePage:
    """Minimal Playwright Page stand-in."""

    def __init__(self, fail_selectors=(), url="https://example.com"):
        self.fail_selectors = set(fail_selectors)
        self.url = url
        self.clicked = []
        self.evaluated = []

    async def click(self, selector, timeout=None):
        self.clicked.append(selector)
        if selector in self.fail_selectors:
            raise RuntimeError(f"no element: {selector}")

    async def evaluate(self, js):
        self.evaluated.append(js)

    async def goto(self, url, wait_until=None, timeout=None):
        self.url = url


class TestBuiltins:
    def test_expected_verbs_are_registered(self):
        assert set(DEFAULT_ACTION_HANDLERS) == {
            "wait", "scroll", "navigate", "click", "hover", "evaluate"
        }

    async def test_wait_consumes_no_action_time(self):
        assert await action_wait(None, {}, 5.0, TIMING) == 0.0


class TestActionClick:
    async def test_clicks_a_single_selector(self):
        page = FakePage()
        await action_click(page, {"selector": "#go", "settle": 0}, 5.0, TIMING)
        assert page.clicked == ["#go"]

    async def test_falls_through_a_selector_list(self):
        # A UI that renders a control as a button in one state and a tab in
        # another needs more than one candidate.
        page = FakePage(fail_selectors={"#first", "#second"})
        await action_click(
            page,
            {"selector": ["#first", "#second", "#third"], "settle": 0},
            5.0,
            TIMING,
        )
        assert page.clicked == ["#first", "#second", "#third"]

    async def test_raises_when_no_selector_matches(self):
        page = FakePage(fail_selectors={"#a", "#b"})
        with pytest.raises(RuntimeError, match="no selector matched"):
            await action_click(
                page, {"selector": ["#a", "#b"], "settle": 0}, 5.0, TIMING
            )


class TestResolveHandlers:
    def test_includes_builtins(self):
        assert "click" in resolve_handlers(make_config())

    def test_project_handlers_are_added(self):
        custom = AsyncMock(return_value=1.0)
        handlers = resolve_handlers(make_config(action_handlers={"verb": custom}))
        assert handlers["verb"] is custom

    def test_project_handlers_override_builtins(self):
        custom = AsyncMock(return_value=1.0)
        handlers = resolve_handlers(make_config(action_handlers={"click": custom}))
        assert handlers["click"] is custom


class TestRunSceneAction:
    async def test_project_handlers_use_the_three_arg_form(self):
        # The documented signature for user handlers is (page, params,
        # duration). They must not be handed the engine's timing object.
        seen = {}

        async def handler(page, params, duration):
            seen["args"] = (page, params, duration)
            return 2.5

        scene = Scene(id="s", narration="", action="verb", action_params={"a": 1})
        result = await run_scene_action(
            {"verb": handler}, "page", scene, 5.0, TIMING
        )
        assert result == 2.5
        assert seen["args"] == ("page", {"a": 1}, 5.0)

    async def test_builtins_receive_timing(self):
        page = FakePage()
        scene = Scene(
            id="s", narration="", action="click",
            action_params={"selector": "#go", "settle": 0},
        )
        await run_scene_action(DEFAULT_ACTION_HANDLERS, page, scene, 5.0, TIMING)
        assert page.clicked == ["#go"]

    async def test_unknown_action_falls_back_to_wait(self):
        scene = Scene(id="s", narration="", action="does_not_exist")
        assert await run_scene_action({}, "page", scene, 5.0, TIMING) == 0.0

    async def test_failing_handler_does_not_abort_the_render(self):
        # One broken selector should cost its own scene's choreography, not
        # the whole video.
        async def boom(page, params, duration):
            raise RuntimeError("selector not found")

        scene = Scene(id="s", narration="", action="click")
        assert await run_scene_action(
            {"click": boom}, "page", scene, 5.0, TIMING
        ) == 0.0

    async def test_handler_returning_none_is_coerced_to_zero(self):
        async def quiet(page, params, duration):
            return None

        scene = Scene(id="s", narration="", action="click")
        assert await run_scene_action(
            {"click": quiet}, "page", scene, 5.0, TIMING
        ) == 0.0
