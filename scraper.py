"""
scraper.py — Playwright Instagram comment scraper (headless / server mode).

Supports:
  - Headless Chromium (no display required — works on Render / Docker)
  - Per-user session files keyed by SHA-256 of the username
  - Credential-based login (username + password filled programmatically)
  - 2FA detection — returns a structured status so the caller can handle it
  - Debug screenshot saving on errors
"""

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

logger = logging.getLogger(__name__)

# ── Directories (read from env so they work both locally and on Render) ────
def _init_dirs() -> tuple[Path, Path]:
    """
    Initialise session and debug directories.
    Falls back to local ./sessions and ./debug if the configured path
    (e.g. /data/sessions) is not writable — common on Render when the
    persistent disk hasn't been attached yet.
    """
    _base   = Path(__file__).parent
    targets = [
        (
            Path(os.environ.get("SESSION_DIR", str(_base / "sessions"))),
            Path(os.environ.get("DEBUG_DIR",   str(_base / "debug"))),
        ),
        # Hard fallback — always inside the container/workspace
        (_base / "sessions", _base / "debug"),
    ]
    for sess_dir, dbg_dir in targets:
        try:
            sess_dir.mkdir(parents=True, exist_ok=True)
            dbg_dir.mkdir(parents=True, exist_ok=True)
            return sess_dir, dbg_dir
        except OSError as exc:
            logger.warning("Cannot create dirs %s / %s: %s — trying fallback.", sess_dir, dbg_dir, exc)
    raise RuntimeError("Could not create any session/debug directory.")

SESSION_DIR, DEBUG_DIR = _init_dirs()

# ── Selectors ──────────────────────────────────────────────────────────────
LOAD_MORE_SELECTORS = [
    "svg[aria-label='Load more comments']",
    "[aria-label='Load more comments']",
    "button:has-text('Load more comments')",
    "span:has-text('Load more comments')",
    "button[class*='_abl-']",
    "span[class*='_abl-']",
]

DISMISS_SELECTORS = [
    # Cookie consent (various Instagram variants)
    "button:has-text('Allow all cookies')",
    "button:has-text('Accept All')",
    "button:has-text('Accept all')",
    "button:has-text('Allow essential and optional cookies')",
    "button:has-text('Allow')",
    # Notification prompts
    "button:has-text('Not Now')",
    "button:has-text('Not now')",
    # Generic close / dismiss
    "[aria-label='Close']",
    "button:has-text('Close')",
    # "Use the app" interstitial sometimes shown before login
    "button:has-text('Continue')",
]


# ── Session helpers ────────────────────────────────────────────────────────

def _session_path(username: str) -> Path:
    """Return the session file path for a given username (hashed for safety)."""
    h = hashlib.sha256(username.lower().strip().encode()).hexdigest()[:16]
    return SESSION_DIR / f"{h}.json"


def has_session(username: str) -> bool:
    p = _session_path(username)
    return p.exists() and p.stat().st_size > 10


def delete_session(username: str) -> bool:
    p = _session_path(username)
    if p.exists():
        p.unlink()
        logger.info("Deleted session for %s", username)
        return True
    return False


def _save_session(context, username: str) -> None:
    state = context.storage_state()
    path  = _session_path(username)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    logger.info("Session saved for %s → %s", username, path)


def _save_debug_screenshot(page, label: str) -> Optional[str]:
    try:
        path = DEBUG_DIR / f"{label}_{int(time.time())}.png"
        page.screenshot(path=str(path))
        logger.info("Debug screenshot saved: %s", path)
        return str(path)
    except Exception:
        return None


# ── Browser factory ────────────────────────────────────────────────────────

def _make_browser_context(p, username: str):
    """Launch headless Chromium + create a context (with session if available)."""
    browser = p.chromium.launch(
        headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--window-size=1280,900",
        ],
    )

    context_kwargs = {
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "viewport":       {"width": 1280, "height": 900},
        "locale":         "en-US",
        "timezone_id":    "America/New_York",
        "extra_http_headers": {"Accept-Language": "en-US,en;q=0.9"},
    }

    if has_session(username):
        logger.info("Loading saved session for %s", username)
        context_kwargs["storage_state"] = str(_session_path(username))

    context = browser.new_context(**context_kwargs)
    # Hide webdriver fingerprint
    context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return browser, context


# ── Login ──────────────────────────────────────────────────────────────────

