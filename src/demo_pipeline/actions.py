"""Built-in scene action handlers, shared by both recording backends.

Scenes name actions by string. The engine resolves them against these
built-ins first, then against the project's `action_handlers` override map,
so a project can replace `click` wholesale or add its own verbs.

Each handler is async with signature `(page, params, duration) -> float`,
returning the seconds spent on the active part of the action. The engine
sleeps the remainder so narration and picture stay locked.

Handlers read their waits and timeouts from `params`, falling back to the
project's `Timing` defaults. So a single slow scene can be tuned in place:

    Scene(id="load", narration="...", action="navigate",
          action_params={"url": "/reports", "settle": 6.0})

Prefer text-based selectors (`button:has-text('Get started')`) over class
selectors — the rendered UI is the source of truth and `:has-text` survives
most class renames.
"""

import asyncio
import logging

logger = logging.getLogger(__name__)


async def action_wait(page, params: dict, duration: float, timing) -> float:
    """No-op — hold the current view for the scene duration."""
    return 0.0


async def action_scroll(page, params: dict, duration: float, timing) -> float:
    """Smooth-scroll to a y offset.

    Pass `relative: True` to scroll by the offset instead of to it.
    """
    y = params.get("y", 0)
    behavior = params.get("behavior", "smooth")
    wait = params.get("wait", timing.scroll_s)
    fn = "scrollBy" if params.get("relative") else "scrollTo"
    await page.evaluate(f"window.{fn}({{top: {y}, behavior: '{behavior}'}})")
    await asyncio.sleep(wait)
    return wait + 0.3


async def action_navigate(page, params: dict, duration: float, timing) -> float:
    """Navigate to a URL, wait for load, settle."""
    url = params["url"]
    settle = params.get("settle", timing.settle_s)
    await page.goto(
        url,
        wait_until=params.get("wait_until", timing.wait_until),
        timeout=params.get("timeout_ms", timing.navigate_ms),
    )
    await asyncio.sleep(settle)
    return settle + 0.5


async def action_click(page, params: dict, duration: float, timing) -> float:
    """Click an element by selector, then wait for a settle period.

    `selector` may be a list, in which case each is tried in order until one
    works. That is the practical way to survive a UI that renders a control
    as a button in one state and a tab in another.
    """
    selectors = params["selector"]
    if isinstance(selectors, str):
        selectors = [selectors]
    settle = params.get("settle", timing.settle_s)
    timeout = params.get("timeout_ms", timing.selector_ms)

    for selector in selectors:
        try:
            await page.click(selector, timeout=timeout)
            await asyncio.sleep(settle)
            return settle + 0.5
        except Exception:
            continue
    raise RuntimeError(f"no selector matched: {selectors}")


async def action_hover(page, params: dict, duration: float, timing) -> float:
    """Hover an element without clicking.

    Useful for drawing attention to a destructive or irreversible control
    (a Confirm or Delete button) without actually firing it.
    """
    selector = params["selector"]
    settle = params.get("settle", 1.0)
    await page.hover(
        selector, timeout=params.get("timeout_ms", timing.selector_ms)
    )
    await asyncio.sleep(settle)
    return settle + 0.2


async def action_evaluate(page, params: dict, duration: float, timing) -> float:
    """Run arbitrary JS in the page.

    The escape hatch for anything the other verbs do not cover, without
    having to define a handler function.
    """
    settle = params.get("settle", 1.0)
    await page.evaluate(params["js"])
    await asyncio.sleep(settle)
    return settle


DEFAULT_ACTION_HANDLERS = {
    "wait": action_wait,
    "scroll": action_scroll,
    "navigate": action_navigate,
    "click": action_click,
    "hover": action_hover,
    "evaluate": action_evaluate,
}


def resolve_handlers(config) -> dict:
    """Merge built-in handlers with the project's overrides."""
    return {**DEFAULT_ACTION_HANDLERS, **(config.action_handlers or {})}


def _takes_timing(handler) -> bool:
    """Whether a handler accepts the engine's `timing` argument.

    Built-ins do. Project handlers use the documented three-argument form,
    so they are called without it. Anything accepting *args gets it too.
    """
    import inspect

    try:
        params = inspect.signature(handler).parameters
    except (TypeError, ValueError):
        return False
    if any(p.kind is inspect.Parameter.VAR_POSITIONAL for p in params.values()):
        return True
    return len(params) >= 4


async def run_scene_action(handlers: dict, page, scene, duration: float, timing) -> float:
    """Look up and run a scene's action, tolerating failure.

    A broken selector should cost one scene's choreography, not the whole
    render — the narration still plays and the picture holds.
    """
    handler = handlers.get(scene.action)
    if handler is None:
        logger.warning(
            "no handler for action %r, defaulting to wait", scene.action
        )
        handler = action_wait
    try:
        if _takes_timing(handler):
            result = await handler(page, scene.action_params, duration, timing)
        else:
            result = await handler(page, scene.action_params, duration)
        return result or 0.0
    except Exception as e:
        logger.warning("action %s failed: %s", scene.action, e)
        return 0.0
