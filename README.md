# 🏆 Instagram Giveaway Picker

A personal tool to randomly pick a winner from Instagram post comments using browser automation (Playwright). No Instagram API or App Review required.

## Features

- 🤖 **Browser automation** — Playwright controls a real Chromium browser
- 🔐 **Per-user sessions** — you and friends each log in with their own account
- 🎯 **Keyword filter** — only pick comments containing a specific word/tag
- 👤 **Deduplication** — one entry per user (optional)
- 🎉 **Animated winner reveal** with confetti
- 🐳 **Docker-ready** — deploys to Render in one click
- 🔒 **Optional access password** — protect with `APP_PASSWORD` env var

---

## Running Locally (without Docker)

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Install Playwright's Chromium
playwright install chromium

# 3. (Optional) copy .env.example and edit
cp .env.example .env
# Set SESSION_DIR=./sessions, DEBUG_DIR=./debug, PORT=5000

# 4. Start
python app.py
```

Open **http://localhost:5000**

---

## Running with Docker Locally

```bash
docker build -t insta-picker .

docker run -p 10000:10000 \
  -v $(pwd)/data:/data \
  -e SESSION_DIR=/data/sessions \
  -e DEBUG_DIR=/data/debug \
  insta-picker
```

Open **http://localhost:10000**

---

## Deploying to Render

### Option A — render.yaml (recommended)

1. Push this folder to a **private** GitHub repo
2. Go to [render.com](https://render.com) → New → Blueprint
3. Connect your repo — Render auto-reads `render.yaml`
4. In the Render dashboard, set the `APP_PASSWORD` env var (optional but recommended)
5. Attach the **1 GB disk** at `/data` ($1/mo) — keeps sessions alive across restarts
6. Deploy 🚀

### Option B — Manual

1. New → Web Service → Docker
2. Connect repo
3. Set env vars:
   - `SESSION_DIR` = `/data/sessions`
   - `DEBUG_DIR` = `/data/debug`
   - `APP_PASSWORD` = `yourpin` (optional)
4. Add Disk: mount path `/data`, 1 GB

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `APP_PASSWORD` | *(empty)* | Optional password to restrict access |
| `SESSION_DIR` | `/data/sessions` | Where per-user session files are stored |
| `DEBUG_DIR` | `/data/debug` | Where error screenshots are saved |
| `PORT` | `10000` | Server port |

---

## How Login Works

1. You (or your friend) open the URL
2. Enter your Instagram **username + password** in the login modal
3. Playwright logs in headlessly and saves only the **session cookie** — not your password
4. On subsequent visits, your session is restored automatically
5. If Instagram asks for 2FA, a code input appears in the UI

> ⚠️ **Only share the URL with people you fully trust.** If you set `APP_PASSWORD`, users must enter it to access the tool.

---

## Project Structure

```
insta-automation/
├── app.py              # Flask server
├── scraper.py          # Playwright login + comment extraction
├── picker.py           # Random winner selection
├── requirements.txt
├── Dockerfile
├── render.yaml
├── .env.example
├── static/
│   ├── style.css
│   └── script.js
└── templates/
    └── index.html
```