def login(username: str, password: str) -> dict:
    """
    Log into Instagram with the given credentials using headless Playwright.

    Returns a dict:
      { "status": "ok" }
      { "status": "2fa_required" }
      { "status": "challenge" }
      { "status": "failed", "error": str }
    """
    logger.info("Starting login for %s", username)

    with sync_playwright() as p:
        browser, context = _make_browser_context(p, username)
        page = context.new_page()

        try:
            logger.info("Navigating to Instagram login page for %s", username)
            response = page.goto(
                "https://www.instagram.com/accounts/login/",
                wait_until="domcontentloaded",
                timeout=45_000,
            )

            # Wait for React to hydrate
            time.sleep(4)

            # ── Log page state so we can diagnose in Render logs ──────────
            try:
                page_state = page.evaluate("""() => ({
                    title:   document.title,
                    url:     window.location.href,
                    html:    document.body ? document.body.innerHTML.substring(0, 500) : 'NO BODY',
                    inputs:  [...document.querySelectorAll('input')].map(i => ({
                        name: i.name, type: i.type, placeholder: i.placeholder,
                        visible: i.offsetHeight > 0 && i.offsetWidth > 0,
                        disabled: i.disabled
                    })),

                    buttons: [...document.querySelectorAll('button,[role=button]')]
                                .slice(0, 20)
                                .map(b => b.textContent?.trim()?.substring(0, 80))
                                .filter(Boolean)
                })""")
                logger.info("Page state: title=%r inputs=%d buttons=%s",
                            page_state.get("title"),
                            len(page_state.get("inputs", [])),
                            page_state.get("buttons"))
            except Exception as e:
                logger.warning("Could not evaluate page state: %s", e)

            # Take screenshot immediately after load
            _save_debug_screenshot(page, f"login_load_{username[:6]}")

            # ── JS-based cookie / consent dialog dismissal ────────────────
            # Uses JavaScript directly (bypasses Playwright selector issues with React)
            CONSENT_KEYWORDS = [
                "allow all cookies",
                "accept all cookies",
                "allow essential and optional cookies",
                "allow all",
                "accept all",
                "allow cookies",
                "accept cookies",
                "decline optional",   # ← also acceptable (removes the consent wall)
            ]

            for _attempt in range(4):
                dismissed = page.evaluate(
                    """(keywords) => {
                        const all = [...document.querySelectorAll(
                            'button, [role="button"], a[role="button"]'
                        )];
                        for (const kw of keywords) {
                            for (const btn of all) {
                                const t = (btn.textContent || '').trim().toLowerCase();
                                if (t.includes(kw)) {
                                    btn.click();
                                    return 'clicked: ' + btn.textContent.trim();
                                }
                            }
                        }
                        return null;
                    }""",
                    CONSENT_KEYWORDS,
                )
                if dismissed:
                    logger.info("JS consent dismissal attempt %d: %s", _attempt + 1, dismissed)
                    time.sleep(2)
                else:
                    break

            # Take screenshot after consent dismissal
            _save_debug_screenshot(page, f"after_consent_{username[:6]}")

            # ── Wait for login form inputs using JS polling ───────────────
            # Uses JavaScript so it works even if React hasn't fully re-rendered
            logger.info("Waiting for login form to appear…")
            for _wait_attempt in range(12):   # up to 24 seconds
                time.sleep(2)
                inputs_info = page.evaluate("""() => {
                    const inputs = [...document.querySelectorAll('input')];
                    return inputs.map(i => ({
                        name:     i.name,
                        type:     i.type,
                        placeholder: i.placeholder,
                        visible:  i.offsetHeight > 0 && i.offsetWidth > 0,
                        disabled: i.disabled
                    }));
                }""")
                usernames = [i for i in inputs_info
                             if i.get("name") == "username"
                             or i.get("placeholder", "").lower() in ("username", "phone number, username, or email")
                             or i.get("type") == "text" and i.get("visible")]
                if usernames:
                    logger.info("Login form appeared after %d polls. Inputs: %s",
                                _wait_attempt + 1, inputs_info)
                    break
                logger.debug("Poll %d: no username input yet. All inputs: %s",
                             _wait_attempt + 1, inputs_info)
            else:
                # Log final page state for diagnosis
                final_state = page.evaluate("""() => ({
                    url:     window.location.href,
                    title:   document.title,
                    inputs:  [...document.querySelectorAll('input')].map(i => ({
                        name: i.name, type: i.type, visible: i.offsetHeight > 0
                    })),
                    buttons: [...document.querySelectorAll('button')].slice(0, 10)
                                .map(b => b.textContent?.trim()?.substring(0, 60))
                })""")
                logger.error("Login form never appeared. Final state: %s", final_state)
                _save_debug_screenshot(page, f"no_form_{username[:6]}")
                status_code = response.status if response else "Unknown"
                return {
                    "status": "failed",
                    "error": (
                        f"Instagram did not show the login form after 30 seconds. "
                        f"HTTP Status: {status_code}. "
                        f"Page title: {final_state.get('title', 'unknown')}. "
                        f"Buttons visible: {final_state.get('buttons', [])}. "
                        "Visit /debug-screenshots to see what the browser saw. "
                        "(If HTTP status is 4xx, Instagram is likely blocking this server's IP)."
                    ),
                }

            # ── Fill username using JS (React-safe approach) ──────────────
            filled_user = page.evaluate(
                """(uname) => {
                    // Find username input by multiple strategies
                    let inp = document.querySelector("input[name='username']")
                           || document.querySelector("input[autocomplete='username']")
                           || [...document.querySelectorAll('input[type=text]')]
                                  .find(i => i.offsetHeight > 0);
                    if (!inp) return false;
                    inp.focus();
                    // React-safe: use nativeInputValueSetter to trigger React events
                    const nativeSetter = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value'
                    )?.set;
                    if (nativeSetter) {
                        nativeSetter.call(inp, uname);
                        inp.dispatchEvent(new Event('input',  {bubbles: true}));
                        inp.dispatchEvent(new Event('change', {bubbles: true}));
                    } else {
                        inp.value = uname;
                        inp.dispatchEvent(new Event('input',  {bubbles: true}));
                    }
                    return true;
                }""",
                username,
            )
            if not filled_user:
                return {"status": "failed", "error": "Could not fill username field via JavaScript."}
            time.sleep(0.8)

            # Tab to password field (more natural than clicking)
            page.keyboard.press("Tab")
            time.sleep(0.5)

            # ── Fill password using JS ─────────────────────────────────────
            filled_pass = page.evaluate(
                """(pwd) => {
                    let inp = document.querySelector("input[name='password']")
                           || document.querySelector("input[type='password']");
                    if (!inp) return false;
                    inp.focus();
                    const nativeSetter = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value'
                    )?.set;
                    if (nativeSetter) {
                        nativeSetter.call(inp, pwd);
                        inp.dispatchEvent(new Event('input',  {bubbles: true}));
                        inp.dispatchEvent(new Event('change', {bubbles: true}));
                    } else {
                        inp.value = pwd;
                        inp.dispatchEvent(new Event('input',  {bubbles: true}));
                    }
                    return true;
                }""",
                password,
            )
            if not filled_pass:
                return {"status": "failed", "error": "Could not fill password field via JavaScript."}
            time.sleep(0.8)

            # ── Click login button via JS ──────────────────────────────────
            page.evaluate("""() => {
                const btn = document.querySelector("button[type='submit']")
                         || [...document.querySelectorAll('button')]
                               .find(b => /log.?in|sign.?in/i.test(b.textContent));
                if (btn) btn.click();
            }""")
            time.sleep(3)

            current_url = page.url

            # ── 2FA required ───────────────────────────────────────────────
            if "two_factor" in current_url or page.locator("input[name='verificationCode']").is_visible(timeout=3_000):
                logger.warning("2FA required for %s", username)
                _save_debug_screenshot(page, f"2fa_{username[:6]}")
                return {"status": "2fa_required"}

            # ── Security challenge (e.g. suspicious login) ─────────────────
            if "challenge" in current_url:
                logger.warning("Challenge detected for %s", username)
                _save_debug_screenshot(page, f"challenge_{username[:6]}")
                return {"status": "challenge"}

            # ── Wrong password / error message ─────────────────────────────
            error_locator = page.locator("#slfErrorAlert, [role='alert']")
            if error_locator.is_visible(timeout=3_000):
                error_msg = error_locator.inner_text()
                logger.error("Login error for %s: %s", username, error_msg)
                _save_debug_screenshot(page, f"error_{username[:6]}")
                return {"status": "failed", "error": error_msg}

            # ── Wait for home feed (successful login) ──────────────────────
            try:
                page.wait_for_url(
                    lambda url: (
                        "accounts/login" not in url
                        and "challenge" not in url
                        and "two_factor" not in url
                    ),
                    timeout=20_000,
                )
            except PlaywrightTimeout:
                _save_debug_screenshot(page, f"timeout_{username[:6]}")
                return {"status": "failed", "error": "Login timed out — Instagram may be challenging this login."}

            time.sleep(2)
            _save_session(context, username)
            logger.info("Login successful for %s", username)
            return {"status": "ok"}

        except Exception as exc:
            logger.exception("Login exception for %s", username)
            _save_debug_screenshot(page, f"exception_{username[:6]}")
            return {"status": "failed", "error": str(exc)}
        finally:
            browser.close()


