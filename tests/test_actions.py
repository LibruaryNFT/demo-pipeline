"""Built-in action handlers and handler resolution."""

from unittest.mock import AsyncMock

from demo_pipeline import Scene
from demo_pipeline.actions import (
    DEFAULT_ACTION_HANDLERS,
    action_wait,
    resolve_handlers,
    run_scene_action,
)

from .test_config import make_config


class TestBuiltins:
    def test_expected_verbs_are_registered(self):
        assert set(DEFAULT_ACTION_HANDLERS) == {
            "wait", "scroll", "navigate", "click", "hover"
        }

    async def test_wait_consumes_no_action_time(self):
        assert await action_wait(None, {}, 5.0) == 0.0


class TestResolveHandlers:
    def test_includes_builtins(self):
        handlers = resolve_handlers(make_config())
        assert "click" in handlers

    def test_project_handlers_are_added(self):
        custom = AsyncMock(return_value=1.0)
        handlers = resolve_handlers(make_config(action_handlers={"my_verb": custom}))
        assert handlers["my_verb"] is custom

    def test_project_handlers_override_builtins(self):
        custom = AsyncMock(return_value=1.0)
        handlers = resolve_handlers(make_config(action_handlers={"click": custom}))
        assert handlers["click"] is custom


class TestRunSceneAction:
    async def test_dispatches_to_the_named_handler(self):
        custom = AsyncMock(return_value=2.5)
        scene = Scene(id="s", narration="", action="my_verb", action_params={"a": 1})
        result = await run_scene_action({"my_verb": custom}, "page", scene, 5.0)
        assert result == 2.5
        custom.assert_awaited_once_with("page", {"a": 1}, 5.0)

    async def test_unknown_action_falls_back_to_wait(self):
        scene = Scene(id="s", narration="", action="does_not_exist")
        assert await run_scene_action({}, "page", scene, 5.0) == 0.0

    async def test_failing_handler_does_not_abort_the_render(self):
        # One broken selector should cost its own scene's choreography, not
        # the whole video.
        boom = AsyncMock(side_effect=RuntimeError("selector not found"))
        scene = Scene(id="s", narration="", action="click")
        assert await run_scene_action({"click": boom}, "page", scene, 5.0) == 0.0

    async def test_handler_returning_none_is_coerced_to_zero(self):
        noisy = AsyncMock(return_value=None)
        scene = Scene(id="s", narration="", action="click")
        assert await run_scene_action({"click": noisy}, "page", scene, 5.0) == 0.0
