/* =============================================================
   script.js — Instagram Giveaway Picker (Docker / Render edition)
   Multi-user login flow, 2FA support, credential-based sessions
   ============================================================= */

'use strict';

// ── State ──────────────────────────────────────────────────────────────────
let allComments  = [];
let lastWinner   = null;
let currentUser  = null;   // Instagram username of the logged-in user
let pendingPw    = null;   // Kept in memory only for 2FA re-submission

// ── DOM refs ───────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);

// Modal
const loginOverlay      = $('login-overlay');
const loginFormSection  = $('login-form-section');
const twofaFormSection  = $('twofa-form-section');
const loginUsername     = $('login-username');
const loginPassword     = $('login-password');
const loginError        = $('login-error');
const loginStatus       = $('login-status');      // ← live progress message
const btnLogin          = $('btn-login');
const twofaCode         = $('twofa-code');
const twofaError        = $('twofa-error');
const btnTwofa          = $('btn-twofa');
const btnBackLogin      = $('btn-back-login');
const btnTogglePw       = $('btn-toggle-pw');
const pwEyeOpen         = $('pw-eye-open');
const pwEyeClosed       = $('pw-eye-closed');

// Header
const userDot           = $('user-dot');
const userName          = $('user-name');
const btnLogout         = $('btn-logout');

// Main
const postUrlInput      = $('post-url');
const btnLoad           = $('btn-load');
const errorBanner       = $('error-banner');
const errorText         = $('error-text');
const infoBanner        = $('info-banner');
const infoText          = $('info-text');
const stepComments      = $('step-comments');
const commentsList      = $('comments-list');
const commentCount      = $('comment-count');
const uniqueCount       = $('unique-count');
const filterInput       = $('filter-input');
const dedupCheck        = $('dedup-check');
const btnPick           = $('btn-pick');
const stepWinner        = $('step-winner');
const winnerAvatar      = $('winner-avatar');
const winnerInitial     = $('winner-initial');
const winnerUsername    = $('winner-username');
const winnerComment     = $('winner-comment');
const winnerPoolSize    = $('winner-pool-size');
const btnPickAgain      = $('btn-pick-again');
const btnCopyWinner     = $('btn-copy-winner');

// ── Init ───────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  // Restore username from localStorage if previously logged in
  const saved = localStorage.getItem('ig_username');
  if (saved) {
    checkExistingSession(saved);
  } else {
    showLoginModal();
  }
  attachListeners();
});

// ── Session check ──────────────────────────────────────────────────────────
async function checkExistingSession(username) {
  try {
    const res  = await fetch(`/session-status?username=${encodeURIComponent(username)}`);
    const data = await res.json();
    if (data.has_session) {
      setLoggedIn(username);
    } else {
      localStorage.removeItem('ig_username');
      showLoginModal();
    }
  } catch {
    showLoginModal();
  }
}

// ── Event listeners ────────────────────────────────────────────────────────
function attachListeners() {
  // Login modal
  btnLogin.addEventListener('click', handleLogin);
  loginPassword.addEventListener('keydown', e => { if (e.key === 'Enter') handleLogin(); });
  btnTwofa.addEventListener('click', handleTwoFA);
  twofaCode.addEventListener('keydown', e => { if (e.key === 'Enter') handleTwoFA(); });
  btnBackLogin.addEventListener('click', () => {
    twofaFormSection.hidden = true;
    loginFormSection.hidden = false;
    pendingPw = null;
  });

  // Password visibility toggle
  btnTogglePw.addEventListener('click', () => {
    const isPassword = loginPassword.type === 'password';
    loginPassword.type = isPassword ? 'text' : 'password';
    pwEyeOpen.style.display   = isPassword ? 'none' : '';
    pwEyeClosed.style.display = isPassword ? '' : 'none';
  });

  // Header
  btnLogout.addEventListener('click', handleLogout);

  // Main
  btnLoad.addEventListener('click', handleLoadComments);
  postUrlInput.addEventListener('keydown', e => { if (e.key === 'Enter') handleLoadComments(); });
  btnPick.addEventListener('click', handlePickWinner);
  btnPickAgain.addEventListener('click', handlePickWinner);
  btnCopyWinner.addEventListener('click', handleCopyWinner);
}

