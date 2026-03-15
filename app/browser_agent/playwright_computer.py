"""PlaywrightComputer — Wraps Playwright for ADK ComputerUseToolset.

Implements the BaseComputer interface to control a Chromium browser
via Playwright, converting normalized coordinates from the Computer Use
model (0-999) into actual pixel positions.
"""

import asyncio
import logging
import time
from typing import Any, Callable, Coroutine, Literal, Optional

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

from google.adk.tools.computer_use.base_computer import (
    BaseComputer,
    ComputerEnvironment,
    ComputerState,
)
from ..services.screenshot_diff import ScreenshotDiffEngine

logger = logging.getLogger(__name__)

# Type for the click preview callback
PreviewCallback = Callable[
    [bytes, int, int, str, str], Coroutine[Any, Any, None]
]

# Type for the safety gate callback: (action_description, url) -> bool (True=proceed, False=cancel)
SafetyGateCallback = Callable[
    [str, str], Coroutine[Any, Any, bool]
]

# Keywords near a click target that indicate a sensitive action
_SENSITIVE_BUTTON_KEYWORDS = [
    "submit", "pay", "login", "sign in", "signin", "log in",
    "confirm", "purchase", "delete", "download", "transfer",
    "checkout", "place order", "send money", "approve", "authorize",
    "register", "sign up", "signup", "change password", "reset password",
    "withdraw", "proceed", "book now", "make payment", "complete order",
    "confirm booking", "final submit", "pay now", "buy now", "order now",
    "send otp", "verify", "continue to pay",
]

PLAYWRIGHT_KEY_MAP: dict[str, str] = {
    "backspace": "Backspace",
    "tab": "Tab",
    "enter": "Enter",
    "shift": "Shift",
    "control": "Control",
    "ctrl": "Control",
    "alt": "Alt",
    "meta": "Meta",
    "command": "Meta",
    "escape": "Escape",
    "esc": "Escape",
    "space": " ",
    "arrowup": "ArrowUp",
    "arrowdown": "ArrowDown",
    "arrowleft": "ArrowLeft",
    "arrowright": "ArrowRight",
    "pageup": "PageUp",
    "pagedown": "PageDown",
    "home": "Home",
    "end": "End",
    "insert": "Insert",
    "delete": "Delete",
    "f1": "F1",
    "f2": "F2",
    "f3": "F3",
    "f4": "F4",
    "f5": "F5",
    "f6": "F6",
    "f7": "F7",
    "f8": "F8",
    "f9": "F9",
    "f10": "F10",
    "f11": "F11",
    "f12": "F12",
}


