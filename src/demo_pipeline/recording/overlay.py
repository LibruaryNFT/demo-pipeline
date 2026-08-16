"""In-page overlay: captions and zoom, drawn into the recording itself.

Everything here is DOM, so the recorder captures it with no compositing
stage afterwards. That is the whole reason it is done this way rather than
in ffmpeg: a caption drawn by the browser needs no font path, no escaping,
and no second pass over the video.

Two structural details do the real work, and both are easy to get wrong:

* **The overlay root hangs off `<html>`, not `<body>`, while zoom scales
  `<body>`.** Captions therefore stay at their true size while the page
  behind them zooms. Put the root inside `<body>` and the caption grows with
  the page, which looks like a bug and is one.

* **`getBoundingClientRect()` already accounts for ancestor transforms**, so
  a zoom target is measured in the coordinates it is actually rendered at.
  There is no coordinate maths anywhere in here, and there does not need to
  be.

Installed with `add_init_script`, so it survives navigation without anyone
having to notice a navigation happened.

Known limitation, worth stating because it will surprise someone: a
transform on `<body>` makes it the containing block for its
`position: fixed` descendants. A sticky header inside `<body>` scrolls with
the page while a zoom is active instead of pinning to the viewport. Zoom
back out and it pins again. If that matters for a given app, do not zoom.
"""

import json

OVERLAY_NAMESPACE = "__dp"

#: Injected once per document. Kept as a template string rather than built
#: with a formatter, because every brace in the JS would need doubling and
#: the result is unreadable. Only the two theme values are substituted.
_OVERLAY_JS = """
(() => {
  if (window.__NS__) return;
  const ACCENT = '__ACCENT__';
  const FONT = '__FONT__';
  const EASE = 'cubic-bezier(0.22, 0.61, 0.36, 1)';

  let root = null, captionEl = null, captionTimer = null;

  function ensureRoot() {
    if (root) return;
    root = document.createElement('div');
    root.id = '__NS__-overlay';
    Object.assign(root.style, {
      position: 'fixed', left: '0', top: '0', width: '100%', height: '100%',
      pointerEvents: 'none', zIndex: '2147483647', fontFamily: FONT,
      overflow: 'hidden'
    });

    captionEl = document.createElement('div');
    Object.assign(captionEl.style, {
      position: 'absolute', left: '50%', bottom: '7%',
      transform: 'translateX(-50%) translateY(12px)',
      maxWidth: '78%', padding: '14px 22px', borderRadius: '14px',
      background: 'rgba(12, 12, 14, 0.82)', backdropFilter: 'blur(6px)',
      color: '#fff', fontSize: '26px', lineHeight: '1.3', fontWeight: '600',
      textAlign: 'center', boxShadow: '0 10px 40px rgba(0,0,0,0.35)',
      opacity: '0', whiteSpace: 'pre-wrap',
      transition: 'opacity 320ms ease, transform 320ms ' + EASE
    });

    root.appendChild(captionEl);
    // documentElement, NOT body — body is what zoom scales.
    (document.documentElement || document.body).appendChild(root);
  }

  const api = {};

  api.ready = () => { ensureRoot(); return true; };

  api.caption = (text, ms) => {
    ensureRoot();
    if (captionTimer) { clearTimeout(captionTimer); captionTimer = null; }
    captionEl.textContent = text;
    captionEl.style.opacity = '1';
    captionEl.style.transform = 'translateX(-50%) translateY(0)';
    if (ms && ms > 0) captionTimer = setTimeout(api.captionHide, ms);
    return true;
  };

  api.captionHide = () => {
    if (!captionEl) return false;
    captionEl.style.opacity = '0';
    captionEl.style.transform = 'translateX(-50%) translateY(12px)';
    return true;
  };

  api.zoom = (selector, scale, ms) => {
    ensureRoot();
    const el = document.querySelector(selector);
    if (!el) return false;
    const r = el.getBoundingClientRect();
    // Origin in page coordinates, so the target stays put as body scales.
    const ox = r.left + r.width / 2 + window.scrollX;
    const oy = r.top + r.height / 2 + window.scrollY;
    const b = document.body;
    b.style.transition = 'transform ' + (ms || 700) + 'ms ' + EASE;
    b.style.transformOrigin = ox + 'px ' + oy + 'px';
    b.style.transform = 'scale(' + scale + ')';
    return true;
  };

  api.zoomReset = (ms) => {
    const b = document.body;
    if (!b) return false;
    b.style.transition = 'transform ' + (ms || 600) + 'ms ' + EASE;
    b.style.transform = 'none';
    return true;
  };

  window.__NS__ = api;
})();
"""


def build_overlay_js(accent: str = "#e91e63", font: str = "") -> str:
    """Return the init script that installs the overlay in a document."""
    default_font = (
        'system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif'
    )
    return (
        _OVERLAY_JS.replace("__NS__", OVERLAY_NAMESPACE)
        .replace("__ACCENT__", accent)
        .replace("__FONT__", font or default_font)
    )


async def install(page, config) -> None:
    """Install the overlay for every document this page loads.

    `add_init_script` runs before any page script on every navigation, which
    is why the overlay does not need the URL-change detection the rest of
    the timeline once used.
    """
    overlay = config.overlay
    if not overlay.enabled:
        return
    js = build_overlay_js(overlay.accent, overlay.font)
    await page.add_init_script(js)
    # add_init_script only affects documents loaded after it is registered,
    # so the page that is already open needs it evaluated directly.
    await page.evaluate(js)


async def _call(page, expression: str) -> bool:
    """Run one overlay call, tolerating a page that has navigated away.

    A failed caption must never take down a render. The picture is still
    correct without it; a raised exception would lose the whole video.
    """
    try:
        return bool(await page.evaluate(expression))
    except Exception:
        return False


async def caption(page, text: str, seconds: float | None = None) -> bool:
    ms = int(seconds * 1000) if seconds else 0
    return await _call(page, f"{OVERLAY_NAMESPACE}.caption({json.dumps(text)}, {ms})")


async def caption_hide(page) -> bool:
    return await _call(page, f"{OVERLAY_NAMESPACE}.captionHide()")


async def zoom(page, selector: str, scale: float, ms: int) -> bool:
    return await _call(
        page, f"{OVERLAY_NAMESPACE}.zoom({json.dumps(selector)}, {scale}, {ms})"
    )


async def zoom_reset(page, ms: int) -> bool:
    return await _call(page, f"{OVERLAY_NAMESPACE}.zoomReset({ms})")