def submit_2fa(username: str, password: str, code: str) -> dict:
    """
    Complete login when 2FA is required.
    Re-logs in and then submits the verification code.
    """
    logger.info("Submitting 2FA code for %s", username)

    with sync_playwright() as p:
        browser, context = _make_browser_context(p, username)
        page = context.new_page()

        try:
            page.goto("https://www.instagram.com/accounts/login/", wait_until="domcontentloaded", timeout=30_000)
            time.sleep(2)
            _dismiss_dialogs(page)

            page.locator("input[name='username']").fill(username)
            page.locator("input[name='password']").fill(password)
            page.locator("button[type='submit']").click()
            time.sleep(3)

            code_input = page.locator("input[name='verificationCode']")
            code_input.wait_for(timeout=10_000)
            code_input.fill(code.strip())

            confirm_btn = page.locator("button[type='button']:has-text('Confirm'), button[type='submit']")
            confirm_btn.first.click()
            time.sleep(3)

            page.wait_for_url(
                lambda url: "accounts/login" not in url and "two_factor" not in url,
                timeout=15_000,
            )
            time.sleep(2)
            _save_session(context, username)
            return {"status": "ok"}

        except Exception as exc:
            logger.exception("2FA exception for %s", username)
            return {"status": "failed", "error": str(exc)}
        finally:
            browser.close()


