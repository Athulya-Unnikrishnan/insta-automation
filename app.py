"""
app.py — Flask web server for the Instagram Giveaway Winner Picker.

Routes:
  GET  /                    → Main UI
  GET  /health              → Health check (Render uses this)
  GET  /session-status      → { "has_session": bool } for a given username
  POST /login               → { username, password } → login result
  POST /login/2fa           → { username, password, code } → 2FA completion
  POST /logout              → { username } → delete session
  POST /load-comments       → { url, username } → { comments, count }
  POST /pick-winner         → { comments, filter, deduplicate } → { winner, pool_size }
"""

import functools
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, Response
from whitenoise import WhiteNoise

from picker import pick_winner

# ── Load .env for local development ────────────────────────────────────────
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

# ── Import scraper safely — a bad SESSION_DIR must not kill the whole app ──
_SCRAPER_ERROR = None
try:
    from scraper import delete_session, fetch_comments, has_session, login, submit_2fa
    SCRAPER_OK = True
except Exception as _scraper_err:
    _SCRAPER_ERROR = str(_scraper_err)
    logger.error("Failed to import scraper: %s", _scraper_err)
    SCRAPER_OK = False

# ── Flask app — explicit paths so gunicorn always finds static/templates ───
_HERE = Path(__file__).parent
app = Flask(
    __name__,
    static_folder=str(_HERE / "static"),
    template_folder=str(_HERE / "templates"),
)

# ── WhiteNoise: serve static files reliably under gunicorn ─────────────────
# This wraps the WSGI app so static files are served directly without
# going through Flask's routing — faster and more reliable on Render.
app.wsgi_app = WhiteNoise(
    app.wsgi_app,
    root=str(_HERE / "static"),
    prefix="static",
)

# ── Optional HTTP Basic Auth ────────────────────────────────────────────────
APP_PASSWORD = os.environ.get("APP_PASSWORD", "").strip()


def require_auth(f):
    """Decorator: protect routes with APP_PASSWORD if one is configured."""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not APP_PASSWORD:
            return f(*args, **kwargs)          # No password set → open access

        auth = request.authorization
        if not auth or auth.password != APP_PASSWORD:
            return Response(
                "Access restricted. Enter the app password.",
                401,
                {"WWW-Authenticate": 'Basic realm="Giveaway Picker"'},
            )
        return f(*args, **kwargs)
    return decorated


# ── Routes ──────────────────────────────────────────────────────────────────

@app.route("/health")
def health():
    """Render health check endpoint."""
    return jsonify({"status": "ok", "scraper_ok": SCRAPER_OK})


@app.route("/debug")
def debug():
    """Startup diagnostics — visit /debug to see what's working."""
    import sys
    info: dict = {
        "scraper_ok":    SCRAPER_OK,
        "scraper_error": _SCRAPER_ERROR,
        "static_folder": str(app.static_folder),
        "template_folder": str(app.template_folder),
        "python":        sys.version,
        "cwd":           os.getcwd(),
    }
    if SCRAPER_OK:
        from scraper import SESSION_DIR, DEBUG_DIR
        info["session_dir"]        = str(SESSION_DIR)
        info["session_dir_exists"] = SESSION_DIR.exists()
        info["debug_dir"]          = str(DEBUG_DIR)
    return jsonify(info)


@app.route("/ping-playwright")
def ping_playwright():
    """
    Quick test: launch Chromium, open google.com, return title.
    Use this to verify Playwright/Chromium works on Render before trying login.
    Takes ~10 seconds.
    """
    if not SCRAPER_OK:
        return jsonify({"ok": False, "error": "Scraper not initialized: " + str(_SCRAPER_ERROR)}), 500
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
            )
            page = browser.new_page()
            page.goto("https://www.google.com", wait_until="domcontentloaded", timeout=20_000)
            title = page.title()
            browser.close()
        return jsonify({"ok": True, "page_title": title, "message": "Playwright + Chromium works correctly!"})
    except Exception as exc:
        logger.exception("Playwright ping failed")
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/debug-screenshots")
def debug_screenshots():
    """
    List all debug screenshots taken during login attempts.
    Visit /debug-screenshots to see what Instagram showed the headless browser.
    """
    if not SCRAPER_OK:
        return jsonify({"error": "Scraper not initialized"}), 500
    from scraper import DEBUG_DIR
    from flask import send_file
    name = request.args.get("name")
    if name:
        # Serve a specific screenshot
        img_path = DEBUG_DIR / name
        if img_path.exists() and img_path.suffix == ".png":
            return send_file(str(img_path), mimetype="image/png")
        return "Screenshot not found", 404

    # List all screenshots
    files = sorted(DEBUG_DIR.glob("*.png"), key=lambda f: f.stat().st_mtime, reverse=True)
    links = [
        f'<li><a href="/debug-screenshots?name={f.name}" target="_blank">{f.name}</a></li>'
        for f in files[:20]
    ]
    html = (
        "<html><body style='font-family:monospace;background:#111;color:#eee;padding:20px'>"
        f"<h2>Debug Screenshots ({len(files)} total)</h2>"
        "<p>These are screenshots taken by Playwright during login attempts.</p>"
        f"<ul>{''.join(links) if links else '<li>No screenshots yet — try logging in first.</li>'}</ul>"
        "</body></html>"
    )
    return html


