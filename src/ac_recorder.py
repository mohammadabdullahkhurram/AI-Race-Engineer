"""
ac_recorder_combined.py — AI Race Engineer · Lap Recorder
Run on your Windows PC while Assetto Corsa is open.

1. Start AC, load Yas Marina North
2. Set env vars (PowerShell):
     $env:LIVE_PUSH_URL="https://your-backend.example.com/api/live-telemetry"
     $env:LIVE_PUSH_TOKEN="your-shared-secret"   # optional
3. Run: python ac_recorder_combined.py
4. Open http://localhost:9000 in your browser
5. Drive — recording starts when you cross S/F line (or on pit start)
6. Lap CSVs save to Desktop; live telemetry pushes to Lovable cloud

No Mac address required.
"""

import ctypes, mmap, csv, time, sys, os, io, json, threading, math
from datetime import datetime
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from configparser import ConfigParser

try:
    import requests
except ImportError:
    os.system("pip install requests -q")
    import requests


BASE_DIR = Path(_file_).resolve().parent
MAP_PNG = BASE_DIR / "map.jpeg"
MAP_INI = BASE_DIR / "map.ini"

POLL_HZ = 20.0
CAR_LENGTH_M = 5.0
CAR_WIDTH_M = 1.9
MAX_PATH_POINTS = 5000

# Live relay config — set via environment variables before running
LIVE_PUSH_URL   = os.getenv("LIVE_PUSH_URL",   "").strip()
LIVE_PUSH_TOKEN = os.getenv("LIVE_PUSH_TOKEN", "").strip()
LIVE_PUSH_HZ    = float(os.getenv("LIVE_PUSH_HZ", "5"))
CORS_ALLOW_ORIGIN   = os.getenv("CORS_ALLOW_ORIGIN", "*").strip() or "*"
LOCAL_REFERENCE_JSON  = os.getenv("LOCAL_REFERENCE_JSON",  "").strip()
LOCAL_BOUNDARIES_JSON = os.getenv("LOCAL_BOUNDARIES_JSON", "").strip()
ENABLE_LOCAL_UI = os.getenv("ENABLE_LOCAL_UI", "1").strip().lower() not in ("0", "false", "no", "off")
UI_AUTO_OPEN    = os.getenv("UI_AUTO_OPEN",    "0").strip().lower() in ("1", "true", "yes", "on")

UI_PORT = None


# ── Shared state (updated by recorder, read by web UI) ────────────────────────
state = {
    "status":    "waiting",   # waiting | recording | done | sending
    "lap_num":   0,
    "lap_time":  None,
    "samples":   0,
    "speed":     0,
    "gear":      0,
    "cur_time":  "0:00.000",
    "throttle":  0,
    "brake":     0,
    "history":   [],
    "connected": False,
    "car_x":     None,
    "car_z":     None,
    "path":      [],
    "pixel_x":   None,
    "pixel_y":   None,
    "heading_rad": 0.0,
    "map":       {},
    "completed_laps":    0,
    "current_time_ms":   0,
    "coaching":  {},
}


# ── Live UI HTML ──────────────────────────────────────────────────────────────