# ── Comment scraping ───────────────────────────────────────────────────────

def fetch_comments(post_url: str, username: str) -> list[dict]:
    """
    Navigate to a post and extract all comments.

    Args:
        post_url: Full Instagram post/reel URL.
        username: The logged-in user's Instagram username (to load their session).

    Returns:
        List of {"username": str, "text": str} dicts.

    Raises:
        RuntimeError: If no session exists for this user.
        Exception: On any Playwright error.
    """
    if not has_session(username):
        raise RuntimeError(f"No saved session for '{username}'. Please log in first.")

    logger.info("Fetching comments for %s as %s", post_url, username)

    with sync_playwright() as p:
        browser, context = _make_browser_context(p, username)
        page = context.new_page()

        try:
            page.goto(post_url, wait_until="domcontentloaded", timeout=60_000)
            time.sleep(3)

            # Dismiss any pop-ups
            _dismiss_dialogs(page)

            # Check if we got redirected to login (session expired)
            if "accounts/login" in page.url:
                delete_session(username)
                raise RuntimeError("Session expired. Please log in again.")

            # Load all comments
            _load_all_comments(page)

            # Extract
            comments = _extract_comments(page)

            # Re-save session (cookies may have refreshed)
            _save_session(context, username)

            return comments

        except Exception:
            _save_debug_screenshot(page, f"scrape_error_{username[:6]}")
            raise
        finally:
            browser.close()


def _dismiss_dialogs(page) -> None:
    for sel in DISMISS_SELECTORS:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=1_500):
                btn.click()
                time.sleep(0.8)
        except Exception:
            pass


def _load_all_comments(page) -> None:
    max_clicks = 200
    clicked    = 0

    for _ in range(max_clicks):
        button = None
        for sel in LOAD_MORE_SELECTORS:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=2_000):
                    button = btn
                    break
            except Exception:
                continue

        if button is None:
            logger.info("No more 'Load more comments' button — done after %d clicks.", clicked)
            break

        try:
            button.scroll_into_view_if_needed()
            button.click()
            clicked += 1
            logger.debug("Load-more click #%d", clicked)
            time.sleep(1.8 + (clicked % 3) * 0.4)
        except Exception as exc:
            logger.warning("Load-more click failed: %s", exc)
            break

    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    time.sleep(2)


def _extract_comments(page) -> list[dict]:
    raw = page.evaluate(
        """
        () => {
            const comments = [];
            const containers = document.querySelectorAll('ul li, article ul li');

            containers.forEach(li => {
                const links = li.querySelectorAll('a');
                let username = '';
                for (const a of links) {
                    const text = (a.textContent || '').trim();
                    if (text && !text.startsWith('#') && text.length < 50) {
                        username = text;
                        break;
                    }
                }
                if (!username) return;

                const spans = li.querySelectorAll('span');
                let commentText = '';
                for (const span of spans) {
                    const t = (span.textContent || '').trim();
                    if (t && t !== username && !t.match(/^\\d+[smhd]$/) && t.length > 0) {
                        commentText = t;
                        break;
                    }
                }

                if (username && commentText) {
                    comments.push({ username, text: commentText });
                }
            });

            return comments;
        }
        """
    )

    seen    = set()
    results = []
    for item in raw:
        key = (item.get("username", "").strip(), item.get("text", "").strip()[:120])
        if key not in seen and key[0] and key[1]:
            seen.add(key)
            results.append({"username": key[0], "text": key[1]})

    logger.info("Extracted %d unique comments.", len(results))
    return results
