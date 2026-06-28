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

from picker import pick_winner
from scraper import delete_session, fetch_comments, has_session, login, submit_2fa

# ── Load .env for local development ────────────────────────────────────────
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

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
    return jsonify({"status": "ok"})


@app.route("/")
@require_auth
def index():
    return render_template("index.html")


@app.route("/session-status")
@require_auth
def session_status():
    username = (request.args.get("username") or "").strip()
    if not username:
        return jsonify({"has_session": False})
    return jsonify({"has_session": has_session(username)})


@app.route("/login", methods=["POST"])
@require_auth
def login_route():
    """
    Body: { "username": str, "password": str }
    Returns:
      { "status": "ok" }
      { "status": "2fa_required" }
      { "status": "challenge" }
      { "status": "failed", "error": str }
    """
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