class PlaywrightComputer(BaseComputer):
    """Playwright-based browser computer for ADK Computer Use.

    Controls a Chromium browser via Playwright, accepting normalized
    coordinates (0-999) from the Gemini Computer Use model and
    denormalizing them to actual screen pixels.
    """

    def __init__(
        self,
        screen_size: tuple[int, int] = (1440, 900),
        headless: bool = True,
        initial_url: str = "https://www.google.com",
    ) -> None:
        self._screen_width = screen_size[0]
        self._screen_height = screen_size[1]
        self._headless = headless
        self._initial_url = initial_url

        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._action_log: list[dict] = []

        # Click preview callback
        self._preview_callback: Optional[PreviewCallback] = None

        # Safety gate callback — pauses for user confirmation on sensitive actions
        self._safety_gate_callback: Optional[SafetyGateCallback] = None

        # Screenshot diff engine
        self._diff_engine = ScreenshotDiffEngine()

        # Track navigation failures per domain to auto-abort repeated attempts
        self._nav_failures: dict[str, int] = {}  # domain -> consecutive failure count
        self._MAX_NAV_FAILURES = 2  # block domain after this many failures
        self._google_captcha_active = False  # Set when Google CAPTCHA detected

    def denormalize_x(self, x: int) -> int:
        """Convert normalized x coordinate (0-999) to actual pixel position."""
        return int(x / 1000 * self._screen_width)

    def denormalize_y(self, y: int) -> int:
        """Convert normalized y coordinate (0-999) to actual pixel position."""
        return int(y / 1000 * self._screen_height)

    def _log_action(self, action: str, details: dict | None = None) -> None:
        """Log an action with timestamp for the journal."""
        entry = {
            "timestamp": time.time(),
            "action": action,
            "details": details or {},
            "url": self._page.url if self._page else None,
        }
        self._action_log.append(entry)
        logger.info("Action: %s | Details: %s", action, details)

    def get_action_log(self) -> list[dict]:
        """Return the full action log."""
        return list(self._action_log)

    def get_current_url(self) -> str:
        """Return the current page URL."""
        if self._page:
            return self._page.url
        return ""

    def set_preview_callback(self, callback: Optional[PreviewCallback]) -> None:
        """Set callback for visual click/action previews.

        Args:
            callback: async fn(screenshot_bytes, x, y, action_type, description)
        """
        self._preview_callback = callback

    def set_safety_gate_callback(self, callback: Optional[SafetyGateCallback]) -> None:
        """Set callback for safety gate confirmation.

        Args:
            callback: async fn(action_description, url) -> bool
                      Returns True to proceed, False to cancel.
        """
        self._safety_gate_callback = callback

    async def _get_element_text_near(self, x: int, y: int, radius: int = 60) -> str:
        """Get text content of elements near the given pixel coordinates.

        Uses JavaScript to find the element at (x, y) and returns its
        text content plus any nearby button/link/input text.
        """
        if not self._page:
            return ""
        try:
            text = await self._page.evaluate(f"""() => {{
                const el = document.elementFromPoint({x}, {y});
                if (!el) return '';
                // Collect text from the element and its ancestors
                let texts = [];
                let node = el;
                for (let i = 0; i < 5 && node; i++) {{
                    const t = (node.textContent || '').trim().substring(0, 100);
                    if (t) texts.push(t);
                    // Check value/placeholder for inputs
                    if (node.value) texts.push(node.value);
                    if (node.placeholder) texts.push(node.placeholder);
                    if (node.getAttribute && node.getAttribute('aria-label'))
                        texts.push(node.getAttribute('aria-label'));
                    if (node.title) texts.push(node.title);
                    // Check type attribute for submit buttons
                    if (node.type === 'submit') texts.push('submit');
                    node = node.parentElement;
                }}
                return texts.join(' | ').substring(0, 500);
            }}""")
            return text or ""
        except Exception as e:
            logger.debug("Could not get element text near (%d,%d): %s", x, y, e)
            return ""

    async def _check_safety_gate(self, x: int, y: int, action_type: str, action_desc: str) -> bool:
        """Check if an action requires safety gate confirmation.

        Returns True if action should proceed, False if cancelled by user.
        """
        if not self._safety_gate_callback:
            return True  # No callback set, proceed

        url = self.get_current_url()

        # Get text near the click target to determine if it's sensitive
        element_text = await self._get_element_text_near(x, y)
        element_text_lower = element_text.lower()

        # Check if any sensitive keyword is found near the target
        is_sensitive = False
        matched_keyword = ""
        for keyword in _SENSITIVE_BUTTON_KEYWORDS:
            if keyword in element_text_lower:
                is_sensitive = True
                matched_keyword = keyword
                break

        if not is_sensitive:
            return True  # Not sensitive, proceed

        # Build a human-readable description
        # Extract the most relevant short text from the element
        short_text = element_text.split("|")[0].strip()[:60]
        if short_text:
            description = f'{action_desc} — button says: "{short_text}"'
        else:
            description = f"{action_desc} (detected: {matched_keyword})"

        logger.info(
            "Safety gate triggered: %s (keyword: '%s', url: %s)",
            description, matched_keyword, url,
        )

        # Call the safety gate callback — this will pause and wait for user response
        proceed = await self._safety_gate_callback(description, url)

        if proceed:
            logger.info("Safety gate: user APPROVED action")
        else:
            logger.info("Safety gate: user DENIED action")

        return proceed

    @property
    def diff_engine(self) -> ScreenshotDiffEngine:
        """Access the screenshot diff engine."""
        return self._diff_engine

    def reset_task_state(self) -> None:
        """Reset per-task state (nav failures, diff baseline, captcha) for a fresh task."""
        self._nav_failures.clear()
        self._diff_engine.reset()
        self._google_captcha_active = False

    async def _send_preview(
        self, x: int, y: int, action_type: str, description: str
    ) -> None:
        """Send a visual preview of an action to the frontend.
        Lightweight — sends only coordinates, no extra screenshot."""
        if self._preview_callback and self._page:
            try:
                await self._preview_callback(
                    b"", x, y, action_type, description
                )
            except Exception as e:
                logger.debug("Preview callback error: %s", e)

    async def _do_initialize(self) -> None:
        """Launch Playwright Chromium browser with full stealth flags."""
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self._headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-infobars",
                "--disable-background-timer-throttling",
                "--disable-popup-blocking",
                "--disable-component-update",
                "--disable-dev-shm-usage",
                "--disable-ipc-flooding-protection",
                "--enable-features=NetworkService,NetworkServiceInProcess",
                "--window-size=1440,900",
            ],
        )
        self._context = await self._browser.new_context(
            viewport={"width": self._screen_width, "height": self._screen_height},
            screen={"width": self._screen_width, "height": self._screen_height},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.6778.85 Safari/537.36"
            ),
            locale="en-IN",
            timezone_id="Asia/Kolkata",
            color_scheme="light",
            java_script_enabled=True,
            has_touch=False,
            is_mobile=False,
            extra_http_headers={
                "Accept-Language": "en-IN,en-US;q=0.9,en;q=0.8,hi;q=0.7",
                "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24", "Google Chrome";v="131"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Linux"',
            },
        )

        # Full stealth: spoof navigator props to defeat bot detection
        await self._context.add_init_script("""
            // Remove webdriver flag
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

            // Fake plugins array (real Chrome has 5 default plugins)
            Object.defineProperty(navigator, 'plugins', {
                get: () => {
                    const plugins = [
                        { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
                        { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
                        { name: 'Native Client', filename: 'internal-nacl-plugin' },
                        { name: 'Chromium PDF Plugin', filename: 'internal-pdf-viewer' },
                        { name: 'Chromium PDF Viewer', filename: 'internal-pdf-viewer' },
                    ];
                    plugins.length = 5;
                    return plugins;
                },
            });

            // Languages
            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-IN', 'en-US', 'en', 'hi'],
            });

            // Hardware concurrency (real machine value)
            Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });

            // Device memory
            Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });

            // Platform
            Object.defineProperty(navigator, 'platform', { get: () => 'Linux x86_64' });

            // Chrome runtime object
            window.chrome = {
                runtime: {
                    onMessage: { addListener: () => {}, removeListener: () => {} },
                    sendMessage: () => {},
                    connect: () => ({ onMessage: { addListener: () => {} } }),
                },
                loadTimes: () => ({}),
                csi: () => ({}),
            };

            // Permissions API
            const originalQuery = window.navigator.permissions?.query;
            if (originalQuery) {
                window.navigator.permissions.query = (parameters) => (
                    parameters.name === 'notifications' ?
                        Promise.resolve({ state: Notification.permission }) :
                        originalQuery(parameters)
                );
            }

            // WebGL vendor/renderer (match real GPU)
            const getParameter = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(param) {
                if (param === 37445) return 'Google Inc. (Intel)';
                if (param === 37446) return 'ANGLE (Intel, Mesa Intel(R) Graphics, OpenGL 4.6)';
                return getParameter.call(this, param);
            };
        """)

        self._page = await self._context.new_page()

        # Add Google CAPTCHA auto-detection — if we land on /sorry, inject banner
        self._page.on("load", lambda _: asyncio.ensure_future(self._check_google_captcha()))

        await self._page.goto(self._initial_url, wait_until="domcontentloaded")
        self._log_action("initialize", {"url": self._initial_url})
        logger.info(
            "PlaywrightComputer initialized: %dx%d headless=%s",
            self._screen_width,
            self._screen_height,
            self._headless,
        )

    async def _check_google_captcha(self) -> None:
        """Detect Google CAPTCHA/sorry page and inject visible error banner."""
        try:
            if not self._page:
                return
            url = self._page.url
            if "google.com/sorry" in url or "google.com/recaptcha" in url:
                logger.warning("Google CAPTCHA detected at %s", url)
                await self._page.evaluate("""() => {
                    const banner = document.createElement('div');
                    banner.id = 'sahay-captcha-banner';
                    banner.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:99999;background:#d32f2f;color:white;padding:20px;text-align:center;font-size:24px;font-weight:bold;';
                    banner.innerHTML = '<div>GOOGLE CAPTCHA DETECTED</div><div style="font-size:16px;margin-top:8px;">Google is blocking automated searches. Navigate DIRECTLY to the target website instead of searching Google.</div><div style="font-size:14px;margin-top:4px;">Report TASK FAILED or try navigating directly to the website URL.</div>';
                    document.body.prepend(banner);
                }""")
        except Exception as e:
            logger.debug("CAPTCHA check error: %s", e)

    async def close(self) -> None:
        """Shut down the browser and Playwright."""
        try:
            if self._context:
                await self._context.close()
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception as e:
            logger.warning("Error during PlaywrightComputer cleanup: %s", e)
        finally:
            self._page = None
            self._context = None
            self._browser = None
            self._playwright = None
            logger.info("PlaywrightComputer closed")

    async def screen_size(self) -> tuple[int, int]:
        """Return the configured viewport dimensions."""
        return (self._screen_width, self._screen_height)

    async def environment(self) -> ComputerEnvironment:
        """Return browser environment type."""
        return ComputerEnvironment.ENVIRONMENT_BROWSER

    async def _take_screenshot(self) -> bytes:
        """Capture a PNG screenshot of the current page."""
        if not self._page:
            raise RuntimeError("Browser not initialized")
        return await self._page.screenshot(type="jpeg", quality=75, full_page=False)

    async def current_state(self) -> ComputerState:
        """Return current screenshot and URL."""
        url = self._page.url if self._page else None

        # Detect Google CAPTCHA on ANY page load (not just navigate())
        if url and ("google.com/sorry" in url or "google.com/recaptcha" in url):
            if not self._google_captcha_active:
                self._google_captcha_active = True
                logger.warning("Google CAPTCHA detected in current_state — activating block")
            # Replace the sorry page with a clear error so model doesn't loop
            try:
                await self._page.goto("about:blank", wait_until="commit", timeout=5000)
                await self._page.evaluate("""() => {
                    document.body.innerHTML = '<div style="background:#d32f2f;color:white;padding:40px;font-family:sans-serif;text-align:center;min-height:100vh;display:flex;align-items:center;justify-content:center;flex-direction:column;">'
                        + '<h1 style="font-size:36px;">GOOGLE SEARCH BLOCKED</h1>'
                        + '<p style="font-size:20px;">Google is blocking automated searches. Navigate DIRECTLY to the target website.</p>'
                        + '<p style="font-size:16px;color:#ffcdd2;margin-top:10px;">Common URLs: amazon.in | flipkart.com | en.wikipedia.org | youtube.com</p>'
                        + '<p style="font-size:16px;color:#ef9a9a;">Or report TASK FAILED if you cannot reach the site.</p>'
                        + '</div>';
                }""")
                url = "about:blank"
            except Exception:
                pass

        screenshot = await self._take_screenshot()
        return ComputerState(screenshot=screenshot, url=url)

    async def open_web_browser(self) -> ComputerState:
        """Confirm the web browser is ready (no-op if already initialized)."""
        if not self._page:
            await self.initialize()
        self._log_action("open_web_browser")
        return await self.current_state()

    async def initialize(self) -> None:
        """Launch Playwright Chromium browser (skip if already running)."""
        if self._page is not None:
            logger.debug("Browser already initialized, skipping")
            return
        await self._do_initialize()

    def _get_domain(self, url: str) -> str:
        """Extract domain from a URL for failure tracking."""
        try:
            from urllib.parse import urlparse
            return urlparse(url).netloc.lower()
        except Exception:
            return url

    # Domains with complex SPAs — no longer redirected to Google (Google is blocked).
    # Instead, the agent navigates directly and uses DOM tools to interact.
    _REDIRECT_TO_GOOGLE_DOMAINS: dict[str, str] = {}  # Empty — direct navigation only

    async def navigate(self, url: str) -> ComputerState:
        """Navigate to a specific URL, handling network errors gracefully.

        If navigation fails, injects a visible error banner into the page
        so the Computer Use model can SEE that navigation failed and
        decide to try a different approach instead of retrying endlessly.

        Tracks failures per domain — after 2 consecutive failures to the
        same domain, immediately blocks further attempts and shows a
        permanent error banner instead of wasting network requests.

        Complex SPAs (like IRCTC) are redirected to Google Search instead,
        since the Computer Use model can't handle their autocomplete forms.
        """
        if not self._page:
            raise RuntimeError("Browser not initialized")

        domain = self._get_domain(url)
        self._log_action("navigate", {"url": url})

        # If Google CAPTCHA is active, block ALL Google navigation
        if getattr(self, '_google_captcha_active', False) and "google.com" in url:
            logger.warning("Blocking Google navigation — CAPTCHA is active")
            try:
                await self._page.goto("about:blank", wait_until="commit", timeout=5000)
                await self._page.evaluate("""() => {
                    document.body.innerHTML = '<div style="background:#d32f2f;color:white;padding:40px;font-family:sans-serif;text-align:center;min-height:100vh;display:flex;align-items:center;justify-content:center;flex-direction:column;">'
                        + '<h1 style="font-size:36px;">GOOGLE IS BLOCKED</h1>'
                        + '<p style="font-size:20px;">Do NOT use Google. Navigate directly to the target website URL.</p>'
                        + '<p style="font-size:16px;color:#ffcdd2;margin-top:10px;">Amazon: amazon.in | Flipkart: flipkart.com | Wikipedia: en.wikipedia.org</p>'
                        + '<p style="font-size:16px;color:#ef9a9a;margin-top:5px;">If you cannot reach the site directly, report TASK FAILED.</p>'
                        + '</div>';
                }""")
            except Exception:
                pass
            return await self.current_state()

        # Redirect complex SPA sites to Google Search
        if domain in self._REDIRECT_TO_GOOGLE_DOMAINS and "google" not in url:
            site_name = self._REDIRECT_TO_GOOGLE_DOMAINS[domain]
            logger.info(
                "Redirecting %s to Google Search (complex SPA)", domain
            )
            import urllib.parse
            search_query = urllib.parse.quote(f"{site_name} {url}")
            google_url = f"https://www.google.com/search?q={search_query}"
            try:
                await self._page.goto(google_url, wait_until="domcontentloaded", timeout=10000)
            except Exception:
                pass
            await asyncio.sleep(0.2)
            # Show banner explaining the redirect
            try:
                await self._page.evaluate(f"""() => {{
                    const banner = document.createElement('div');
                    banner.id = 'sahay-redirect-info';
                    banner.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:99999;'
                        + 'background:#2563eb;color:white;padding:12px;font-size:16px;'
                        + 'font-family:sans-serif;text-align:center;';
                    banner.innerHTML = '<strong>REDIRECTED TO GOOGLE</strong> — {site_name} has a complex form. '
                        + 'Search results below have the information you need. Read them with get_page_text.';
                    const old = document.getElementById('sahay-redirect-info');
                    if (old) old.remove();
                    document.body.prepend(banner);
                }}""")
            except Exception:
                pass
            return await self.current_state()

        # Check if this domain is already known to be blocked
        if self._nav_failures.get(domain, 0) >= self._MAX_NAV_FAILURES:
            logger.warning(
                "Navigation BLOCKED for %s (failed %d times already). Skipping.",
                domain, self._nav_failures[domain],
            )
            # Show a strong error banner without even trying
            try:
                safe_url = url.replace("'", "\\'").replace('"', '\\"')
                await self._page.evaluate(f"""() => {{
                    const banner = document.createElement('div');
                    banner.id = 'sahay-nav-error';
                    banner.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:99999;'
                        + 'background:#dc2626;color:white;padding:20px;font-size:20px;'
                        + 'font-family:sans-serif;text-align:center;box-shadow:0 4px 12px rgba(0,0,0,0.3)';
                    banner.innerHTML = '<strong>WEBSITE BLOCKED</strong><br>'
                        + 'The website {safe_url} has been tried multiple times and is NOT reachable.<br>'
                        + '<strong>This site blocks automated browsers. Do NOT retry.</strong><br>'
                        + '<em>Report TASK FAILED and suggest the user try manually.</em>';
                    const old = document.getElementById('sahay-nav-error');
                    if (old) old.remove();
                    document.body.prepend(banner);
                }}""")
            except Exception:
                pass
            return await self.current_state()

        nav_failed = False
        error_msg = ""
        try:
            await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
            # Success — reset failure count for this domain
            self._nav_failures[domain] = 0
        except Exception as e:
            logger.warning("Navigation failed for %s: %s — retrying with commit", url, e)
            error_msg = str(e)
            try:
                await self._page.goto(url, wait_until="commit", timeout=30000)
                # Partial success — reset
                self._nav_failures[domain] = 0
            except Exception as e2:
                logger.warning("Navigation retry also failed: %s", e2)
                nav_failed = True
                error_msg = str(e2)
                # Increment failure counter for this domain
                self._nav_failures[domain] = self._nav_failures.get(domain, 0) + 1
                logger.warning(
                    "Domain %s failure count: %d/%d",
                    domain, self._nav_failures[domain], self._MAX_NAV_FAILURES,
                )

        await asyncio.sleep(0.2)

        # Check if we landed on Google CAPTCHA (sorry page) — this means
        # Google is blocking us. Treat as a hard navigation failure.
        if self._page and ("google.com/sorry" in self._page.url or "google.com/recaptcha" in self._page.url):
            self._google_captcha_active = True
            logger.warning("Google CAPTCHA active — navigating to about:blank and injecting error")
            try:
                await self._page.goto("about:blank", wait_until="commit", timeout=5000)
            except Exception:
                pass
            try:
                await self._page.evaluate("""() => {
                    document.body.innerHTML = '<div style="background:#d32f2f;color:white;padding:40px;font-family:sans-serif;text-align:center;min-height:100vh;display:flex;align-items:center;justify-content:center;flex-direction:column;">'
                        + '<h1 style="font-size:36px;margin-bottom:20px;">GOOGLE SEARCH BLOCKED</h1>'
                        + '<p style="font-size:20px;max-width:600px;">Google is temporarily blocking searches from this browser due to too many automated requests.</p>'
                        + '<p style="font-size:18px;margin-top:20px;color:#ffcdd2;">Navigate DIRECTLY to the target website URL instead of using Google Search.</p>'
                        + '<p style="font-size:16px;margin-top:10px;color:#ef9a9a;">If you don\\'t know the URL, report TASK FAILED.</p>'
                        + '</div>';
                }""")
            except Exception:
                pass
            return await self.current_state()

        # If navigation failed, inject a visible error banner
        if nav_failed and self._page:
            failures = self._nav_failures.get(domain, 0)
            safe_url = url.replace("'", "\\'").replace('"', '\\"')
            short_error = error_msg[:120].replace("'", "\\'").replace('"', '\\"')
            blocked_msg = (
                "<strong>This site blocks automated browsers. Do NOT retry. Report TASK FAILED.</strong>"
                if failures >= self._MAX_NAV_FAILURES
                else f"<em>Failure {failures}/{self._MAX_NAV_FAILURES}. Try a completely different approach or report TASK FAILED.</em>"
            )
            try:
                await self._page.evaluate(f"""() => {{
                    const banner = document.createElement('div');
                    banner.id = 'sahay-nav-error';
                    banner.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:99999;'
                        + 'background:#dc2626;color:white;padding:16px 20px;font-size:18px;'
                        + 'font-family:sans-serif;text-align:center;box-shadow:0 4px 12px rgba(0,0,0,0.3)';
                    banner.innerHTML = '<strong>NAVIGATION FAILED</strong><br>'
                        + 'Could not reach: {safe_url}<br>'
                        + '<small>{short_error}</small><br>'
                        + '{blocked_msg}';
                    const old = document.getElementById('sahay-nav-error');
                    if (old) old.remove();
                    document.body.prepend(banner);
                }}""")
            except Exception:
                pass

        return await self.current_state()

    async def click_at(self, x: int, y: int) -> ComputerState:
        """Click at the given coordinates with visual preview and safety gate."""
        if not self._page:
            raise RuntimeError("Browser not initialized")
        self._log_action("click_at", {"x": x, "y": y})

        # Send visual preview BEFORE clicking
        await self._send_preview(x, y, "click", f"Clicking at ({x}, {y})")

        # Safety gate check — pause for user confirmation on sensitive buttons
        proceed = await self._check_safety_gate(
            x, y, "click", f"Click at ({x}, {y})"
        )
        if not proceed:
            logger.info("Click CANCELLED by safety gate at (%d, %d)", x, y)
            return await self.current_state()

        await self._page.mouse.click(x, y)
        await asyncio.sleep(0.2)
        try:
            await self._page.wait_for_load_state("domcontentloaded", timeout=2000)
        except Exception:
            pass
        return await self.current_state()

    async def hover_at(self, x: int, y: int) -> ComputerState:
        """Hover the mouse at the given coordinates."""
        if not self._page:
            raise RuntimeError("Browser not initialized")
        self._log_action("hover_at", {"x": x, "y": y})
        await self._page.mouse.move(x, y)
        await asyncio.sleep(0.2)
        return await self.current_state()

    async def type_text_at(
        self,
        x: int,
        y: int,
        text: str,
        press_enter: bool = True,
        clear_before_typing: bool = True,
    ) -> ComputerState:
        """Click at coordinates, optionally clear the field, and type text."""
        if not self._page:
            raise RuntimeError("Browser not initialized")
        self._log_action(
            "type_text_at",
            {"x": x, "y": y, "text": text, "press_enter": press_enter},
        )

        # Send visual preview BEFORE typing
        preview_text = text[:20] + "..." if len(text) > 20 else text
        await self._send_preview(x, y, "type", f"Typing '{preview_text}'")

        await self._page.mouse.click(x, y)
        await asyncio.sleep(0.2)

        if clear_before_typing:
            await self._page.keyboard.press("Control+a")
            await self._page.keyboard.press("Backspace")
            await asyncio.sleep(0.2)

        await self._page.keyboard.type(text, delay=50)

        # Wait briefly for autocomplete dropdowns to appear
        await asyncio.sleep(0.8)

        # Check if an autocomplete/dropdown appeared — if so, click the first match
        # instead of pressing Enter (which might not select from the dropdown)
        autocomplete_clicked = False
        try:
            search_text = text.lower()[:15].replace("'", "").replace('"', '')
            dropdown_item = await self._page.evaluate(
                """(searchText) => {
                const selectors = [
                    'ul.ui-autocomplete li',
                    '.autocomplete-suggestions div',
                    '[role="listbox"] [role="option"]',
                    '.dropdown-menu li',
                    '.suggestions li',
                    '.pac-container .pac-item',
                    '[class*="autocomplete"] li',
                    '[class*="suggest"] li',
                    '[class*="dropdown"] li:not(.disabled)',
                    'datalist option',
                    '.ng-option',
                    'mat-option',
                ];
                for (const sel of selectors) {
                    const items = document.querySelectorAll(sel);
                    for (const item of items) {
                        if (item.offsetHeight > 0 && item.textContent.toLowerCase().includes(searchText)) {
                            item.click();
                            return {
                                text: item.textContent.trim().substring(0, 80),
                                clicked: true
                            };
                        }
                    }
                    if (items.length > 0 && items[0].offsetHeight > 0) {
                        items[0].click();
                        return {
                            text: items[0].textContent.trim().substring(0, 80),
                            clicked: true
                        };
                    }
                }
                return { found: false };
                }""",
                search_text,
            )
            if dropdown_item and dropdown_item.get("clicked"):
                autocomplete_clicked = True
                logger.info("Auto-clicked dropdown item: %s", dropdown_item.get("text", ""))
                await asyncio.sleep(0.2)
        except Exception as e:
            logger.debug("Autocomplete check failed: %s", e)

        if press_enter and not autocomplete_clicked:
            await self._page.keyboard.press("Enter")

        await asyncio.sleep(0.2)
        try:
            await self._page.wait_for_load_state("domcontentloaded", timeout=2000)
        except Exception:
            pass
        return await self.current_state()

    async def scroll_document(
        self, direction: Literal["up", "down", "left", "right"]
    ) -> ComputerState:
        """Scroll the entire page in the given direction."""
        if not self._page:
            raise RuntimeError("Browser not initialized")
        self._log_action("scroll_document", {"direction": direction})

        if direction == "up":
            await self._page.keyboard.press("PageUp")
        elif direction == "down":
            await self._page.keyboard.press("PageDown")
        elif direction == "left":
            await self._page.evaluate(
                f"window.scrollBy(-{self._screen_width // 2}, 0)"
            )
        elif direction == "right":
            await self._page.evaluate(
                f"window.scrollBy({self._screen_width // 2}, 0)"
            )

        await asyncio.sleep(0.2)
        return await self.current_state()

    async def scroll_at(
        self,
        x: int,
        y: int,
        direction: Literal["up", "down", "left", "right"],
        magnitude: int,
    ) -> ComputerState:
        """Scroll at specific coordinates."""
        if not self._page:
            raise RuntimeError("Browser not initialized")
        self._log_action(
            "scroll_at",
            {"x": x, "y": y, "direction": direction, "magnitude": magnitude},
        )
        await self._page.mouse.move(x, y)

        delta_x, delta_y = 0, 0
        scroll_amount = magnitude * 100
        if direction == "up":
            delta_y = -scroll_amount
        elif direction == "down":
            delta_y = scroll_amount
        elif direction == "left":
            delta_x = -scroll_amount
        elif direction == "right":
            delta_x = scroll_amount

        await self._page.mouse.wheel(delta_x, delta_y)
        await asyncio.sleep(0.2)
        return await self.current_state()

    async def wait(self, seconds: int) -> ComputerState:
        """Wait for the specified number of seconds."""
        self._log_action("wait", {"seconds": seconds})
        await asyncio.sleep(seconds)
        return await self.current_state()

    async def go_back(self) -> ComputerState:
        """Navigate back in browser history."""
        if not self._page:
            raise RuntimeError("Browser not initialized")
        self._log_action("go_back")
        await self._page.go_back(wait_until="domcontentloaded", timeout=10000)
        await asyncio.sleep(0.2)
        return await self.current_state()

    async def go_forward(self) -> ComputerState:
        """Navigate forward in browser history."""
        if not self._page:
            raise RuntimeError("Browser not initialized")
        self._log_action("go_forward")
        await self._page.go_forward(wait_until="domcontentloaded", timeout=10000)
        await asyncio.sleep(0.2)
        return await self.current_state()

    async def search(self) -> ComputerState:
        """Navigate to Google search."""
        return await self.navigate("https://www.google.com")

    async def key_combination(self, keys: list[str]) -> ComputerState:
        """Press a keyboard key combination."""
        if not self._page:
            raise RuntimeError("Browser not initialized")
        self._log_action("key_combination", {"keys": keys})

        mapped_keys = []
        for key in keys:
            mapped = PLAYWRIGHT_KEY_MAP.get(key.lower(), key)
            mapped_keys.append(mapped)

        combo = "+".join(mapped_keys)
        await self._page.keyboard.press(combo)
        await asyncio.sleep(0.2)
        return await self.current_state()

    async def get_dom_snapshot(self, max_length: int = 15000) -> str:
        """Extract a structured DOM snapshot of the visible page.

        Returns a compact representation with interactive elements,
        their roles, text, and bounding boxes for precise targeting.
        """
        if not self._page:
            raise RuntimeError("Browser not initialized")

        snapshot = await self._page.evaluate("""() => {
            const result = { title: document.title, url: location.href, elements: [] };
            const seen = new Set();

            // Selectors for interactive + informational elements
            const selectors = [
                'a[href]', 'button', 'input', 'select', 'textarea',
                'h1', 'h2', 'h3', 'h4',
                '[role="button"]', '[role="link"]', '[role="tab"]',
                '[role="menuitem"]', '[role="option"]', '[role="search"]',
                '[onclick]', 'label', 'nav', 'form',
                '[aria-label]', '[placeholder]',
            ];

            for (const sel of selectors) {
                for (const el of document.querySelectorAll(sel)) {
                    if (seen.has(el)) continue;
                    seen.add(el);

                    const rect = el.getBoundingClientRect();
                    // Skip invisible elements
                    if (rect.width === 0 || rect.height === 0) continue;
                    if (rect.bottom < 0 || rect.top > window.innerHeight) continue;
                    if (rect.right < 0 || rect.left > window.innerWidth) continue;

                    const tag = el.tagName.toLowerCase();
                    const entry = { tag };

                    // Position (center of element)
                    entry.x = Math.round(rect.left + rect.width / 2);
                    entry.y = Math.round(rect.top + rect.height / 2);

                    // Core attributes
                    if (el.id) entry.id = el.id;
                    if (el.type) entry.type = el.type;
                    if (el.name) entry.name = el.name;
                    if (el.value && el.value.length < 100) entry.value = el.value;
                    if (el.placeholder) entry.placeholder = el.placeholder;
                    if (el.href) entry.href = el.href.substring(0, 120);
                    const role = el.getAttribute('role');
                    if (role) entry.role = role;
                    const ariaLabel = el.getAttribute('aria-label');
                    if (ariaLabel) entry.ariaLabel = ariaLabel;
                    if (el.disabled) entry.disabled = true;
                    if (el.checked) entry.checked = true;
                    if (el.readOnly) entry.readOnly = true;
                    if (el.required) entry.required = true;

                    // Visible text (trimmed)
                    const text = (el.innerText || el.textContent || '').trim();
                    if (text && text.length < 200) entry.text = text.substring(0, 150);

                    // Selected option for <select>
                    if (tag === 'select' && el.selectedIndex >= 0) {
                        const opt = el.options[el.selectedIndex];
                        if (opt) entry.selected = opt.text;
                        entry.options = Array.from(el.options)
                            .slice(0, 10)
                            .map(o => o.text);
                    }

                    result.elements.push(entry);
                }
            }

            // Also grab visible text blocks for reading page content
            const textBlocks = [];
            const walker = document.createTreeWalker(
                document.body, NodeFilter.SHOW_TEXT,
                { acceptNode: n => {
                    const p = n.parentElement;
                    if (!p) return NodeFilter.FILTER_REJECT;
                    const s = getComputedStyle(p);
                    if (s.display === 'none' || s.visibility === 'hidden') return NodeFilter.FILTER_REJECT;
                    const t = n.textContent.trim();
                    if (t.length < 3 || t.length > 500) return NodeFilter.FILTER_REJECT;
                    return NodeFilter.FILTER_ACCEPT;
                }}
            );
            let node, totalLen = 0;
            while ((node = walker.nextNode()) && totalLen < 5000) {
                const t = node.textContent.trim();
                if (t.length >= 10) {
                    textBlocks.push(t);
                    totalLen += t.length;
                }
            }
            result.pageText = textBlocks.join(' | ');

            return result;
        }""")

        # Format as compact text
        lines = [f"PAGE: {snapshot.get('title', '')} — {snapshot.get('url', '')}"]

        elements = snapshot.get('elements', [])
        if elements:
            lines.append(f"\nINTERACTIVE ELEMENTS ({len(elements)}):")
            for el in elements:
                parts = [f"<{el['tag']}>"]
                if el.get('type'):
                    parts.append(f"type={el['type']}")
                if el.get('id'):
                    parts.append(f"id={el['id']}")
                if el.get('name'):
                    parts.append(f"name={el['name']}")
                if el.get('role'):
                    parts.append(f"role={el['role']}")
                if el.get('ariaLabel'):
                    parts.append(f'aria="{el["ariaLabel"]}"')
                if el.get('placeholder'):
                    parts.append(f'placeholder="{el["placeholder"]}"')
                if el.get('text'):
                    parts.append(f'"{el["text"]}"')
                if el.get('href'):
                    parts.append(f"→ {el['href']}")
                if el.get('value'):
                    parts.append(f"val={el['value']}")
                if el.get('disabled'):
                    parts.append("[disabled]")
                if el.get('required'):
                    parts.append("[required]")
                if el.get('selected'):
                    parts.append(f"selected={el['selected']}")
                if el.get('options'):
                    parts.append(f"opts=[{', '.join(el['options'][:5])}]")
                parts.append(f"@({el['x']},{el['y']})")
                lines.append("  " + " ".join(parts))

        page_text = snapshot.get('pageText', '')
        if page_text:
            lines.append(f"\nPAGE TEXT:\n{page_text[:3000]}")

        result = "\n".join(lines)
        return result[:max_length]

    async def get_form_fields(self) -> str:
        """Extract all form fields with labels for accurate form filling."""
        if not self._page:
            raise RuntimeError("Browser not initialized")

        fields = await self._page.evaluate("""() => {
            const result = [];
            const inputs = document.querySelectorAll('input, select, textarea');
            for (const el of inputs) {
                const rect = el.getBoundingClientRect();
                if (rect.width === 0 || rect.height === 0) continue;

                const field = {
                    tag: el.tagName.toLowerCase(),
                    x: Math.round(rect.left + rect.width / 2),
                    y: Math.round(rect.top + rect.height / 2),
                };

                if (el.type) field.type = el.type;
                if (el.name) field.name = el.name;
                if (el.id) field.id = el.id;
                if (el.placeholder) field.placeholder = el.placeholder;
                if (el.value) field.value = el.value.substring(0, 80);
                if (el.required) field.required = true;
                if (el.readOnly) field.readOnly = true;

                // Find label
                let label = '';
                if (el.id) {
                    const lbl = document.querySelector('label[for="' + el.id + '"]');
                    if (lbl) label = lbl.textContent.trim();
                }
                if (!label && el.closest('label')) {
                    label = el.closest('label').textContent.trim();
                }
                if (!label && el.getAttribute('aria-label')) {
                    label = el.getAttribute('aria-label');
                }
                if (label) field.label = label.substring(0, 100);

                // Options for select
                if (el.tagName === 'SELECT') {
                    field.options = Array.from(el.options).slice(0, 15).map(o => o.text);
                    if (el.selectedIndex >= 0) {
                        field.selected = el.options[el.selectedIndex].text;
                    }
                }

                result.push(field);
            }
            return result;
        }""")

        if not fields:
            return "No form fields found on this page."

        lines = ["FORM FIELDS:"]
        for f in fields:
            parts = [f"<{f['tag']}>"]
            if f.get('type'):
                parts.append(f"type={f['type']}")
            if f.get('label'):
                parts.append(f'label="{f["label"]}"')
            if f.get('name'):
                parts.append(f"name={f['name']}")
            if f.get('placeholder'):
                parts.append(f'placeholder="{f["placeholder"]}"')
            if f.get('value'):
                parts.append(f'value="{f["value"]}"')
            if f.get('required'):
                parts.append("[required]")
            if f.get('readOnly'):
                parts.append("[readonly]")
            if f.get('options'):
                parts.append(f"opts=[{', '.join(f['options'][:5])}]")
            parts.append(f"@({f['x']},{f['y']})")
            lines.append("  " + " ".join(parts))

        return "\n".join(lines)

    async def get_page_text(self) -> str:
        """Read the main text content of the current page."""
        if not self._page:
            raise RuntimeError("Browser not initialized")

        text = await self._page.evaluate("""() => {
            // Get main content, prefer article/main/content areas
            const selectors = ['main', 'article', '[role="main"]', '#content', '.content'];
            for (const sel of selectors) {
                const el = document.querySelector(sel);
                if (el && el.innerText.trim().length > 50) {
                    return el.innerText.trim().substring(0, 8000);
                }
            }
            return document.body.innerText.trim().substring(0, 8000);
        }""")

        return f"PAGE TEXT ({self._page.url}):\n{text}" if text else "No readable text content found."

    async def click_element(self, selector: str) -> "ComputerState":
        """Click an element by CSS selector — more precise than coordinate clicking."""
        if not self._page:
            raise RuntimeError("Browser not initialized")
        self._log_action("click_element", {"selector": selector})

        # Try to get element coordinates for preview
        try:
            el = await self._page.query_selector(selector)
            if el:
                box = await el.bounding_box()
                if box:
                    cx = int(box["x"] + box["width"] / 2)
                    cy = int(box["y"] + box["height"] / 2)
                    await self._send_preview(
                        cx, cy, "click", f"Clicking: {selector[:40]}"
                    )
        except Exception:
            pass

        # Fix selectors that Playwright doesn't support
        fixed_selector = self._fix_selector(selector)

        try:
            await self._page.click(fixed_selector, timeout=2000)
        except Exception:
            # Fallback strategies
            fallbacks = []
            # Extract text from :contains() or :has-text()
            import re as _re
            text_match = _re.search(r":(?:contains|has-text)\(['\"]?(.+?)['\"]?\)", selector)
            if text_match:
                text = text_match.group(1)
                fallbacks.append(f"text={text}")
                fallbacks.append(f"text='{text}'")
            fallbacks.append(f"text={selector}")

            for fb in fallbacks:
                try:
                    await self._page.click(fb, timeout=2000)
                    break
                except Exception:
                    continue
            else:
                logger.warning("click_element failed for '%s'", selector)
                return await self.current_state()
        await asyncio.sleep(0.2)
        try:
            await self._page.wait_for_load_state("domcontentloaded", timeout=3000)
        except Exception:
            pass
        return await self.current_state()

    @staticmethod
    def _fix_selector(selector: str) -> str:
        """Convert unsupported CSS selectors to Playwright-compatible ones."""
        import re as _re
        # :contains('text') → text='text'
        m = _re.match(r"(.+?):contains\(['\"]?(.+?)['\"]?\)", selector)
        if m:
            tag = m.group(1).strip()
            text = m.group(2)
            if tag and tag not in ("*",):
                return f"{tag}:has-text(\"{text}\")"
            return f"text={text}"
        return selector

    async def fill_field(self, selector: str, value: str) -> "ComputerState":
        """Fill a form field by CSS selector."""
        if not self._page:
            raise RuntimeError("Browser not initialized")
        self._log_action("fill_field", {"selector": selector, "value": value})
        fixed_selector = self._fix_selector(selector)
        try:
            await self._page.fill(fixed_selector, value, timeout=2000)
        except Exception:
            # Fallback: try click + type
            try:
                await self._page.click(fixed_selector, timeout=2000)
                await self._page.keyboard.type(value, delay=30)
            except Exception as e:
                logger.warning("fill_field failed for '%s': %s", selector, e)
                return await self.current_state()
        await asyncio.sleep(0.2)
        return await self.current_state()

    async def open_new_tab(self, url: str = "") -> "ComputerState":
        """Open a new browser tab and optionally navigate to a URL."""
        if not self._context:
            raise RuntimeError("Browser not initialized")
        self._log_action("open_new_tab", {"url": url})
        new_page = await self._context.new_page()
        self._tabs = getattr(self, '_tabs', [])
        self._tabs.append(self._page)
        self._page = new_page
        if url:
            try:
                await self._page.goto(url, wait_until="domcontentloaded", timeout=10000)
            except Exception as e:
                logger.warning("New tab navigation failed: %s", e)
        return await self.current_state()

    async def switch_tab(self, tab_index: int) -> "ComputerState":
        """Switch to a different browser tab by index (0-based)."""
        self._tabs = getattr(self, '_tabs', [])
        all_pages = self._tabs + [self._page]
        if 0 <= tab_index < len(all_pages):
            old_page = self._page
            self._page = all_pages[tab_index]
            # Update tabs list
            self._tabs = [p for p in all_pages if p != self._page]
            self._log_action("switch_tab", {"index": tab_index})
            await self._page.bring_to_front()
        else:
            logger.warning("Invalid tab index: %d (have %d tabs)", tab_index, len(all_pages))
        return await self.current_state()

    async def close_tab(self) -> "ComputerState":
        """Close current tab and switch to the previous one."""
        self._tabs = getattr(self, '_tabs', [])
        if self._tabs:
            await self._page.close()
            self._page = self._tabs.pop()
            self._log_action("close_tab", {})
        return await self.current_state()

    async def drag_and_drop(
        self, x: int, y: int, destination_x: int, destination_y: int
    ) -> ComputerState:
        """Drag and drop — not supported per project requirements."""
        self._log_action(
            "drag_and_drop",
            {"x": x, "y": y, "dest_x": destination_x, "dest_y": destination_y},
        )
        logger.warning("drag_and_drop is not supported by SAHAY")
        return await self.current_state()

    async def select_option_at(self, x: int, y: int, option_text: str) -> ComputerState:
        """Click a dropdown at (x, y), then select an option by visible text."""
        if not self._page:
            raise RuntimeError("Browser not initialized")
        self._log_action(
            "select_option_at", {"x": x, "y": y, "option": option_text}
        )
        await self._send_preview(x, y, "click", f"Selecting: {option_text[:30]}")
        await self._page.mouse.click(x, y)
        await asyncio.sleep(0.2)
        await self._page.keyboard.type(option_text, delay=80)
        await asyncio.sleep(0.2)
        await self._page.keyboard.press("Enter")
        await asyncio.sleep(0.2)
        return await self.current_state()
