"""Interactive browser tools (Playwright) for Agent mode.

Registered on the same MCP server as the read-only web tools (see mcp_server.py).

How the agent "sees" a page: browser_snapshot() tags every visible interactive
element with a short ref (e1, e2, ...) and returns them as text lines, e.g.
    [e12] link "Pricing" -> https://example.com/pricing
    [e18] textbox "Email" (type=email)
    [e21] button "Sign up"
The model then acts with browser_click(ref="e21"), browser_type(ref="e18", ...).

Each agent run gets its own isolated browser context (fresh profile, no cookies),
identified by `session`, which agent.py injects; the model never sees it.

Safety
- Every request the page makes is checked: only public http(s) hosts are allowed
  (blocks localhost, private networks, cloud metadata), same rule as fetch_url.
- Downloads, service workers and permission prompts are disabled.
- Password and payment fields are never filled in.
- Risky clicks/submits (buy, pay, sign up, send, delete, ...) return
  needs_approval instead of acting; agent.py asks the user, then re-calls with
  confirmed=True.
- Sessions are capped and closed after inactivity.
"""
import asyncio
import base64
import json
import re
import time
from typing import Callable
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP
from playwright.async_api import Browser, BrowserContext, Page, async_playwright

VIEWPORT = {"width": 1280, "height": 800}
NAV_TIMEOUT_MS = 20_000
ACTION_TIMEOUT_MS = 8_000
MAX_SESSIONS = 3
IDLE_SECONDS = 600
MAX_ELEMENTS = 150
MAX_TEXT_CHARS = 6_000

RISKY_ACTION = re.compile(
    r"\b(buy|purchase|pay|checkout|check out|place order|order now|subscribe|sign ?up|register|"
    r"create account|join|submit|send|post|publish|tweet|reply|comment|delete|remove|cancel "
    r"(subscription|account|order)|confirm|transfer|donate|book|reserve|apply|upload|log ?in|"
    r"sign ?in|log ?out|sign ?out|unsubscribe|accept offer|agree)\b",
    re.I,
)
SENSITIVE_FIELD = re.compile(
    r"pass(word|code)?|pwd|card|cc-|cvc|cvv|credit|iban|routing|account.?number|ssn|social.?security|otp|2fa|pin\b",
    re.I,
)

# Marks visible interactive elements with data-as-ref and describes them.
SNAPSHOT_JS = r"""
(maxElements) => {
  const sel = [
    'a[href]', 'button', 'input:not([type=hidden])', 'textarea', 'select', 'summary',
    '[role=button]', '[role=link]', '[role=checkbox]', '[role=radio]', '[role=tab]',
    '[role=menuitem]', '[role=option]', '[role=switch]', '[role=combobox]', '[role=textbox]',
    '[contenteditable=""]', '[contenteditable=true]'
  ].join(',');
  document.querySelectorAll('[data-as-ref]').forEach(e => e.removeAttribute('data-as-ref'));
  const roleOf = (el) => {
    const r = el.getAttribute('role'); if (r) return r;
    const t = el.tagName.toLowerCase();
    if (t === 'a') return 'link';
    if (t === 'select') return 'combobox';
    if (t === 'textarea' || el.isContentEditable) return 'textbox';
    if (t === 'input') {
      return ({checkbox: 'checkbox', radio: 'radio', submit: 'button', button: 'button',
               reset: 'button', image: 'button', range: 'slider', file: 'file'})[el.type] || 'textbox';
    }
    return 'button';
  };
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const out = [];
  let n = 0;
  for (const el of document.querySelectorAll(sel)) {
    if (out.length >= maxElements) break;
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    if (r.width < 2 || r.height < 2 || st.visibility === 'hidden' || st.display === 'none' || st.opacity === '0') continue;
    const ref = 'e' + (++n);
    el.setAttribute('data-as-ref', ref);
    const tag = el.tagName.toLowerCase();
    const label = el.labels && el.labels[0] ? el.labels[0].innerText : '';
    let name = clean(el.getAttribute('aria-label') || label || (tag === 'input' ? '' : el.innerText) ||
      el.getAttribute('title') || el.getAttribute('placeholder') || el.getAttribute('alt') ||
      (tag === 'input' && ['submit', 'button'].includes(el.type) ? el.value : '') || el.getAttribute('name'));
    const item = { ref, role: roleOf(el), name: name.slice(0, 80), tag };
    if (tag === 'input' || tag === 'textarea') {
      item.type = el.type || 'text';
      item.field = [el.name, el.id, el.autocomplete, el.placeholder, label].join(' ').slice(0, 120);
      if (el.type !== 'password' && el.value) item.value = el.value.slice(0, 60);
      if (el.placeholder) item.placeholder = el.placeholder.slice(0, 60);
      if (el.type === 'checkbox' || el.type === 'radio') item.checked = el.checked;
    }
    if (tag === 'button' || (tag === 'input' && el.type === 'submit')) {
      item.submit = (el.type === 'submit') && !!el.form;
    }
    if (tag === 'a') item.href = el.href;
    if (tag === 'select') item.options = [...el.options].slice(0, 25).map(o => clean(o.text).slice(0, 40));
    if (el.disabled) item.disabled = true;
    if (r.bottom < 0 || r.top > innerHeight) item.offscreen = true;
    out.push(item);
  }
  const text = (document.body ? document.body.innerText : '').replace(/\n{3,}/g, '\n\n');
  return {
    title: document.title, url: location.href, elements: out, text,
    scroll: { y: Math.round(scrollY), height: document.documentElement.scrollHeight, viewport: innerHeight },
  };
}
"""