// ── Login ──────────────────────────────────────────────────────────────────
async function handleLogin() {
  const username = loginUsername.value.trim();
  const password = loginPassword.value.trim();

  loginError.hidden = true;
  loginStatus.hidden = true;

  if (!username || !password) {
    showModalError(loginError, 'Please enter your username and password.');
    return;
  }

  setLoading(btnLogin, true);

  // ── Live status messages while Playwright runs on the server ──────────────
  // Instagram login takes 30–90 seconds headlessly on a cloud server.
  const STAGES = [
    { ms:     0, msg: '⏳ Sending credentials to server…' },
    { ms:  3000, msg: '🌐 Launching headless Chromium browser…' },
    { ms:  8000, msg: '📸 Opening Instagram login page…' },
    { ms: 18000, msg: '🔐 Submitting your credentials…' },
    { ms: 28000, msg: '⏳ Waiting for Instagram to respond…' },
    { ms: 50000, msg: '🔄 Still working — Instagram can be slow from server IPs…' },
    { ms: 80000, msg: '⏱️ Almost there, hang tight…' },
  ];

  const statusTimers = STAGES.map(({ ms, msg }) =>
    setTimeout(() => {
      loginStatus.textContent = msg;
      loginStatus.hidden = false;
    }, ms)
  );

  const clearStatusTimers = () => statusTimers.forEach(clearTimeout);

  // 5-minute hard timeout
  const controller = new AbortController();
  const hardTimeout = setTimeout(() => controller.abort(), 5 * 60 * 1000);

  try {
    const res = await fetch('/login', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ username, password }),
      signal:  controller.signal,
    });

    clearStatusTimers();
    clearTimeout(hardTimeout);
    loginStatus.hidden = true;

    let data;
    try {
      data = await res.json();
    } catch {
      showModalError(loginError, `Server error (HTTP ${res.status}). Check the Render logs for details.`);
      return;
    }

    if (data.status === 'ok') {
      pendingPw = null;
      setLoggedIn(username);
      hideLoginModal();

    } else if (data.status === '2fa_required') {
      pendingPw = password;
      loginFormSection.hidden = true;
      twofaFormSection.hidden = false;
      twofaCode.focus();

    } else if (data.status === 'challenge') {
      showModalError(loginError,
        '⚠️ Instagram flagged this login as suspicious (new server IP). ' +
        'Try logging into instagram.com normally in your personal browser first ' +
        'to clear any flags, then try again here.'
      );

    } else {
      // Show the actual server error so we can debug
      const errMsg = data.error || 'Login failed. Check your credentials.';
      showModalError(loginError, '❌ ' + errMsg);
    }

  } catch (err) {
    clearStatusTimers();
    clearTimeout(hardTimeout);
    loginStatus.hidden = true;

    if (err.name === 'AbortError') {
      showModalError(loginError, '⏱️ Login timed out after 5 minutes. Instagram may be blocking server IPs. Try again or check Render logs.');
    } else {
      showModalError(loginError, '🔌 Network error: ' + err.message);
    }
  } finally {
    setLoading(btnLogin, false);
  }
}

// ── 2FA ────────────────────────────────────────────────────────────────────
async function handleTwoFA() {
  const username = loginUsername.value.trim();
  const code     = twofaCode.value.trim();

  twofaError.hidden = true;

  if (!code) {
    showModalError(twofaError, 'Please enter the verification code.');
    return;
  }

  setLoading(btnTwofa, true);

  try {
    const res  = await fetch('/login/2fa', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ username, password: pendingPw, code }),
    });
    const data = await res.json();

    if (data.status === 'ok') {
      pendingPw = null;
      setLoggedIn(username);
      hideLoginModal();
    } else {
      showModalError(twofaError, data.error || 'Invalid code. Please try again.');
    }
  } catch (err) {
    showModalError(twofaError, 'Network error: ' + err.message);
  } finally {
    setLoading(btnTwofa, false);
  }
}

// ── Logout ─────────────────────────────────────────────────────────────────
async function handleLogout() {
  if (!currentUser) return;
  if (!confirm(`Log out @${currentUser}? You'll need to re-enter your credentials next time.`)) return;

  try {
    await fetch('/logout', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ username: currentUser }),
    });
  } catch { /* ignore */ }

  localStorage.removeItem('ig_username');
  currentUser = null;
  allComments = [];

  // Reset UI
  stepComments.hidden = true;
  stepWinner.hidden   = true;
  hideError();
  hideInfo();

  showLoginModal();
}