@app.route("/")
@require_auth
def index():
    return render_template("index.html")


@app.route("/session-status")
@require_auth
def session_status():
    if not SCRAPER_OK:
        return jsonify({"has_session": False, "error": "Scraper not initialized"})
    username = (request.args.get("username") or "").strip()
    if not username:
        return jsonify({"has_session": False})
    return jsonify({"has_session": has_session(username)})


@app.route("/login", methods=["POST"])
@require_auth
def login_route():
    if not SCRAPER_OK:
        return jsonify({"status": "failed", "error": "Scraper failed to initialize. Check /debug for details."}), 500
    data     = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()

    if not username or not password:
        return jsonify({"status": "failed", "error": "Username and password are required."}), 400

    logger.info("Login request for: %s", username)
    result = login(username, password)
    return jsonify(result)


@app.route("/login/2fa", methods=["POST"])
@require_auth
def login_2fa_route():
    """
    Body: { "username": str, "password": str, "code": str }
    Returns: { "status": "ok" } | { "status": "failed", "error": str }
    """
    data     = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()
    code     = (data.get("code") or "").strip()

    if not username or not password or not code:
        return jsonify({"status": "failed", "error": "Username, password, and 2FA code are required."}), 400

    result = submit_2fa(username, password, code)
    return jsonify(result)


@app.route("/logout", methods=["POST"])
@require_auth
def logout_route():
    """
    Body: { "username": str }
    Deletes the saved session for this user.
    """
    data     = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    if not username:
        return jsonify({"error": "Username required."}), 400

    deleted = delete_session(username)
    msg = "Session cleared. You'll need to log in again." if deleted else "No session found for this user."
    return jsonify({"message": msg, "cleared": deleted})


@app.route("/load-comments", methods=["POST"])
@require_auth
def load_comments():
    """
    Body: { "url": str, "username": str }
    Returns: { "comments": [...], "count": int }
    """
    data     = request.get_json(silent=True) or {}
    url      = (data.get("url") or "").strip()
    username = (data.get("username") or "").strip()

    if not url:
        return jsonify({"error": "No URL provided."}), 400
    if "instagram.com/p/" not in url and "instagram.com/reel/" not in url:
        return jsonify({"error": "Please provide a valid Instagram post or reel URL."}), 400
    if not username:
        return jsonify({"error": "No Instagram username provided. Please log in first."}), 400
    if not has_session(username):
        return jsonify({"error": f"No session found for @{username}. Please log in first."}), 401

    try:
        logger.info("Fetching comments for %s as @%s", url, username)
        comments = fetch_comments(url, username)
        return jsonify({"comments": comments, "count": len(comments)})
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 401
    except Exception as exc:
        logger.exception("Error fetching comments")
        return jsonify({"error": str(exc)}), 500


@app.route("/pick-winner", methods=["POST"])
@require_auth
def pick_winner_route():
    """
    Body: { "comments": [...], "filter": str|null, "deduplicate": bool }
    Returns: { "winner": {...}|null, "pool_size": int }
    """
    data     = request.get_json(silent=True) or {}
    comments = data.get("comments", [])
    filter_kw = data.get("filter") or None
    dedup    = bool(data.get("deduplicate", True))

    if not comments:
        return jsonify({"error": "No comments provided."}), 400

    winner = pick_winner(comments, filter_keyword=filter_kw, deduplicate=dedup)

    # Count eligible pool
    pool = comments
    if filter_kw:
        pool = [c for c in pool if filter_kw.lower() in c.get("text", "").lower()]
    if dedup:
        seen: set = set()
        deduped = []
        for c in pool:
            u = c.get("username", "").lower()
            if u not in seen:
                seen.add(u)
                deduped.append(c)
        pool = deduped

    return jsonify({"winner": winner, "pool_size": len(pool)})


# ── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    logger.info("Starting on http://localhost:%d", port)
    app.run(debug=False, port=port, host="0.0.0.0")