class _Session:
    def __init__(self, context: BrowserContext, page: Page) -> None:
        self.context = context
        self.page = page
        self.refs: dict[str, dict] = {}
        self.last_used = time.monotonic()


def register(mcp: FastMCP, check_safe: Callable[[str], str]) -> None:
    """Add the browser_* tools to `mcp`. `check_safe(url)` raises for non-public URLs."""
    state: dict = {"pw": None, "browser": None}
    sessions: dict[str, _Session] = {}
    host_ok: dict[str, bool] = {}
    lock = asyncio.Lock()

    # ------------------------------ plumbing ------------------------------ #
    async def _browser() -> Browser:
        if state["browser"] is None or not state["browser"].is_connected():
            if state["pw"] is None:
                state["pw"] = await async_playwright().start()
            state["browser"] = await state["pw"].chromium.launch(headless=True)
        return state["browser"]

    async def _is_public(url: str) -> bool:
        host = urlparse(url).hostname or ""
        if host not in host_ok:
            try:
                await asyncio.to_thread(check_safe, url)
                host_ok[host] = True
            except Exception:  # noqa: BLE001
                host_ok[host] = False
        return host_ok[host]

    async def _guard(route) -> None:
        url = route.request.url
        if url.startswith(("data:", "blob:", "about:")):
            await route.continue_()
        elif url.startswith(("http://", "https://")) and await _is_public(url):
            await route.continue_()
        else:
            await route.abort("blockedbyclient")

    async def _close(session: str) -> None:
        s = sessions.pop(session, None)
        if s:
            try:
                await s.context.close()
            except Exception:  # noqa: BLE001
                pass

    async def _reap_idle() -> None:
        now = time.monotonic()
        for sid in [k for k, s in sessions.items() if now - s.last_used > IDLE_SECONDS]:
            await _close(sid)

    async def _get(session: str, create: bool = False) -> _Session:
        async with lock:
            await _reap_idle()
            s = sessions.get(session)
            if s:
                s.last_used = time.monotonic()
                if s.page.is_closed():
                    s.page = s.context.pages[-1] if s.context.pages else await s.context.new_page()
                return s
            if not create:
                raise RuntimeError("No page is open yet. Call browser_open(url) first.")
            while len(sessions) >= MAX_SESSIONS:  # evict the oldest
                await _close(min(sessions, key=lambda k: sessions[k].last_used))
            context = await (await _browser()).new_context(
                viewport=VIEWPORT,
                accept_downloads=False,
                service_workers="block",
                java_script_enabled=True,
                locale="en-US",
            )
            context.set_default_timeout(ACTION_TIMEOUT_MS)
            context.set_default_navigation_timeout(NAV_TIMEOUT_MS)
            await context.route("**/*", _guard)
            s = _Session(context, await context.new_page())
            # Follow pages opened in a new tab.
            context.on("page", lambda p: setattr(s, "page", p))
            sessions[session] = s
            return s

    async def _settle(page: Page) -> None:
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT_MS)
            await page.wait_for_load_state("networkidle", timeout=3_000)
        except Exception:  # noqa: BLE001 - pages that never go idle are fine
            pass

    async def _screenshot(page: Page) -> str | None:
        try:
            img = await page.screenshot(type="jpeg", quality=45, scale="css", timeout=5_000)
            return base64.b64encode(img).decode()
        except Exception:  # noqa: BLE001
            return None

    def _line(e: dict) -> str:
        parts = [f'[{e["ref"]}] {e["role"]} "{e["name"]}"']
        if e.get("type") and e["role"] in ("textbox", "checkbox", "radio", "file"):
            parts.append(f'(type={e["type"]})')
        if "value" in e:
            parts.append(f'value="{e["value"]}"')
        if "checked" in e:
            parts.append("checked" if e["checked"] else "unchecked")
        if e.get("options"):
            parts.append("options=" + json.dumps(e["options"]))
        if e.get("href"):
            parts.append(f'-> {e["href"][:120]}')
        if e.get("disabled"):
            parts.append("(disabled)")
        if e.get("offscreen"):
            parts.append("(offscreen)")
        return " ".join(parts)

    async def _snapshot(s: _Session, note: str = "", with_text: bool = True) -> dict:
        await _settle(s.page)
        try:
            data = await s.page.evaluate(SNAPSHOT_JS, MAX_ELEMENTS)
        except Exception as e:  # noqa: BLE001 - e.g. navigation in progress
            await asyncio.sleep(1)
            try:
                data = await s.page.evaluate(SNAPSHOT_JS, MAX_ELEMENTS)
            except Exception:  # noqa: BLE001
                return {"error": f"Could not read the page: {e}", "url": s.page.url}
        s.refs = {e["ref"]: e for e in data["elements"]}
        out = {
            "url": data["url"],
            "title": data["title"],
            "note": note,
            "elements": [_line(e) for e in data["elements"]],
            "scroll": data["scroll"],
        }
        if with_text:
            text = data["text"]
            out["text"] = text[:MAX_TEXT_CHARS]
            out["text_truncated"] = len(text) > MAX_TEXT_CHARS
        shot = await _screenshot(s.page)
        if shot:
            out["screenshot"] = shot  # stripped by agent.py before the model sees it
        return out

    def _describe(e: dict) -> str:
        return f'{e["role"]} “{e["name"] or e["ref"]}”'

    def _err(msg: str) -> str:
        return json.dumps({"error": msg})

    def _element(s: _Session, ref: str):
        ref = ref.strip().strip("[]")
        info = s.refs.get(ref)
        if not info:
            raise LookupError(f"Unknown element ref '{ref}'. Take a fresh browser_snapshot and use a ref from it.")
        return s.page.locator(f'[data-as-ref="{ref}"]').first, info

    # ------------------------------- tools -------------------------------- #
    @mcp.tool()
    async def browser_open(url: str, session: str = "default") -> str:
        """Open a URL in a real (headless) browser so you can interact with it:
        click, type, select, scroll. Returns the page title, visible text and a
        list of interactive elements with refs (e.g. [e12]) to use with the other
        browser_* tools. Use this when the task requires doing something on a page
        or the page needs JavaScript; use fetch_url for simple reading.

        Args:
            url: The page URL (http or https)
        """
        try:
            safe = await asyncio.to_thread(check_safe, url)
            s = await _get(session, create=True)
            resp = await s.page.goto(safe, wait_until="domcontentloaded")
            status = resp.status if resp else None
            return json.dumps(await _snapshot(s, note=f"Opened (HTTP {status})" if status else "Opened"))
        except Exception as e:  # noqa: BLE001
            return _err(f"{type(e).__name__}: {e}")

    @mcp.tool()
    async def browser_snapshot(include_text: bool = True, session: str = "default") -> str:
        """Re-read the current page: URL, title, visible text and interactive elements
        with fresh refs. Refs change after the page updates, so snapshot again
        before acting if you're unsure.

        Args:
            include_text: Include the page's visible text (default true)
        """
        try:
            s = await _get(session)
            return json.dumps(await _snapshot(s, with_text=include_text))
        except Exception as e:  # noqa: BLE001
            return _err(f"{type(e).__name__}: {e}")

    @mcp.tool()
    async def browser_click(ref: str, session: str = "default", confirmed: bool = False) -> str:
        """Click an element (link, button, checkbox, tab, ...) by its ref from the
        latest snapshot. Returns the updated page. Clicks that buy, pay, sign up,
        send, post, delete or submit forms need the user's approval, which is
        requested automatically.

        Args:
            ref: Element ref from the latest snapshot, e.g. "e12"
        """
        try:
            s = await _get(session)
            loc, info = _element(s, ref)
            risky = bool(RISKY_ACTION.search(info.get("name", ""))) or info.get("submit")
            if risky and not confirmed:
                return json.dumps({
                    "needs_approval": True,
                    "action": f"Click {_describe(info)}",
                    "reason": "This could submit a form or make a change on the website.",
                    "url": s.page.url,
                })
            before = s.page.url
            await loc.scroll_into_view_if_needed()
            await loc.click()
            note = f"Clicked {_describe(info)}"
            if s.page.url != before:
                note += f" → navigated to {s.page.url}"
            return json.dumps(await _snapshot(s, note=note))
        except Exception as e:  # noqa: BLE001
            return _err(f"{type(e).__name__}: {e}")

    @mcp.tool()
    async def browser_type(
        ref: str, text: str, submit: bool = False, session: str = "default", confirmed: bool = False
    ) -> str:
        """Type text into an input or text area (replacing what's there). Set
        submit=true to press Enter afterwards (e.g. to run a search). Never used
        for passwords or payment details; those fields are refused.

        Args:
            ref: Element ref of the input, e.g. "e18"
            text: The text to enter
            submit: Press Enter after typing (default false)
        """
        try:
            s = await _get(session)
            loc, info = _element(s, ref)
            if info.get("type") == "password" or SENSITIVE_FIELD.search(
                " ".join([info.get("field", ""), info.get("name", "")])
            ):
                return _err("Refused: this looks like a password or payment field. "
                            "AskScout never enters credentials or payment details; ask the user to do it.")
            is_search = bool(re.search(r"search|query|\bq\b|find", info.get("field", "") + " " + info.get("name", ""), re.I)) \
                or info.get("type") == "search"
            if submit and not is_search and not confirmed:
                return json.dumps({
                    "needs_approval": True,
                    "action": f"Type “{text[:80]}” into {_describe(info)} and press Enter",
                    "reason": "Pressing Enter could submit a form.",
                    "url": s.page.url,
                })
            await loc.scroll_into_view_if_needed()
            await loc.fill(text)
            note = f"Typed into {_describe(info)}"
            if submit:
                await loc.press("Enter")
                note += " and pressed Enter"
            return json.dumps(await _snapshot(s, note=note))
        except Exception as e:  # noqa: BLE001
            return _err(f"{type(e).__name__}: {e}")

    @mcp.tool()
    async def browser_select(ref: str, option: str, session: str = "default") -> str:
        """Choose an option in a dropdown (<select>) by its visible label or value.

        Args:
            ref: Element ref of the dropdown
            option: The option label (or value) to pick
        """
        try:
            s = await _get(session)
            loc, info = _element(s, ref)
            try:
                await loc.select_option(label=option)
            except Exception:  # noqa: BLE001
                await loc.select_option(value=option)
            return json.dumps(await _snapshot(s, note=f"Selected “{option}” in {_describe(info)}"))
        except Exception as e:  # noqa: BLE001
            return _err(f"{type(e).__name__}: {e}")

    @mcp.tool()
    async def browser_scroll(direction: str = "down", session: str = "default") -> str:
        """Scroll the page to reveal more content.

        Args:
            direction: "down", "up", "top" or "bottom"
        """
        try:
            s = await _get(session)
            js = {
                "down": "window.scrollBy(0, window.innerHeight * 0.85)",
                "up": "window.scrollBy(0, -window.innerHeight * 0.85)",
                "top": "window.scrollTo(0, 0)",
                "bottom": "window.scrollTo(0, document.documentElement.scrollHeight)",
            }.get(direction.lower())
            if not js:
                return _err('direction must be "down", "up", "top" or "bottom"')
            await s.page.evaluate(js)
            await asyncio.sleep(0.4)
            return json.dumps(await _snapshot(s, note=f"Scrolled {direction}", with_text=False))
        except Exception as e:  # noqa: BLE001
            return _err(f"{type(e).__name__}: {e}")

    @mcp.tool()
    async def browser_back(session: str = "default") -> str:
        """Go back to the previous page in the browser history."""
        try:
            s = await _get(session)
            await s.page.go_back()
            return json.dumps(await _snapshot(s, note="Went back"))
        except Exception as e:  # noqa: BLE001
            return _err(f"{type(e).__name__}: {e}")

    @mcp.tool()
    async def browser_close(session: str = "default") -> str:
        """Close the browser session (called automatically when a run ends)."""
        await _close(session)
        return json.dumps({"closed": True})