// ── Load comments ──────────────────────────────────────────────────────────
async function handleLoadComments() {
  if (!currentUser) { showLoginModal(); return; }

  const url = postUrlInput.value.trim();
  if (!url) { showError('Please paste an Instagram post URL first.'); return; }

  hideError();
  showInfo('⏳ Playwright is opening a headless browser and loading comments… this may take 30–90 seconds for posts with many comments.');
  setLoading(btnLoad, true);

  try {
    const res  = await fetch('/load-comments', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ url, username: currentUser }),
    });
    const data = await res.json();

    hideInfo();

    if (res.status === 401) {
      // Session expired
      localStorage.removeItem('ig_username');
      currentUser = null;
      showError('Your session expired. Please log in again.');
      showLoginModal();
      return;
    }

    if (!res.ok || data.error) {
      showError(data.error || 'Failed to load comments.');
      return;
    }

    allComments = data.comments || [];

    if (allComments.length === 0) {
      showError('No comments found. The post may be private or have no comments yet.');
      return;
    }

    renderComments(allComments);
    stepComments.hidden = false;
    stepWinner.hidden   = true;
    stepComments.scrollIntoView({ behavior: 'smooth', block: 'start' });

  } catch (err) {
    hideInfo();
    showError('Network error: ' + err.message);
  } finally {
    setLoading(btnLoad, false);
  }
}

// ── Render comments ────────────────────────────────────────────────────────
function renderComments(comments) {
  const total  = comments.length;
  const unique = new Set(comments.map(c => c.username.toLowerCase())).size;
  commentCount.textContent = total.toLocaleString();
  uniqueCount.textContent  = unique.toLocaleString();

  commentsList.innerHTML = '';
  const slice = comments.slice(0, 150);

  slice.forEach((c, i) => {
    const row = document.createElement('div');
    row.className = 'comment-row';
    row.setAttribute('role', 'listitem');
    row.style.animationDelay = `${Math.min(i * 10, 400)}ms`;

    row.innerHTML = `
      <div class="comment-avatar" style="background:${usernameToGradient(c.username)}" aria-hidden="true">
        ${escapeHtml((c.username[0] || '?').toUpperCase())}
      </div>
      <div class="comment-body">
        <div class="comment-username">@${escapeHtml(c.username)}</div>
        <div class="comment-text">${escapeHtml(c.text)}</div>
      </div>
    `;
    commentsList.appendChild(row);
  });

  if (comments.length > 150) {
    const more = document.createElement('div');
    more.className = 'comment-row';
    more.style.cssText = 'justify-content:center;color:var(--text-muted);font-size:0.82rem;padding:14px;';
    more.textContent = `…and ${(comments.length - 150).toLocaleString()} more comments`;
    commentsList.appendChild(more);
  }
}

// ── Pick winner ────────────────────────────────────────────────────────────
async function handlePickWinner() {
  if (!allComments.length) return;

  btnPick.disabled = true;
  btnPick.style.transform = 'scale(0.96)';
  setTimeout(() => { btnPick.style.transform = ''; btnPick.disabled = false; }, 400);

  try {
    const res  = await fetch('/pick-winner', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({
        comments:    allComments,
        filter:      filterInput.value.trim() || null,
        deduplicate: dedupCheck.checked,
      }),
    });
    const data = await res.json();

    if (!res.ok || data.error) { showError(data.error || 'Could not pick a winner.'); return; }
    if (!data.winner) {
      showError('No eligible comments match your filter. Try relaxing the criteria.');
      return;
    }

    lastWinner = data.winner;
    showWinner(data.winner, data.pool_size);

  } catch (err) {
    showError('Network error: ' + err.message);
  }
}

function showWinner(winner, poolSize) {
  stepWinner.hidden = false;

  winnerInitial.textContent    = (winner.username[0] || '?').toUpperCase();
  winnerAvatar.style.background = usernameToGradient(winner.username);
  winnerUsername.textContent   = '@' + winner.username;
  winnerComment.textContent    = '"' + winner.text + '"';
  winnerPoolSize.textContent   = `Selected from ${poolSize.toLocaleString()} eligible ${poolSize === 1 ? 'entry' : 'entries'}`;

  // Re-trigger animations
  void stepWinner.offsetWidth;
  winnerAvatar.style.animation = 'none';
  void winnerAvatar.offsetWidth;
  winnerAvatar.style.animation = '';

  stepWinner.scrollIntoView({ behavior: 'smooth', block: 'start' });
  launchConfetti();
}