LIVE_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AC Recorder — AI Race Engineer</title>
<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@400;600;700;800&family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{--red:#E8002D;--teal:#00D2BE;--yellow:#FFD700;--orange:#FF9800;--bg:#050505;--bg2:#0d0d0d;--bg3:#141414;--border:#1e1e1e;--border2:#2a2a2a;--muted:#555;--fd:'Barlow Condensed',sans-serif;--fm:'JetBrains Mono',monospace;}
body{background:var(--bg);color:#fff;font-family:var(--fm);min-height:100vh;overflow-x:hidden}
body::before{content:'';position:fixed;inset:0;background-image:repeating-linear-gradient(45deg,rgba(255,255,255,.012) 0,rgba(255,255,255,.012) 1px,transparent 1px,transparent 8px),repeating-linear-gradient(-45deg,rgba(255,255,255,.012) 0,rgba(255,255,255,.012) 1px,transparent 1px,transparent 8px);pointer-events:none}
.topbar{display:flex;align-items:center;justify-content:space-between;padding:14px 28px;border-bottom:1px solid var(--border);position:relative}
.topbar::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;background:var(--red)}
.brand{display:flex;align-items:center;gap:10px}
.brand-flag{width:3px;height:28px;background:var(--red)}
.brand-name{font-family:var(--fd);font-size:18px;font-weight:800;letter-spacing:4px;text-transform:uppercase}
.brand-sub{font-size:9px;color:var(--muted);letter-spacing:2px;text-transform:uppercase;margin-top:2px}
.session-info{font-size:10px;color:var(--muted);letter-spacing:1px;text-align:right;text-transform:uppercase}
.session-info span{display:block;color:#fff;font-size:11px;margin-top:2px}
.status-bar{display:flex;align-items:center;gap:12px;padding:8px 28px;background:var(--bg2);border-bottom:1px solid var(--border)}
.status-dot{width:8px;height:8px;border-radius:50%;background:var(--muted);flex-shrink:0}
.status-dot.waiting{background:var(--muted)}
.status-dot.recording{background:var(--red);animation:pulse .8s infinite}
.status-dot.done{background:var(--teal)}
.status-dot.sending{background:var(--orange);animation:pulse .5s infinite}
@keyframes pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.3;transform:scale(1.5)}}
.status-text{font-size:11px;letter-spacing:2px;text-transform:uppercase;color:var(--muted)}
.status-text.recording{color:var(--red)}
.status-text.done{color:var(--teal)}
.status-text.sending{color:var(--orange)}
.lap-badge{margin-left:auto;font-family:var(--fd);font-size:13px;font-weight:700;letter-spacing:2px;color:var(--muted)}
.lap-progress{height:2px;background:var(--border)}
.lap-progress-fill{height:100%;background:var(--red);transition:width .4s linear;width:0}
.wrap{padding:16px 20px;max-width:1400px;margin:0 auto}
.main-grid{display:grid;grid-template-columns:1fr 380px;gap:16px;margin-bottom:16px}
.map-card{background:var(--bg2);border:1px solid var(--border);padding:14px;height:100%}
.map-header{font-size:9px;letter-spacing:3px;text-transform:uppercase;color:var(--muted);margin-bottom:10px;display:flex;justify-content:space-between}
.map-container{position:relative;width:fit-content;max-width:100%;margin:0 auto;background:#000;overflow:auto;border:1px solid var(--border)}
#trackImage{display:block;max-width:100%;height:auto;background:#000}
#trackOverlay{position:absolute;inset:0;pointer-events:none}
.map-legend{display:flex;gap:14px;margin-top:8px;flex-wrap:wrap}
.ml-item{display:flex;align-items:center;gap:5px;font-size:10px;color:var(--muted)}
.ml-dot{width:10px;height:3px;border-radius:1px}
.right-col{display:flex;flex-direction:column;gap:12px}
.hero{display:grid;grid-template-columns:1fr 1fr 1fr;gap:2px}
.hero-cell{background:var(--bg2);border:1px solid var(--border);padding:14px 16px;text-align:center}
.hero-label{font-size:9px;letter-spacing:2px;text-transform:uppercase;color:var(--muted);margin-bottom:6px}
.hero-val{font-family:var(--fd);font-size:36px;font-weight:800;letter-spacing:-1px;line-height:1}
.hero-val.time{color:#fff}
.hero-val.speed{color:var(--teal)}
.hero-val.samples{color:var(--yellow)}
.hero-unit{font-size:9px;color:var(--muted);letter-spacing:1px;text-transform:uppercase;margin-top:3px}
.gauges-row{display:grid;grid-template-columns:60px 1fr;gap:10px}
.gear-display{background:var(--bg2);border:1px solid var(--border);padding:10px;text-align:center}
.gear-val{font-family:var(--fd);font-size:52px;font-weight:800;color:var(--yellow);line-height:1}
.gear-lbl{font-size:9px;letter-spacing:2px;text-transform:uppercase;color:var(--muted);margin-top:3px}
.gauges-col{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.gauge-card{background:var(--bg2);border:1px solid var(--border);padding:12px}
.gauge-label{font-size:9px;letter-spacing:2px;text-transform:uppercase;color:var(--muted);margin-bottom:8px;display:flex;justify-content:space-between}
.gauge-label span{color:#fff;font-size:11px;font-weight:700}
.gauge-track{height:5px;background:var(--bg3);overflow:hidden}
.gauge-fill{height:100%;transition:width .1s linear}
.gauge-fill.throttle{background:var(--teal)}
.gauge-fill.brake{background:var(--red)}
.coaching-card{background:var(--bg2);border:1px solid var(--border);overflow:hidden;flex:1}
.coaching-top-bar{height:3px;background:#333;transition:background .2s}
.coaching-top-bar.danger{background:var(--red)}
.coaching-top-bar.warn{background:var(--orange)}
.coaching-top-bar.good{background:var(--teal)}
.coaching-tag{font-size:9px;letter-spacing:3px;text-transform:uppercase;color:var(--muted);padding:12px 16px 0}
.coaching-msg{font-family:var(--fd);font-size:28px;font-weight:800;letter-spacing:1px;line-height:1.1;padding:6px 16px;transition:color .2s}
.coaching-msg.danger{color:var(--red)}
.coaching-msg.warn{color:var(--orange)}
.coaching-msg.good{color:var(--teal)}
.coaching-msg.info{color:#fff}
.coaching-sub{font-size:11px;color:var(--muted);padding:4px 16px 12px;line-height:1.5}
.coaching-speeds{display:grid;grid-template-columns:1fr 1fr 1fr;border-top:1px solid var(--border)}
.cs-cell{padding:10px 14px;border-right:1px solid var(--border)}
.cs-cell:last-child{border-right:none}
.cs-label{font-size:9px;letter-spacing:1px;text-transform:uppercase;color:var(--muted);margin-bottom:3px}
.cs-val{font-family:var(--fd);font-size:20px;font-weight:700}
.corner-ahead{background:var(--bg3);border:1px solid var(--border2);border-left:3px solid var(--yellow);padding:10px 14px;display:none}
.ca-label{font-size:9px;letter-spacing:2px;text-transform:uppercase;color:var(--muted);margin-bottom:3px}
.ca-name{font-family:var(--fd);font-size:18px;font-weight:700;color:var(--yellow);line-height:1}
.ca-detail{font-size:10px;color:var(--muted);margin-top:4px}
.history{background:var(--bg2);border:1px solid var(--border)}
.history-header{padding:10px 16px;font-size:9px;letter-spacing:3px;text-transform:uppercase;color:var(--muted);border-bottom:1px solid var(--border);display:flex;justify-content:space-between}
.history-row{display:grid;grid-template-columns:60px 1fr 1fr 80px;padding:10px 16px;border-bottom:1px solid #111;font-size:11px}
.history-row:last-child{border-bottom:none}
.lap{color:var(--muted)}.ht{color:#fff;font-weight:700}.hs{color:var(--yellow)}
.dl{color:var(--teal);text-decoration:none;font-size:10px;text-align:right}
.dl:hover{color:#fff}
.empty{padding:20px 16px;font-size:11px;color:var(--muted);text-align:center}
.waiting-msg{text-align:center;padding:80px 24px}
.waiting-icon{font-size:48px;margin-bottom:16px;opacity:.3}
.waiting-title{font-family:var(--fd);font-size:24px;font-weight:700;letter-spacing:3px;text-transform:uppercase;color:var(--muted);margin-bottom:8px}
.waiting-sub{font-size:11px;color:#333;letter-spacing:1px}
@media(max-width:900px){.main-grid{grid-template-columns:1fr}}
</style>
</head>
<body>
<div class="topbar">
  <div class="brand">
    <div class="brand-flag"></div>
    <div><div class="brand-name">AC Recorder</div><div class="brand-sub">AI Race Engineer · Yas Marina</div></div>
  </div>
  <div class="session-info" id="sourceInfo">Windows PC · Local Telemetry<span id="relayInfo">Standalone</span></div>
</div>
<div class="status-bar">
  <div class="status-dot" id="dot"></div>
  <div class="status-text" id="statusText">Waiting for lap...</div>
  <div class="lap-badge" id="lapBadge"></div>
</div>
<div class="lap-progress"><div class="lap-progress-fill" id="lapProg"></div></div>

<div class="wrap">
  <div class="waiting-msg" id="waitingMsg">
    <div class="waiting-icon">🏁</div>
    <div class="waiting-title">Standing By</div>
    <div class="waiting-sub" id="waitSub">Cross the start/finish line to begin recording</div>
  </div>

  <div id="recordingUI" style="display:none">
    <div class="main-grid">
      <div class="map-card">
        <div class="map-header">
          <span>Track Position — Live</span>
          <span id="mapDist" style="color:var(--yellow);font-size:11px"></span>
        </div>
        <div class="map-container">
          <img id="trackImage" src="/map.png" alt="Track map">
          <canvas id="trackOverlay"></canvas>
        </div>
        <div class="map-legend">
          <div class="ml-item"><div class="ml-dot" style="background:rgba(255,255,255,0.3)"></div>Track walls</div>
          <div class="ml-item"><div class="ml-dot" style="background:var(--red)"></div>Your path</div>
          <div class="ml-item"><div class="ml-dot" style="background:var(--yellow);border-radius:50%;width:8px;height:8px"></div>S/F line</div>
        </div>
      </div>
      <div class="right-col">
        <div class="hero">
          <div class="hero-cell"><div class="hero-label">Lap Time</div><div class="hero-val time" id="heroTime">0:00.000</div><div class="hero-unit">current</div></div>
          <div class="hero-cell"><div class="hero-label">Speed</div><div class="hero-val speed" id="heroSpeed">0</div><div class="hero-unit">km/h</div></div>
          <div class="hero-cell"><div class="hero-label">Samples</div><div class="hero-val samples" id="heroSamples">0</div><div class="hero-unit">@ 50 Hz</div></div>
        </div>
        <div class="gauges-row">
          <div class="gear-display"><div class="gear-val" id="heroGear">N</div><div class="gear-lbl">Gear</div></div>
          <div class="gauges-col">
            <div class="gauge-card"><div class="gauge-label">Throttle <span id="gThrottle">0%</span></div><div class="gauge-track"><div class="gauge-fill throttle" id="gThrottleFill" style="width:0%"></div></div></div>
            <div class="gauge-card"><div class="gauge-label">Brake <span id="gBrake">0%</span></div><div class="gauge-track"><div class="gauge-fill brake" id="gBrakeFill" style="width:0%"></div></div></div>
          </div>
        </div>
        <div class="corner-ahead" id="cornerAhead">
          <div class="ca-label">⚠ Corner Ahead</div>
          <div class="ca-name" id="caName">—</div>
          <div class="ca-detail" id="caDetail"></div>
        </div>
        <div class="coaching-card">
          <div class="coaching-top-bar info" id="coachBar"></div>
          <div class="coaching-tag">Live Coaching — vs A2RL</div>
          <div class="coaching-msg info" id="coachMsg">Loading reference...</div>
          <div class="coaching-sub" id="coachSub"></div>
          <div class="coaching-speeds">
            <div class="cs-cell"><div class="cs-label">Your Speed</div><div class="cs-val" id="csYou" style="color:#fff">—</div></div>
            <div class="cs-cell"><div class="cs-label">A2RL Speed</div><div class="cs-val" id="csRef" style="color:var(--muted)">—</div></div>
            <div class="cs-cell"><div class="cs-label">Delta</div><div class="cs-val" id="csDelta">—</div></div>
          </div>
        </div>
      </div>
    </div>
    <div class="history">
      <div class="history-header">
        <span>Lap History</span>
        <span id="totalLaps" style="color:var(--yellow)"></span>
      </div>
      <div id="historyBody"><div class="empty">No laps recorded yet — cross the S/F line to begin</div></div>
    </div>
  </div>

  <div id="waitingHistory" style="margin-top:16px">
    <div class="history">
      <div class="history-header">
        <span>Lap History</span>
        <span id="totalLapsWait" style="color:var(--yellow)"></span>
      </div>
      <div id="historyBodyWait"><div class="empty">No laps recorded yet — cross the S/F line to begin</div></div>
    </div>
  </div>
</div>

<script>
let mapCtx = null;
const trackImg = document.getElementById('trackImage');

window.addEventListener('load', () => {
  const mc = document.getElementById('trackOverlay');
  if (mc) { mapCtx = mc.getContext('2d'); }
  if (trackImg) { trackImg.addEventListener('load', () => drawMap()); }
  drawMap();
});

function resizeMapCanvas() {
  if (!mapCtx || !trackImg) return null;
  const c = mapCtx.canvas;
  const rect = trackImg.getBoundingClientRect();
  c.width = rect.width; c.height = rect.height;
  c.style.width = rect.width + 'px'; c.style.height = rect.height + 'px';
  c.style.left = '0px'; c.style.top = '0px';
  return { W: c.width, H: c.height };
}

function drawMap() {
  if (!mapCtx || !trackImg) return;
  const dims = resizeMapCanvas();
  if (!dims) return;
  mapCtx.clearRect(0, 0, dims.W, dims.H);
}
window.addEventListener('resize', drawMap);

function drawLiveOverlay(data) {
  if (!mapCtx || !trackImg) return;
  const dims = resizeMapCanvas();
  if (!dims) return;
  const { W, H } = dims;
  const nat = { w: trackImg.naturalWidth || W, h: trackImg.naturalHeight || H };
  const scaleX = W / nat.w, scaleY = H / nat.h;

  mapCtx.clearRect(0, 0, W, H);

  // Draw path
  const path = data.path || [];
  if (path.length > 1) {
    mapCtx.beginPath();
    mapCtx.strokeStyle = 'rgba(232,0,45,0.8)';
    mapCtx.lineWidth = 2;
    mapCtx.lineJoin = 'round';
    mapCtx.moveTo(path[0][0] * scaleX, path[0][1] * scaleY);
    for (let i = 1; i < path.length; i++) {
      mapCtx.lineTo(path[i][0] * scaleX, path[i][1] * scaleY);
    }
    mapCtx.stroke();
  }

  // Draw car dot
  if (data.pixel_x !== null && data.pixel_y !== null) {
    const cx = data.pixel_x * scaleX, cy = data.pixel_y * scaleY;
    mapCtx.beginPath();
    mapCtx.arc(cx, cy, 6, 0, Math.PI * 2);
    mapCtx.fillStyle = '#FFD700';
    mapCtx.fill();
    mapCtx.strokeStyle = '#000';
    mapCtx.lineWidth = 1.5;
    mapCtx.stroke();
  }
}

function gearLabel(g) {
  if (g === 0) return 'N';
  if (g === -1) return 'R';
  return String(g);
}

function updateCoachingUI(c) {
  if (!c) return;
  const sev = c.severity || 'info';
  const bar = document.getElementById('coachBar');
  const msg = document.getElementById('coachMsg');
  const sub = document.getElementById('coachSub');
  if (bar) { bar.className = 'coaching-top-bar ' + sev; }
  if (msg) { msg.className = 'coaching-msg ' + sev; msg.textContent = c.message || ''; }
  if (sub) { sub.textContent = c.sub || ''; }
  const you = document.getElementById('csYou');
  const ref = document.getElementById('csRef');
  const delta = document.getElementById('csDelta');
  if (you) you.textContent = c.cur_speed ? c.cur_speed + ' km/h' : '—';
  if (ref) ref.textContent = c.ref_speed ? c.ref_speed + ' km/h' : '—';
  if (delta && c.speed_delta !== undefined) {
    const d = c.speed_delta;
    delta.textContent = (d > 0 ? '+' : '') + d + ' km/h';
    delta.style.color = d > 5 ? 'var(--teal)' : d < -12 ? 'var(--red)' : '#fff';
  }
  const ca = document.getElementById('cornerAhead');
  const caName = document.getElementById('caName');
  const caDetail = document.getElementById('caDetail');
  if (ca && c.corner_ahead) {
    ca.style.display = 'block';
    if (caName) caName.textContent = c.corner_ahead.corner_name || '';
    if (caDetail) caDetail.textContent = 'Apex ' + (c.corner_ahead.ref_apex_speed_kmh || 0).toFixed(0) + ' km/h';
  } else if (ca) {
    ca.style.display = 'none';
  }
}

function renderHistory(history, bodyId, totalId) {
  const body = document.getElementById(bodyId);
  const total = document.getElementById(totalId);
  if (!body) return;
  if (!history || history.length === 0) {
    body.innerHTML = '<div class="empty">No laps recorded yet — cross the S/F line to begin</div>';
    if (total) total.textContent = '';
    return;
  }
  if (total) total.textContent = history.length + ' lap' + (history.length !== 1 ? 's' : '');
  body.innerHTML = history.slice().reverse().map(h =>
    '<div class="history-row">' +
    '<span class="lap">LAP ' + h.lap + '</span>' +
    '<span class="ht">' + h.time + '</span>' +
    '<span class="hs">' + h.samples + ' samples</span>' +
    '<a class="dl" href="/download/' + h.lap + '">Download</a>' +
    '</div>'
  ).join('');
}

async function poll() {
  try {
    const r = await fetch('/state');
    const data = await r.json();
    const st = data.status || 'waiting';
    const isRecording = (st === 'recording');

    document.getElementById('waitingMsg').style.display   = isRecording ? 'none' : 'block';
    document.getElementById('recordingUI').style.display  = isRecording ? 'block' : 'none';
    document.getElementById('waitingHistory').style.display = isRecording ? 'none' : 'block';

    const dot  = document.getElementById('dot');
    const stxt = document.getElementById('statusText');
    if (dot)  { dot.className  = 'status-dot '  + st; }
    if (stxt) { stxt.className = 'status-text ' + st; stxt.textContent = st.toUpperCase(); }

    const badge = document.getElementById('lapBadge');
    if (badge && data.lap_num) badge.textContent = 'LAP ' + data.lap_num;

    const relay = data.relay || {};
    const ri = document.getElementById('relayInfo');
    if (ri) {
      ri.textContent = relay.enabled
        ? (relay.sent_ok ? '● Cloud relay active' : '○ Cloud relay error')
        : 'Standalone';
    }

    if (isRecording) {
      document.getElementById('heroTime').textContent    = data.cur_time    || '0:00.000';
      document.getElementById('heroSpeed').textContent   = Math.round(data.speed || 0);
      document.getElementById('heroSamples').textContent = data.samples     || 0;
      document.getElementById('heroGear').textContent    = gearLabel(data.gear || 0);
      const thr = Math.round((data.throttle || 0) * 100);
      const brk = Math.round((data.brake    || 0) * 100);
      document.getElementById('gThrottle').textContent     = thr + '%';
      document.getElementById('gBrake').textContent        = brk + '%';
      document.getElementById('gThrottleFill').style.width = thr + '%';
      document.getElementById('gBrakeFill').style.width    = brk + '%';
      if (data.coaching) updateCoachingUI(data.coaching);
      drawLiveOverlay(data);
      if (data.pixel_x !== null && data.pixel_y !== null) {
        document.getElementById('mapDist').textContent =
          'PX ' + Math.round(data.pixel_x) + ', ' + Math.round(data.pixel_y);
      }
    }

    if (data.history && data.history.length > 0) {
      renderHistory(data.history, 'historyBody',     'totalLaps');
      renderHistory(data.history, 'historyBodyWait', 'totalLapsWait');
    }
  } catch(e) {}
}

setInterval(poll, 250);
poll();
</script>
</body>
</html>"""


# ── AC Shared Memory Structs ──────────────────────────────────────────────────

class SPageFilePhysics(ctypes.Structure):
    _fields_ = [
        ("packetId",            ctypes.c_int),
        ("gas",                 ctypes.c_float),
        ("brake",               ctypes.c_float),
        ("fuel",                ctypes.c_float),
        ("gear",                ctypes.c_int),
        ("rpms",                ctypes.c_int),
        ("steerAngle",          ctypes.c_float),
        ("speedKmh",            ctypes.c_float),
        ("velocity",            ctypes.c_float * 3),
        ("accG",                ctypes.c_float * 3),
        ("wheelSlip",           ctypes.c_float * 4),
        ("wheelLoad",           ctypes.c_float * 4),
        ("wheelsPressure",      ctypes.c_float * 4),
        ("wheelAngularSpeed",   ctypes.c_float * 4),
        ("tyreWear",            ctypes.c_float * 4),
        ("tyreDirtyLevel",      ctypes.c_float * 4),
        ("tyreCoreTemperature", ctypes.c_float * 4),
        ("camberRAD",           ctypes.c_float * 4),
        ("suspensionTravel",    ctypes.c_float * 4),
        ("drs",                 ctypes.c_float),
        ("tc",                  ctypes.c_float),
        ("heading",             ctypes.c_float),
        ("pitch",               ctypes.c_float),
        ("roll",                ctypes.c_float),
        ("cgHeight",            ctypes.c_float),
        ("carDamage",           ctypes.c_float * 5),
        ("numberOfTyresOut",    ctypes.c_int),
        ("pitLimiterOn",        ctypes.c_int),
        ("abs",                 ctypes.c_float),
        ("kersCharge",          ctypes.c_float),
        ("kersInput",           ctypes.c_float),
        ("autoShifterOn",       ctypes.c_int),
        ("rideHeight",          ctypes.c_float * 2),
        ("turboBoost",          ctypes.c_float),
        ("ballast",             ctypes.c_float),
        ("airDensity",          ctypes.c_float),
        ("airTemp",             ctypes.c_float),
        ("roadTemp",            ctypes.c_float),
        ("localAngularVel",     ctypes.c_float * 3),
        ("finalFF",             ctypes.c_float),
        ("performanceMeter",    ctypes.c_float),
        ("engineBrake",         ctypes.c_int),
        ("ersRecoveryLevel",    ctypes.c_int),
        ("ersPowerLevel",       ctypes.c_int),
        ("ersHeatCharging",     ctypes.c_int),
        ("ersCurrentKJ",        ctypes.c_float),
        ("drsAvailable",        ctypes.c_int),
        ("drsEnabled",          ctypes.c_int),
        ("brakeTemp",           ctypes.c_float * 4),
        ("clutch",              ctypes.c_float),
        ("tyreTempI",           ctypes.c_float * 4),
        ("tyreTempM",           ctypes.c_float * 4),
        ("tyreTempO",           ctypes.c_float * 4),
        ("isAIControlled",      ctypes.c_int),
        ("tyreContactPoint",    ctypes.c_float * 12),
        ("tyreContactNormal",   ctypes.c_float * 12),
        ("tyreContactHeading",  ctypes.c_float * 12),
        ("brakeBias",           ctypes.c_float),
        ("localVelocity",       ctypes.c_float * 3),
    ]


class SPageFileGraphic(ctypes.Structure):
    _fields_ = [
        ("packetId",                ctypes.c_int),
        ("status",                  ctypes.c_int),
        ("session",                 ctypes.c_int),
        ("currentTime",             ctypes.c_wchar * 15),
        ("lastTime",                ctypes.c_wchar * 15),
        ("bestTime",                ctypes.c_wchar * 15),
        ("split",                   ctypes.c_wchar * 15),
        ("completedLaps",           ctypes.c_int),
        ("position",                ctypes.c_int),
        ("iCurrentTime",            ctypes.c_int),
        ("iLastTime",               ctypes.c_int),
        ("iBestTime",               ctypes.c_int),
        ("sessionTimeLeft",         ctypes.c_float),
        ("distanceTraveled",        ctypes.c_float),
        ("isInPit",                 ctypes.c_int),
        ("currentSectorIndex",      ctypes.c_int),
        ("lastSectorTime",          ctypes.c_int),
        ("numberOfLaps",            ctypes.c_int),
        ("tyreCompound",            ctypes.c_wchar * 33),
        ("replayTimeMultiplier",    ctypes.c_float),
        ("normalizedCarPosition",   ctypes.c_float),
        ("carCoordinates",          ctypes.c_float * 3),
        ("penaltyTime",             ctypes.c_float),
        ("flag",                    ctypes.c_int),
        ("idealLineOn",             ctypes.c_int),
        ("isInPitLane",             ctypes.c_int),
        ("surfaceGrip",             ctypes.c_float),
        ("mandatoryPitDone",        ctypes.c_int),
        ("windSpeed",               ctypes.c_float),
        ("windDirection",           ctypes.c_float),
        ("isSetupMenuVisible",      ctypes.c_int),
        ("mainDisplayIndex",        ctypes.c_int),
        ("secondaryDisplayIndex",   ctypes.c_int),
        ("tc",                      ctypes.c_int),
        ("tcCut",                   ctypes.c_int),
        ("engineMap",               ctypes.c_int),
        ("abs",                     ctypes.c_int),
        ("fuelXLap",                ctypes.c_float),
        ("rainLights",              ctypes.c_int),
        ("flashingLights",          ctypes.c_int),
        ("lightsStage",             ctypes.c_int),
        ("exhaustTemperature",      ctypes.c_float),
        ("wiperLV",                 ctypes.c_int),
        ("driverStintTotalTimeLeft",ctypes.c_float),
        ("driverStintTimeLeft",     ctypes.c_float),
        ("rainTyres",               ctypes.c_int),
    ]


FIELDS = [
    "LapTimeCurrent", "SpeedKmh", "Throttle", "Brake", "Steering",
    "Gear", "Rpms", "GlobalAccelerationG", "LateralG",
    "CarCoordX", "CarCoordZ",
    "TyreTempFL", "TyreTempFR", "TyreTempRL", "TyreTempRR",
    "BrakeTempFL", "BrakeTempFR", "BrakeTempRL", "BrakeTempRR",
]

lap_csvs = {}

ref_data = {
    "loaded": False,
    "x": [], "y": [], "dist_m": [],
    "speed_kmh": [], "throttle": [], "brake": [],
    "corners": [],
    "total_dist": 0,
    "left_bnd": [],
    "right_bnd": [],
}

coaching_state = {
    "message":     "Waiting for lap...",
    "sub":         "",
    "severity":    "info",
    "corner_ahead": None,
    "ref_speed":   0,
    "cur_speed":   0,
    "speed_delta": 0,
    "dist_m":      0,
    "lap_pct":     0,
}

relay_state = {
    "enabled":      bool(LIVE_PUSH_URL),
    "last_error":   "",
    "last_push_ts": 0.0,
    "sent_ok":      False,
}


# ── Cloud relay (Lovable / Supabase) ─────────────────────────────────────────

def build_public_state_snapshot():
    snapshot = json.loads(json.dumps(state))
    path_pts = snapshot.get("path") or []
    if len(path_pts) > 800:
        snapshot["path"] = path_pts[-800:]
    hist = snapshot.get("history") or []
    if len(hist) > 20:
        snapshot["history"] = hist[-20:]
    snapshot["coaching"] = json.loads(json.dumps(coaching_state))
    snapshot["relay"] = {
        "enabled":    relay_state["enabled"],
        "sent_ok":    relay_state["sent_ok"],
        "last_error": relay_state["last_error"],
        "push_url":   LIVE_PUSH_URL,
    }
    snapshot["updated_at"] = time.time()
    return snapshot


def push_live_state(force=False):
    if not LIVE_PUSH_URL:
        return False
    now = time.time()
    min_interval = 1.0 / max(LIVE_PUSH_HZ, 0.5)
    if not force and now - relay_state["last_push_ts"] < min_interval:
        return False
    headers = {"Content-Type": "application/json"}
    if LIVE_PUSH_TOKEN:
        headers["Authorization"] = f"Bearer {LIVE_PUSH_TOKEN}"
    payload = build_public_state_snapshot()
    try:
        resp = requests.post(LIVE_PUSH_URL, json=payload, headers=headers, timeout=2.5)
        relay_state["last_push_ts"] = now
        relay_state["sent_ok"] = 200 <= resp.status_code < 300
        relay_state["last_error"] = "" if relay_state["sent_ok"] else f"HTTP {resp.status_code}: {resp.text[:200]}"
        return relay_state["sent_ok"]
    except Exception as e:
        relay_state["last_push_ts"] = now
        relay_state["sent_ok"] = False
        relay_state["last_error"] = str(e)
        return False


relay_stop_event = threading.Event()
relay_thread = None


def relay_worker():
    interval = max(0.4, 1.0 / max(LIVE_PUSH_HZ, 0.5))
    consecutive_errors = 0
    while not relay_stop_event.is_set():
        try:
            if LIVE_PUSH_URL:
                push_live_state(force=True)
            consecutive_errors = 0
        except Exception:
            consecutive_errors += 1
            if consecutive_errors > 10:
                relay_stop_event.wait(min(interval * 3, 5.0))
                continue
        relay_stop_event.wait(interval)


def start_relay_worker():
    global relay_thread
    if not LIVE_PUSH_URL:
        return
    if relay_thread is not None and not relay_thread.is_alive():
        print("  Relay thread died — restarting...")
        relay_thread = None
    if relay_thread is not None:
        return
    relay_stop_event.clear()
    relay_thread = threading.Thread(target=relay_worker, daemon=True, name="ac-relay")
    relay_thread.start()


def _relay_watchdog():
    while True:
        time.sleep(10)
        if LIVE_PUSH_URL and not relay_stop_event.is_set():
            start_relay_worker()


def stop_relay_worker():
    relay_stop_event.set()


# ── Local reference lap (for coaching) ───────────────────────────────────────

def _candidate_reference_paths():
    candidates = []
    if LOCAL_REFERENCE_JSON:
        candidates.append(Path(LOCAL_REFERENCE_JSON))
    script_dir = Path(__file__).resolve().parent
    candidates.extend([
        script_dir / "output" / "fast_laps.json",
        script_dir / "fast_laps.json",
        script_dir / "reference_lap.json",
        script_dir / "reference.json",
    ])
    seen = []
    for p in candidates:
        if p and p not in seen:
            seen.append(p)
    return seen


def _candidate_boundaries_paths():
    candidates = []
    if LOCAL_BOUNDARIES_JSON:
        candidates.append(Path(LOCAL_BOUNDARIES_JSON))
    script_dir = Path(__file__).resolve().parent
    candidates.extend([
        script_dir / "output" / "boundaries.json",
        script_dir / "boundaries.json",
        script_dir / "track_boundaries.json",
    ])
    seen = []
    for p in candidates:
        if p and p not in seen:
            seen.append(p)
    return seen


def load_local_boundaries():
    for path in _candidate_boundaries_paths():
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            left  = data.get("left_bnd",  [])
            right = data.get("right_bnd", [])
            if left or right:
                ref_data["left_bnd"]  = left
                ref_data["right_bnd"] = right
                print(f"  Local boundaries loaded: {path.name} ({len(left)} left, {len(right)} right)")
                return True
        except Exception as e:
            print(f"  Failed to read boundaries from {path}: {e}")
    return False


def load_local_reference():
    load_local_boundaries()
    for path in _candidate_reference_paths():
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            ref_data["x"]          = data["x"]
            ref_data["y"]          = data["y"]
            ref_data["dist_m"]     = data["dist_m"]
            ref_data["speed_kmh"]  = data["speed_kmh"]
            ref_data["throttle"]   = data["throttle"]
            ref_data["brake"]      = data["brake"]
            ref_data["corners"]    = data.get("corners", [])
            ref_data["total_dist"] = data.get("total_dist", 3425)
            if data.get("left_bnd"):  ref_data["left_bnd"]  = data["left_bnd"]
            if data.get("right_bnd"): ref_data["right_bnd"] = data["right_bnd"]
            ref_data["loaded"] = True
            print(f"  Local reference loaded: {path.name} ({len(ref_data['x'])} points, {len(ref_data['corners'])} corners)")
            return True
        except Exception as e:
            print(f"  Failed to read reference from {path}: {e}")
    if ref_data["left_bnd"] or ref_data["right_bnd"]:
        print("  Boundaries available, but no local reference JSON was found")
        return True
    return False


_last_ref_i = 0


def find_nearest_ref(car_x, car_z):
    global _last_ref_i
    if not ref_data["loaded"] or not ref_data["x"]:
        return -1
    xs, ys = ref_data["x"], ref_data["y"]
    n = len(xs)
    window = 100
    start = max(0, _last_ref_i - 10)
    end   = min(n, _last_ref_i + window)
    best_i, best_d = start, float("inf")
    for i in range(start, end):
        d = (xs[i] - car_x) ** 2 + (ys[i] - car_z) ** 2
        if d < best_d:
            best_d, best_i = d, i
    if _last_ref_i > n - window:
        for i in range(0, min(window, n)):
            d = (xs[i] - car_x) ** 2 + (ys[i] - car_z) ** 2
            if d < best_d:
                best_d, best_i = d, i
    _last_ref_i = best_i
    return best_i


def update_coaching(car_x, car_z, speed_kmh, throttle, brake):
    if not ref_data["loaded"] or not ref_data.get("x"):
        return
    ref_i = find_nearest_ref(car_x, car_z)
    if ref_i < 0:
        return
    ref_speed    = ref_data["speed_kmh"][ref_i]
    ref_throttle = ref_data["throttle"][ref_i]
    ref_brake    = ref_data["brake"][ref_i]
    cur_dist     = ref_data["dist_m"][ref_i]
    total_dist   = ref_data["total_dist"]
    speed_delta  = speed_kmh - ref_speed

    coaching_state["ref_speed"]   = round(ref_speed, 1)
    coaching_state["cur_speed"]   = round(speed_kmh, 1)
    coaching_state["speed_delta"] = round(speed_delta, 1)
    coaching_state["dist_m"]      = round(cur_dist, 0)
    coaching_state["lap_pct"]     = round(cur_dist / total_dist * 100, 1) if total_dist else 0
    coaching_state["ref_gps_x"]   = round(ref_data["x"][ref_i], 2)
    coaching_state["ref_gps_y"]   = round(ref_data["y"][ref_i], 2)

    next_corner = None
    for c in ref_data["corners"]:
        dtc = c["dist_m"] - cur_dist
        if 10 < dtc < 400:
            next_corner = c
            break
    coaching_state["corner_ahead"] = next_corner

    msg, sub, sev = "On pace", f"{speed_kmh:.0f} vs {ref_speed:.0f} km/h", "info"

    if ref_brake > 0.5 and brake < 0.1:
        msg = f"BRAKE NOW — {ref_speed:.0f}→{ref_speed*0.6:.0f} km/h"
        sub = f"Reference braking hard here — {speed_kmh:.0f} km/h entry"
        sev = "danger"
    elif next_corner:
        name = next_corner["corner_name"]
        dist_to_brake = next_corner.get("ref_brake_point_m", next_corner["dist_m"] - 80) - cur_dist
        ref_apex  = next_corner.get("ref_apex_speed_kmh", 0)
        ref_entry = next_corner.get("ref_entry_speed_kmh", 0)
        if dist_to_brake <= 0 and brake < 0.1:
            msg = f"BRAKE NOW — {name}"
            sub = f"Target apex {ref_apex:.0f} km/h — entry was {ref_entry:.0f} km/h"
            sev = "danger"
        elif 0 < dist_to_brake <= 30:
            msg = f"BRAKE IN {dist_to_brake:.0f}m — {name}"
            sub = f"Target {ref_apex:.0f} km/h apex · Entry {ref_entry:.0f} km/h"
            sev = "danger"
        elif 30 < dist_to_brake <= 80:
            msg = f"Prepare to brake — {name} in {next_corner['dist_m']-cur_dist:.0f}m"
            sub = f"Brake point {dist_to_brake:.0f}m ahead · Apex target {ref_apex:.0f} km/h"
            sev = "warn"
        elif dist_to_brake > 80:
            msg = f"{name} in {next_corner['dist_m']-cur_dist:.0f}m"
            sub = f"Brake at {next_corner.get('ref_brake_point_m',0):.0f}m · Apex {ref_apex:.0f} km/h"
            sev = "info"
    elif ref_throttle > 0.85 and throttle < 0.5 and speed_kmh > 60:
        gap = (ref_throttle - throttle) * 100
        msg = f"MORE THROTTLE — {throttle*100:.0f}% vs {ref_throttle*100:.0f}%"
        sub = f"A2RL is at full throttle here — you are {gap:.0f}% behind"
        sev = "warn"
    elif speed_delta < -25:
        msg = f"−{abs(speed_delta):.0f} km/h DEFICIT"
        sub = f"You: {speed_kmh:.0f}  A2RL: {ref_speed:.0f} — carry more speed"
        sev = "danger"
    elif speed_delta < -12:
        msg = f"−{abs(speed_delta):.0f} km/h — carry more speed"
        sub = f"You: {speed_kmh:.0f}  A2RL: {ref_speed:.0f} km/h"
        sev = "warn"
    elif speed_delta > 15:
        msg = f"+{speed_delta:.0f} km/h vs A2RL"
        sub = f"Faster than autonomous car here — {speed_kmh:.0f} vs {ref_speed:.0f} km/h"
        sev = "good"
    elif speed_delta > 5:
        msg = "On pace +"
        sub = f"{speed_kmh:.0f} vs {ref_speed:.0f} km/h — ahead of A2RL"
        sev = "good"

    coaching_state["message"]  = msg
    coaching_state["sub"]      = sub
    coaching_state["severity"] = sev


# ── Map helpers ───────────────────────────────────────────────────────────────

def load_map_ini(path: Path):
    if not path.exists():
        raise FileNotFoundError(f"map.ini not found: {path}")
    parser = ConfigParser()
    parser.read(path, encoding="utf-8")
    p = parser["PARAMETERS"]
    return {
        "width":        float(p.get("WIDTH",        "0")),
        "height":       float(p.get("HEIGHT",       "0")),
        "margin":       float(p.get("MARGIN",       "0")),
        "scale_factor": float(p.get("SCALE_FACTOR", "1")),
        "x_offset":     float(p.get("X_OFFSET",     "0")),
        "z_offset":     float(p.get("Z_OFFSET",     "0")),
    }


def world_to_pixel(x: float, z: float, mp: dict):
    if not mp:
        return x, z
    scale = mp.get("scale_factor", 1.0) or 1.0
    px = (x + mp.get("x_offset", 0.0)) / scale
    py = (z + mp.get("z_offset", 0.0)) / scale
    return px, py


# ── Telemetry helpers ─────────────────────────────────────────────────────────

def take_sample(p, g):
    return {
        "LapTimeCurrent":      g.iCurrentTime,
        "SpeedKmh":            round(p.speedKmh, 2),
        "Throttle":            round(p.gas, 4),
        "Brake":               round(p.brake, 4),
        "Steering":            round(p.steerAngle, 4),
        "Gear":                p.gear,
        "Rpms":                p.rpms,
        "GlobalAccelerationG": round(p.accG[2], 4),
        "LateralG":            round(p.accG[0], 4),
        "CarCoordX":           round(g.carCoordinates[0], 3),
        "CarCoordZ":           round(g.carCoordinates[2], 3),
        "TyreTempFL":          round(p.tyreCoreTemperature[0], 1),
        "TyreTempFR":          round(p.tyreCoreTemperature[1], 1),
        "TyreTempRL":          round(p.tyreCoreTemperature[2], 1),
        "TyreTempRR":          round(p.tyreCoreTemperature[3], 1),
        "BrakeTempFL":         round(p.brakeTemp[0], 1),
        "BrakeTempFR":         round(p.brakeTemp[1], 1),
        "BrakeTempRL":         round(p.brakeTemp[2], 1),
        "BrakeTempRR":         round(p.brakeTemp[3], 1),
    }


def to_csv(records):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=FIELDS, delimiter=",")
    w.writeheader()
    w.writerows(records)
    return buf.getvalue()


def fmt(ms):
    if ms <= 0: return "0:00.000"
    m = ms // 60000
    s = (ms % 60000) / 1000
    return f"{m}:{s:06.3f}"


def save_to_desktop(csv_text, lap_num):
    desktop = Path.home() / "Desktop"
    desktop.mkdir(exist_ok=True)
    ts   = datetime.now().strftime("%H%M%S")
    path = desktop / f"ac_lap{lap_num}_{ts}.csv"
    path.write_text(csv_text, encoding="utf-8")
    return str(path)


# ── Mini HTTP server for live UI ──────────────────────────────────────────────

class UIHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # suppress access logs

    def do_OPTIONS(self):
        self.send_response(204)
        self._send_cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, "text/html", LIVE_HTML.encode())

        elif self.path == "/state":
            self._send(200, "application/json",
                       json.dumps(build_public_state_snapshot()).encode())

        elif self.path == "/health":
            payload = json.dumps({
                "ok":           True,
                "status":       state.get("status"),
                "relay_enabled": relay_state["enabled"],
                "relay_ok":     relay_state["sent_ok"],
                "relay_error":  relay_state["last_error"],
            }).encode()
            self._send(200, "application/json", payload)

        elif self.path == "/ref_map":
            has_bnd = len(ref_data.get("left_bnd", [])) > 0
            has_ref = len(ref_data.get("x",        [])) > 0
            payload = json.dumps({
                "ok":        has_bnd or has_ref,
                "x":         ref_data.get("x",         []),
                "y":         ref_data.get("y",         []),
                "left_bnd":  ref_data.get("left_bnd",  []),
                "right_bnd": ref_data.get("right_bnd", []),
            }).encode()
            self._send(200, "application/json", payload)

        elif self.path == "/map.png":
            if MAP_PNG.exists():
                self._send(200, "image/png", MAP_PNG.read_bytes())
            else:
                self._send(404, "text/plain", b"map.png not found")

        elif self.path.startswith("/download/"):
            lap_n = self.path.split("/")[-1]
            try:
                n = int(lap_n)
                if n in lap_csvs:
                    self._send(200, "text/csv",
                               lap_csvs[n].encode("utf-8"),
                               f'attachment; filename="ac_lap{n}.csv"')
                else:
                    self._send(404, "text/plain", b"Lap not found")
            except ValueError:
                self._send(400, "text/plain", b"Invalid lap number")
        else:
            self._send(404, "text/plain", b"Not found")

    def _send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin",  CORS_ALLOW_ORIGIN)
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")

    def _send(self, code, ctype, body, disposition=None):
        self.send_response(code)
        self._send_cors_headers()
        self.send_header("Content-Type",   ctype)
        self.send_header("Content-Length", str(len(body)))
        if disposition:
            self.send_header("Content-Disposition", disposition)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def start_ui_server(port=9000):
    import socket
    test = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    test.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        test.bind(("0.0.0.0", port))
        test.close()
    except OSError:
        raise OSError(f"Port {port} already in use. Close other apps using it.")
    server = HTTPServer(("0.0.0.0", port), UIHandler)
    server.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


# ── Main recorder ─────────────────────────────────────────────────────────────

def main():
    global UI_PORT
    print("=" * 55)
    print("  AI Race Engineer — AC Lap Recorder")
    print("=" * 55)

    if sys.platform != "win32":
        print("ERROR: Must run on Windows where Assetto Corsa is installed.")
        sys.exit(1)

    if ENABLE_LOCAL_UI:
        if not MAP_PNG.exists():
            print(f"ERROR: Track map not found: {MAP_PNG}")
            print("Set ENABLE_LOCAL_UI=0 if you only need the Cloud relay.")
            sys.exit(1)
        if not MAP_INI.exists():
            print(f"ERROR: Track map.ini not found: {MAP_INI}")
            print("Set ENABLE_LOCAL_UI=0 if you only need the Cloud relay.")
            sys.exit(1)
    else:
        if not MAP_INI.exists():
            print("  WARNING: map.ini not found — pixel coordinates will be raw world coords")

    print()
    if MAP_INI.exists():
        state["map"] = load_map_ini(MAP_INI)
    else:
        state["map"] = {}

    if ENABLE_LOCAL_UI:
        UI_PORT = 9000
        try:
            start_ui_server(UI_PORT)
            print(f"Live UI started on port {UI_PORT}")
        except Exception as e:
            print(f"WARNING: Could not start UI server: {e}")
            UI_PORT = None

        if UI_PORT and UI_AUTO_OPEN:
            import webbrowser
            webbrowser.open(f"http://localhost:{UI_PORT}")
            print(f"Opening browser at http://localhost:{UI_PORT}")
        elif UI_PORT:
            print(f"Local UI available at http://localhost:{UI_PORT}")
    else:
        UI_PORT = None
        print("Local UI disabled")

    if LIVE_PUSH_URL:
        relay_state["enabled"] = True
        print(f"Live relay enabled -> {LIVE_PUSH_URL}")
        if LIVE_PUSH_TOKEN:
            print("Live relay auth token configured")
        print(f"Live relay rate: {LIVE_PUSH_HZ:.1f} Hz")
        start_relay_worker()
        _wd = threading.Thread(target=_relay_watchdog, daemon=True, name="ac-relay-watchdog")
        _wd.start()
    else:
        print("Live relay disabled (set LIVE_PUSH_URL to enable)")

    print("Checking local reference files...", end="", flush=True)
    if load_local_reference():
        print(" Ready ✓")
    else:
        print(" None found (coaching inactive)")

    print("\nConnecting to Assetto Corsa...", end="", flush=True)
    try:
        phys_mm  = mmap.mmap(-1, ctypes.sizeof(SPageFilePhysics),  "Local\\acpmf_physics")
        graph_mm = mmap.mmap(-1, ctypes.sizeof(SPageFileGraphic), "Local\\acpmf_graphics")
        print(" Connected ✓")
    except Exception as e:
        print(f"\nERROR: {e}")
        print("Make sure Assetto Corsa is running and you are in a session.")
        input("Press ENTER to exit...")
        stop_relay_worker()
        sys.exit(1)

    def read_p():
        phys_mm.seek(0)
        return SPageFilePhysics.from_buffer_copy(
            phys_mm.read(ctypes.sizeof(SPageFilePhysics)))

    def read_g():
        graph_mm.seek(0)
        return SPageFileGraphic.from_buffer_copy(
            graph_mm.read(ctypes.sizeof(SPageFileGraphic)))

    # ── Wait for a live AC session (status == 2) ──────────────────────────────
    # This is the same strict check used in the original working recorder.
    # Without it the shared memory returns all-zeros and lap detection never fires.
    print("Waiting for active AC session (load into a car on track)", end="", flush=True)
    while True:
        gw = read_g()
        if gw.status == 2:
            print(f" Ready! [status={gw.status}]")
            break
        if LIVE_PUSH_URL:
            push_live_state(force=True)
        time.sleep(0.5)
        print(".", end="", flush=True)

    if UI_PORT:
        print(f"UI: http://localhost:{UI_PORT}")
    print("Drive a lap — recording starts automatically at S/F line\n")

    lap_num       = 0
    last_path_x   = None
    last_path_z   = None
    last_heading  = 0.0
    min_world_step = 0.5
    auto_start_next = False

    # ── Per-lap outer loop ────────────────────────────────────────────────────
    while True:
        lap_num += 1
        state["status"]    = "waiting"
        state["lap_num"]   = lap_num
        state["samples"]   = 0
        state["lap_time"]  = None
        state["cur_time"]  = "0:00.000"
        state["path"]      = []
        state["pixel_x"]   = None
        state["pixel_y"]   = None
        state["heading_rad"] = 0.0
        state["coaching"]  = coaching_state.copy()
        push_live_state(force=True) if LIVE_PUSH_URL else None
        last_path_x  = None
        last_path_z  = None
        last_heading = 0.0

        print(f"{'─'*55}")
        print(f"  LAP {lap_num} — Waiting for S/F line...")

        if not ref_data["loaded"]:
            print("  Retrying local reference load...", end="", flush=True)
            if load_local_reference():
                print(" Loaded ✓")
            else:
                print(" Still not available")

        records   = []
        recording = False
        prev_laps = read_g().completedLaps
        prev_t    = 0
        last_tick = 0.0
        INTERVAL  = 0.02     # 50 Hz sample rate

        g_init    = read_g()
        in_pit    = (g_init.isInPit == 1 or g_init.isInPitLane == 1)
        first_lap = (g_init.completedLaps == 0)
        pit_start = first_lap or in_pit

        # If previous lap just ended by crossing S/F, start recording immediately
        if auto_start_next:
            recording = True
            records   = []
            state["status"] = "recording"
            auto_start_next = False
            print(f"  ● Recording started (auto — continued from lap {lap_num - 1})")
        elif pit_start:
            reason = "first lap of session" if first_lap else "car detected in pit lane"
            print(f"  Pit start detected ({reason}) — recording when car moves")
            state["status"] = "waiting"
        else:
            print("  Track start — waiting for S/F line crossing")

        # ── Inner polling loop (one lap) ──────────────────────────────────────
        try:
            while True:
                now = time.perf_counter()
                if now - last_tick < INTERVAL:
                    time.sleep(0.002)
                    continue
                last_tick = now

                g = read_g()
                p = read_p()
                cur_t = g.iCurrentTime

                # ── Recording trigger ─────────────────────────────────────────
                # Exactly matches the original working recorder:
                #   pit start  → begin when car starts moving (speed > 5 km/h)
                #   track start → begin on clean S/F timer rollover only
                if not recording:
                    if pit_start:
                        if p.speedKmh > 5 and cur_t > 0:
                            recording = True
                            records   = []
                            state["status"] = "recording"
                            print("  ● Recording started (pit start)")
                    else:
                        # S/F crossing: timer resets from a large value to near-zero
                        if cur_t > 0 and cur_t < 3000 and prev_t > 5000:
                            recording = True
                            records   = []
                            state["status"] = "recording"
                            print("  ● Recording started (S/F crossing)")
                    if cur_t > 0:
                        prev_t = cur_t

                # ── Sample + state update (only while recording) ───────────────
                if recording:
                    records.append(take_sample(p, g))

                    state["samples"]  = len(records)
                    state["speed"]    = round(p.speedKmh, 1)
                    state["gear"]     = p.gear
                    rec_t = cur_t - records[0]["LapTimeCurrent"] if records else 0
                    state["cur_time"] = fmt(max(0, rec_t))
                    state["throttle"] = round(p.gas,   3)
                    state["brake"]    = round(p.brake, 3)

                    cx = float(g.carCoordinates[0])
                    cz = float(g.carCoordinates[2])
                    state["connected"]       = (g.status == 2)
                    state["car_x"]           = round(cx, 2)
                    state["car_z"]           = round(cz, 2)
                    state["completed_laps"]  = int(g.completedLaps)
                    state["current_time_ms"] = int(g.iCurrentTime)

                    px, py = world_to_pixel(cx, cz, state["map"])
                    state["pixel_x"] = round(px, 2)
                    state["pixel_y"] = round(py, 2)

                    if last_path_x is not None:
                        dx = cx - last_path_x
                        dz = cz - last_path_z
                        if abs(dx) + abs(dz) > 1e-6:
                            last_heading = math.atan2(dz, dx)
                    state["heading_rad"] = last_heading

                    should_add = (last_path_x is None or
                                  abs(cx - last_path_x) > min_world_step or
                                  abs(cz - last_path_z) > min_world_step)
                    if should_add:
                        state["path"].append([round(px, 2), round(py, 2)])
                        if len(state["path"]) > MAX_PATH_POINTS:
                            state["path"] = state["path"][-MAX_PATH_POINTS:]
                        last_path_x = cx
                        last_path_z = cz

                    if len(records) % 5 == 0 and ref_data["loaded"]:
                        update_coaching(cx, cz, p.speedKmh, p.gas, p.brake)
                        state["coaching"] = coaching_state.copy()

                    if len(records) % 100 == 0:
                        print(f"\r  ● {fmt(cur_t)}  {p.speedKmh:.0f} km/h  "
                              f"G{p.gear}  {len(records)} samples   ",
                              end="", flush=True)

                    # ── Lap complete: completedLaps counter incremented ────────
                    if g.completedLaps > prev_laps and len(records) > 100:
                        lap_ms       = g.iLastTime if g.iLastTime > 0 else cur_t
                        lap_time_str = fmt(lap_ms)
                        print(f"\r  ■ LAP {lap_num} DONE — {lap_time_str}  "
                              f"({len(records)} samples)          ")
                        state["status"]   = "sending"
                        state["lap_time"] = lap_time_str
                        state["cur_time"] = lap_time_str
                        state["speed"]    = 0
                        state["gear"]     = 0
                        state["throttle"] = 0.0
                        state["brake"]    = 0.0
                        push_live_state(force=True) if LIVE_PUSH_URL else None
                        break

                    # ── Fallback: timer reset without completedLaps increment ──
                    if len(records) > 300 and cur_t < 1000:
                        if records[-1]["LapTimeCurrent"] > 20000:
                            lap_ms       = records[-1]["LapTimeCurrent"]
                            lap_time_str = fmt(lap_ms)
                            print(f"\r  ■ LAP {lap_num} DONE — {lap_time_str}  "
                                  f"({len(records)} samples)          ")
                            state["status"]   = "sending"
                            state["lap_time"] = lap_time_str
                            state["cur_time"] = lap_time_str
                            state["speed"]    = 0
                            state["gear"]     = 0
                            state["throttle"] = 0.0
                            state["brake"]    = 0.0
                            pit_start = False
                            push_live_state(force=True) if LIVE_PUSH_URL else None
                            break

                prev_laps = g.completedLaps

        except KeyboardInterrupt:
            print("\nStopped.")
            break

        # ── Post-lap: save CSV + push to cloud ───────────────────────────────
        if records:
            csv_text = to_csv(records)
            lap_csvs[lap_num] = csv_text
            local = save_to_desktop(csv_text, lap_num)
            print(f"  Saved: {Path(local).name}")

            lap_entry = {
                "lap":     lap_num,
                "time":    state["lap_time"] or "?",
                "samples": len(records),
            }
            state["history"].append(lap_entry)
            state["completed_lap"] = lap_entry
            state["status"] = "sending"

            # Push final lap state to cloud (async so recording can restart immediately)
            if LIVE_PUSH_URL:
                def _push_final(force=True):
                    push_live_state(force=True)
                threading.Thread(target=_push_final, daemon=True).start()

            state.pop("completed_lap", None)
            state["status"] = "waiting"
            push_live_state(force=True) if LIVE_PUSH_URL else None
        else:
            state["status"] = "waiting"
            push_live_state(force=True) if LIVE_PUSH_URL else None

        print(f"  Starting lap {lap_num + 1} recording immediately...")
        auto_start_next = True  # skip S/F detection for next lap — we just crossed it

    # ── Cleanup ───────────────────────────────────────────────────────────────
    stop_relay_worker()
    phys_mm.close()
    graph_mm.close()
    print("\nDone.")
    try:
        input("Press ENTER to exit...")
    except EOFError:
        pass


if __name__ == "__main__":
    main()