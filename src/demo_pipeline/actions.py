"""Built-in scene action handlers, shared by both recording backends.

Scenes name actions by string. The engine resolves them against these
built-ins first, then against the project's `action_handlers` override map,
so a project can replace `click` wholesale or add its own verbs.

Each handler is async with signature `(page, params, duration) -> float`,
returning the seconds spent on the active part of the action. The engine
sleeps the remainder so narration and picture stay locked.

Prefer text-based selectors (`button:has-text('Get started')`) over class
selectors — the rendered UI is the source of truth and `:has-text` survives
most class renames.
"""

import asyncio
import logging

logger = logging.getLogger(__name__)


async def action_wait(page, params: dict, duration: float) -> float:
    """No-op — hold the current view for the scene duration."""
    return 0.0


async def action_scroll(page, params: dict, duration: float) -> float:
    """Smooth-scroll to a y offset."""
    y = params.get("y", 0)
    await page.evaluate(f"window.scrollTo({{top: {y}, behavior: 'smooth'}})")
    await asyncio.sleep(1.2)
    return 1.5


async def action_navigate(page, params: dict, duration: float) -> float:
    """Navigate to a URL, wait for load, settle."""
    url = params["url"]
    await page.goto(url, wait_until="domcontentloaded", timeout=15000)
    await asyncio.sleep(2.5)
    return 3.0


async def action_click(page, params: dict, duration: float) -> float:
    """Click an element by selector, then wait for a settle period."""
    selector = params["selector"]
    settle = params.get("settle", 2.0)
    await page.click(selector, timeout=5000)
    await asyncio.sleep(settle)
    return 0.5 + settle


async def action_hover(page, params: dict, duration: float) -> float:
    """Hover an element without clicking.

    Useful for drawing attention to a destructive or irreversible control
    (a Confirm or Delete button) without actually firing it.
    """
    selector = params["selector"]
    await page.hover(selector, timeout=5000)
    await asyncio.sleep(1.0)
    return 1.2


DEFAULT_ACTION_HANDLERS = {
    "wait": action_wait,
    "scroll": action_scroll,
    "navigate": action_navigate,
    "click": action_click,
    "hover": action_hover,
}


def resolve_handlers(config) -> dict:
    """Merge built-in handlers with the project's overrides."""
    return {**DEFAULT_ACTION_HANDLERS, **(config.action_handlers or {})}


async def run_scene_action(handlers: dict, page, scene, duration: float) -> float:
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
        return await handler(page, scene.action_params, duration) or 0.0
    except Exception as e:
        logger.warning("action %s failed: %s", scene.action, e)
        return 0.0