// ── Copy username ──────────────────────────────────────────────────────────
async function handleCopyWinner() {
  if (!lastWinner) return;
  try {
    await navigator.clipboard.writeText('@' + lastWinner.username);
    const orig = btnCopyWinner.textContent;
    btnCopyWinner.textContent = '✅ Copied!';
    setTimeout(() => { btnCopyWinner.textContent = orig; }, 2000);
  } catch {
    btnCopyWinner.textContent = lastWinner.username;
  }
}

// ── UI state helpers ───────────────────────────────────────────────────────
function showLoginModal() {
  loginOverlay.classList.remove('hidden');
  loginFormSection.hidden = false;
  twofaFormSection.hidden = true;
  loginError.hidden       = true;
  twofaError.hidden       = true;
  loginUsername.focus();
}

function hideLoginModal() {
  loginOverlay.classList.add('hidden');
  loginPassword.value = '';
  twofaCode.value     = '';
}

function setLoggedIn(username) {
  currentUser = username;
  localStorage.setItem('ig_username', username);
  userDot.className    = 'user-dot active';
  userName.textContent = '@' + username;
  btnLogout.hidden     = false;
}

function setLoading(btn, isLoading) {
  btn.classList.toggle('loading', isLoading);
  btn.disabled = isLoading;
}

function showError(msg) { errorText.textContent = msg; errorBanner.hidden = false; }
function hideError()    { errorBanner.hidden = true; }
function showInfo(msg)  { infoText.textContent = msg; infoBanner.hidden = false; }
function hideInfo()     { infoBanner.hidden = true; }

function showModalError(el, msg) { el.textContent = msg; el.hidden = false; }

// ── Confetti ───────────────────────────────────────────────────────────────
function launchConfetti() {
  const canvas = $('confetti-canvas');
  const ctx    = canvas.getContext('2d');
  canvas.width = window.innerWidth;
  canvas.height = window.innerHeight;

  const COLORS = ['#fcb045','#fd1d1d','#833ab4','#a855f7','#f472b6','#38bdf8','#4ade80'];
  const pieces = Array.from({ length: 140 }, () => ({
    x: Math.random() * canvas.width,
    y: -10 - Math.random() * 200,
    w: 6 + Math.random() * 8,
    h: 10 + Math.random() * 8,
    r: Math.random() * Math.PI * 2,
    dr: (Math.random() - 0.5) * 0.18,
    dx: (Math.random() - 0.5) * 3,
    dy: 3 + Math.random() * 4,
    col: COLORS[Math.floor(Math.random() * COLORS.length)],
    alpha: 1,
  }));

  let frame;
  const tick = () => {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    let alive = false;
    pieces.forEach(p => {
      if (p.y > canvas.height + 20) return;
      alive = true;
      p.x += p.dx; p.y += p.dy; p.r += p.dr;
      if (p.y > canvas.height * 0.6) p.alpha = Math.max(0, p.alpha - 0.015);
      ctx.save();
      ctx.globalAlpha = p.alpha;
      ctx.translate(p.x, p.y);
      ctx.rotate(p.r);
      ctx.fillStyle = p.col;
      ctx.fillRect(-p.w/2, -p.h/2, p.w, p.h);
      ctx.restore();
    });
    if (alive) frame = requestAnimationFrame(tick);
    else ctx.clearRect(0, 0, canvas.width, canvas.height);
  };
  cancelAnimationFrame(frame);
  frame = requestAnimationFrame(tick);
}

// ── Utilities ──────────────────────────────────────────────────────────────
function escapeHtml(str) {
  return String(str)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function usernameToGradient(username) {
  let hash = 0;
  for (let i = 0; i < username.length; i++) hash = username.charCodeAt(i) + ((hash << 5) - hash);
  const h1 = Math.abs(hash) % 360;
  const h2 = (h1 + 40 + Math.abs(hash >> 8) % 80) % 360;
  return `linear-gradient(135deg, hsl(${h1},70%,55%), hsl(${h2},65%,45%))`;
}
