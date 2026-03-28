"""
ac_recorder.py — AI Race Engineer · Lap Recorder
Run on your Windows PC while Assetto Corsa is open.

1. Start AC, load Yas Marina North
2. Run: python ac_recorder.py
3. Enter Mac IP once
4. Open http://localhost:9000 in your browser on the PC
5. Drive — recording starts when you cross S/F line
6. Data sends to Mac automatically, CSV saved to Desktop
"""

import ctypes, mmap, csv, time, sys, os, io, json, threading
from datetime import datetime
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

try:
    import requests
except ImportError:
    os.system("pip install requests -q")
    import requests


# ── Shared state (updated by recorder, read by web UI) ────────────────────────
state = {
    "status":    "waiting",   # waiting | recording | done | sending
    "lap_num":   0,
    "lap_time":  None,        # formatted string
    "samples":   0,
    "speed":     0,
    "gear":      0,
    "cur_time":  "0:00.000",
    "throttle":  0,
    "brake":     0,
    "mac_ip":    "",
    "mac_port":  8080,
    "history":   [],          # list of {lap, time, samples}
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

/* Main layout: map left, coaching right */
.wrap{padding:16px 20px;max-width:1400px;margin:0 auto}
.main-grid{display:grid;grid-template-columns:1fr 380px;gap:16px;margin-bottom:16px}

/* Track map */
.map-card{background:var(--bg2);border:1px solid var(--border);padding:14px;height:100%}
.map-header{font-size:9px;letter-spacing:3px;text-transform:uppercase;color:var(--muted);margin-bottom:10px;display:flex;justify-content:space-between}
.map-container{position:relative;width:100%;height:340px;background:#0a0a0a}
.map-container canvas{display:block;width:100%!important;height:100%!important}
.map-legend{display:flex;gap:14px;margin-top:8px;flex-wrap:wrap}
.ml-item{display:flex;align-items:center;gap:5px;font-size:10px;color:var(--muted)}
.ml-dot{width:10px;height:3px;border-radius:1px}

/* Right column */
.right-col{display:flex;flex-direction:column;gap:12px}

/* Hero stats */
.hero{display:grid;grid-template-columns:1fr 1fr 1fr;gap:2px}
.hero-cell{background:var(--bg2);border:1px solid var(--border);padding:14px 16px;text-align:center}
.hero-label{font-size:9px;letter-spacing:2px;text-transform:uppercase;color:var(--muted);margin-bottom:6px}
.hero-val{font-family:var(--fd);font-size:36px;font-weight:800;letter-spacing:-1px;line-height:1}
.hero-val.time{color:#fff}
.hero-val.speed{color:var(--teal)}
.hero-val.samples{color:var(--yellow)}
.hero-unit{font-size:9px;color:var(--muted);letter-spacing:1px;text-transform:uppercase;margin-top:3px}

/* Gauges */
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

/* LIVE COACHING PANEL */
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

/* Corner ahead */
.corner-ahead{background:var(--bg3);border:1px solid var(--border2);border-left:3px solid var(--yellow);padding:10px 14px;display:none}
.ca-label{font-size:9px;letter-spacing:2px;text-transform:uppercase;color:var(--muted);margin-bottom:3px}
.ca-name{font-family:var(--fd);font-size:18px;font-weight:700;color:var(--yellow);line-height:1}
.ca-detail{font-size:10px;color:var(--muted);margin-top:4px}

/* Lap history */
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
  <div class="session-info" id="macInfo">Windows PC · Live Telemetry<span id="macLink">—</span></div>
</div>
<div class="status-bar">
  <div class="status-dot" id="dot"></div>
  <div class="status-text" id="statusText">Waiting for lap...</div>
  <div class="lap-badge" id="lapBadge"></div>
</div>
<div class="lap-progress"><div class="lap-progress-fill" id="lapProg"></div></div>

<div class="wrap">

  <!-- WAITING STATE -->
  <div class="waiting-msg" id="waitingMsg">
    <div class="waiting-icon">🏁</div>
    <div class="waiting-title">Standing By</div>
    <div class="waiting-sub" id="waitSub">Cross the start/finish line to begin recording</div>
  </div>

  <!-- RECORDING STATE -->
  <div id="recordingUI" style="display:none">
    <div class="main-grid">

      <!-- LEFT: Track map -->
      <div class="map-card">
        <div class="map-header">
          <span>Track Position — Live</span>
          <span id="mapDist" style="color:var(--yellow);font-size:11px"></span>
        </div>
        <div class="map-container">
          <canvas id="trackMap"></canvas>
        </div>
        <div class="map-legend">
          <div class="ml-item"><div class="ml-dot" style="background:rgba(255,255,255,0.3)"></div>Track walls</div>
          <div class="ml-item"><div class="ml-dot" style="background:var(--red)"></div>Your path</div>
          <div class="ml-item"><div class="ml-dot" style="background:var(--yellow);border-radius:50%;width:8px;height:8px"></div>S/F line</div>
        </div>
      </div>

      <!-- RIGHT: Coaching + Stats -->
      <div class="right-col">

        <!-- Hero stats -->
        <div class="hero">
          <div class="hero-cell"><div class="hero-label">Lap Time</div><div class="hero-val time" id="heroTime">0:00.000</div><div class="hero-unit">current</div></div>
          <div class="hero-cell"><div class="hero-label">Speed</div><div class="hero-val speed" id="heroSpeed">0</div><div class="hero-unit">km/h</div></div>
          <div class="hero-cell"><div class="hero-label">Samples</div><div class="hero-val samples" id="heroSamples">0</div><div class="hero-unit">@ 50 Hz</div></div>
        </div>

        <!-- Gear + Gauges -->
        <div class="gauges-row">
          <div class="gear-display"><div class="gear-val" id="heroGear">N</div><div class="gear-lbl">Gear</div></div>
          <div class="gauges-col">
            <div class="gauge-card"><div class="gauge-label">Throttle <span id="gThrottle">0%</span></div><div class="gauge-track"><div class="gauge-fill throttle" id="gThrottleFill" style="width:0%"></div></div></div>
            <div class="gauge-card"><div class="gauge-label">Brake <span id="gBrake">0%</span></div><div class="gauge-track"><div class="gauge-fill brake" id="gBrakeFill" style="width:0%"></div></div></div>
          </div>
        </div>

        <!-- Corner ahead -->
        <div class="corner-ahead" id="cornerAhead">
          <div class="ca-label">⚠ Corner Ahead</div>
          <div class="ca-name" id="caName">—</div>
          <div class="ca-detail" id="caDetail"></div>
        </div>

        <!-- Live coaching -->
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

    <!-- Lap history below -->
    <div class="history">
      <div class="history-header">
        <span>Lap History</span>
        <span id="totalLaps" style="color:var(--yellow)"></span>
      </div>
      <div id="historyBody"><div class="empty">No laps recorded yet — cross the S/F line to begin</div></div>
    </div>

  </div>

  <!-- Waiting lap history -->
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
const sampleHistory = [];
let chartCtx = null;
let mapCtx = null;
let refMapData = null;
let mapBounds = null;
let pathHistory = [];
let _lastCarX = null, _lastCarZ = null;

window.addEventListener('load', () => {
  const mc = document.getElementById('trackMap');
  if (mc) { mapCtx = mc.getContext('2d'); }
  loadRefMap();
  const sc = document.getElementById('samplesChart');
  if (sc) chartCtx = sc.getContext('2d');
});

// ── Reference map loading ─────────────────────────────────────────────────────
async function loadRefMap() {
  try {
    const res = await fetch('/ref_map');
    const d   = await res.json();
    if (d.ok && d.x && d.x.length > 0) {
      refMapData = d;
      computeBounds();
      drawMap(null);
      document.getElementById('coachMsg').textContent = 'Reference loaded — drive!';
    } else {
      setTimeout(loadRefMap, 3000);
    }
  } catch(e) { setTimeout(loadRefMap, 3000); }
}

function computeBounds() {
  if (!refMapData) return;
  const lb = refMapData.left_bnd  || [];
  const rb = refMapData.right_bnd || [];
  const allX = [...refMapData.x, ...lb.map(p=>p[0]), ...rb.map(p=>p[0])];
  const allY = [...refMapData.y, ...lb.map(p=>p[1]), ...rb.map(p=>p[1])];
  mapBounds = {
    minX: Math.min(...allX), maxX: Math.max(...allX),
    minY: Math.min(...allY), maxY: Math.max(...allY),
  };
}

function toC(x, y, W, H, pad) {
  if (!mapBounds) return [pad, pad];
  const sx = (W - pad*2) / (mapBounds.maxX - mapBounds.minX);
  const sy = (H - pad*2) / (mapBounds.maxY - mapBounds.minY);
  return [(x - mapBounds.minX)*sx + pad, (y - mapBounds.minY)*sy + pad];
}

function drawMap(carPos) {
  if (!mapCtx) return;
  const c = mapCtx.canvas;
  c.width  = c.parentElement ? c.parentElement.clientWidth  : 500;
  c.height = c.parentElement ? c.parentElement.clientHeight : 340;
  const W = c.width, H = c.height, pad = 16;

  mapCtx.fillStyle = '#0a0a0a';
  mapCtx.fillRect(0, 0, W, H);

  if (!refMapData) {
    mapCtx.fillStyle = '#2a2a2a';
    mapCtx.font = '12px JetBrains Mono, monospace';
    mapCtx.textAlign = 'center';
    mapCtx.fillText('Loading track data...', W/2, H/2);
    return;
  }

  const xs = refMapData.x, ys = refMapData.y;
  const lb = refMapData.left_bnd  || [];
  const rb = refMapData.right_bnd || [];

  // Track fill + walls
  if (lb.length > 0 && rb.length > 0) {
    // Fill
    mapCtx.beginPath();
    mapCtx.moveTo(...toC(lb[0][0], lb[0][1], W, H, pad));
    for (let i=1;i<lb.length;i++) mapCtx.lineTo(...toC(lb[i][0], lb[i][1], W, H, pad));
    for (let i=rb.length-1;i>=0;i--) mapCtx.lineTo(...toC(rb[i][0], rb[i][1], W, H, pad));
    mapCtx.closePath();
    mapCtx.fillStyle = 'rgba(255,255,255,0.04)';
    mapCtx.fill();
    // Left wall
    mapCtx.beginPath();
    mapCtx.lineWidth=1.5; mapCtx.strokeStyle='rgba(255,255,255,0.25)';
    mapCtx.lineCap='round'; mapCtx.lineJoin='round';
    for (let i=0;i<lb.length;i++) {
      const pt = toC(lb[i][0],lb[i][1],W,H,pad);
      i===0 ? mapCtx.moveTo(...pt) : mapCtx.lineTo(...pt);
    }
    mapCtx.stroke();
    // Right wall
    mapCtx.beginPath();
    mapCtx.lineWidth=1.5; mapCtx.strokeStyle='rgba(255,255,255,0.25)';
    for (let i=0;i<rb.length;i++) {
      const pt = toC(rb[i][0],rb[i][1],W,H,pad);
      i===0 ? mapCtx.moveTo(...pt) : mapCtx.lineTo(...pt);
    }
    mapCtx.stroke();
  } else {
    // Fallback: thick centre line
    mapCtx.beginPath();
    mapCtx.lineWidth=10; mapCtx.strokeStyle='#1e1e1e';
    mapCtx.lineCap='round'; mapCtx.lineJoin='round';
    for (let i=0;i<xs.length;i++) {
      const pt = toC(xs[i],ys[i],W,H,pad);
      i===0 ? mapCtx.moveTo(...pt) : mapCtx.lineTo(...pt);
    }
    mapCtx.stroke();
  }

  // Driven path
  if (pathHistory.length > 1) {
    mapCtx.beginPath();
    mapCtx.lineWidth=3; mapCtx.strokeStyle='#E8002D';
    mapCtx.lineCap='round'; mapCtx.lineJoin='round';
    for (let i=0;i<pathHistory.length;i++) {
      const pt = toC(pathHistory[i][0],pathHistory[i][1],W,H,pad);
      i===0 ? mapCtx.moveTo(...pt) : mapCtx.lineTo(...pt);
    }
    mapCtx.stroke();
  }

  // S/F dot
  const sf = toC(xs[0],ys[0],W,H,pad);
  mapCtx.beginPath(); mapCtx.arc(sf[0],sf[1],5,0,Math.PI*2);
  mapCtx.fillStyle='#FFD700'; mapCtx.fill();

  // Car dot
  if (carPos) {
    const cp = toC(carPos[0],carPos[1],W,H,pad);
    mapCtx.beginPath(); mapCtx.arc(cp[0],cp[1],12,0,Math.PI*2);
    mapCtx.fillStyle='rgba(232,0,45,0.25)'; mapCtx.fill();
    mapCtx.beginPath(); mapCtx.arc(cp[0],cp[1],7,0,Math.PI*2);
    mapCtx.fillStyle='#fff'; mapCtx.fill();
    mapCtx.beginPath(); mapCtx.arc(cp[0],cp[1],4,0,Math.PI*2);
    mapCtx.fillStyle='#E8002D'; mapCtx.fill();
  }
}
window.addEventListener('resize', () => drawMap(_lastCarX !== null ? [_lastCarX, _lastCarZ] : null));

// ── Coaching update ───────────────────────────────────────────────────────────
function updateCoachingUI(c) {
  if (!c) return;
  const sev  = c.severity || 'info';
  const bar  = document.getElementById('coachBar');
  const msg  = document.getElementById('coachMsg');
  const sub  = document.getElementById('coachSub');
  bar.className  = 'coaching-top-bar ' + sev;
  msg.className  = 'coaching-msg ' + sev;
  msg.textContent = c.message  || '';
  sub.textContent = c.sub      || '';
  const delta = c.speed_delta || 0;
  document.getElementById('csYou').textContent  = Math.round(c.cur_speed || 0) + ' km/h';
  document.getElementById('csRef').textContent  = Math.round(c.ref_speed || 0) + ' km/h';
  const dEl = document.getElementById('csDelta');
  dEl.textContent   = (delta >= 0 ? '+' : '') + delta.toFixed(1) + ' km/h';
  dEl.style.color   = delta >= 0 ? 'var(--teal)' : 'var(--red)';
  document.getElementById('lapProg').style.width = (c.lap_pct || 0) + '%';
  if (c.dist_m) document.getElementById('mapDist').textContent = Math.round(c.dist_m) + 'm';
  // Corner ahead
  const ca = document.getElementById('cornerAhead');
  if (c.corner_ahead && c.dist_m) {
    const dist_to = Math.round(c.corner_ahead.dist_m - c.dist_m);
    if (dist_to > 0 && dist_to < 300) {
      ca.style.display = 'block';
      document.getElementById('caName').textContent =
        c.corner_ahead.corner_name + ' — ' + dist_to + 'm ahead';
      const apex = (c.corner_ahead.ref_apex_speed_kmh || 0).toFixed(0);
      const bp   = Math.round((c.corner_ahead.ref_brake_point_m || 0) - c.dist_m);
      document.getElementById('caDetail').textContent =
        'Brake in ' + bp + 'm  ·  Target apex ' + apex + ' km/h';
    } else { ca.style.display = 'none'; }
  } else { ca.style.display = 'none'; }
}

// ── History rendering ─────────────────────────────────────────────────────────
function renderHistory(history, bodyId, totalId) {
  if (!history || history.length === 0) return;
  const times  = history.map(h => {
    const p = h.time.split(':');
    return p.length===2 ? parseFloat(p[0])*60+parseFloat(p[1]) : parseFloat(h.time);
  });
  const bestIdx = times.indexOf(Math.min(...times));
  if (totalId) document.getElementById(totalId).textContent =
    history.length + ' LAP' + (history.length>1?'S':'');
  document.getElementById(bodyId).innerHTML = history.slice().reverse().map((h,i) => {
    const origIdx = history.length-1-i;
    const isBest  = origIdx===bestIdx && history.length>1;
    return `<div class="history-row" style="${isBest?'border-left:2px solid var(--teal);padding-left:14px':''}">
      <div class="lap" style="${isBest?'color:var(--teal)':''}">LAP ${h.lap}${isBest?' ★':''}</div>
      <div class="ht" style="${isBest?'color:var(--teal)':''}">${h.time}</div>
      <div class="hs">${h.samples.toLocaleString()} pts</div>
      <a class="dl" href="/download/${h.lap}" download="ac_lap${h.lap}.csv">↓ CSV</a>
    </div>`;
  }).join('');
}

// ── Main poll ─────────────────────────────────────────────────────────────────
function gearLabel(g){return g<=0?'R':String(g);}
function fmt(ms){if(ms<=0)return'0:00.000';const m=ms/60000|0,s=(ms%60000)/1000;return m+':'+s.toFixed(3).padStart(6,'0');}

async function poll() {
  try {
    const res  = await fetch('/state');
    const data = await res.json();

    const dot  = document.getElementById('dot');
    const stxt = document.getElementById('statusText');
    const lb   = document.getElementById('lapBadge');
    const wMsg = document.getElementById('waitingMsg');
    const rUI  = document.getElementById('recordingUI');
    const wHis = document.getElementById('waitingHistory');
    const mLink= document.getElementById('macLink');

    if (data.mac_ip) mLink.textContent = data.mac_ip + ':' + data.mac_port;

    dot.className  = 'status-dot '  + data.status;
    stxt.className = 'status-text ' + data.status;
    lb.textContent = 'LAP ' + data.lap_num;

    if (data.status === 'waiting') {
      const isFirst = data.lap_num <= 1;
      stxt.textContent = isFirst
        ? 'Waiting for start/finish line...'
        : 'Lap ' + (data.lap_num-1) + ' complete — waiting for lap ' + data.lap_num;
      wMsg.style.display = isFirst ? 'block' : 'none';
      rUI.style.display  = isFirst ? 'none'  : 'block';
      wHis.style.display = isFirst ? 'block' : 'none';
      pathHistory.length = 0;
      _lastCarX = null; _lastCarZ = null;
      if (refMapData) drawMap(null);
      sampleHistory.length = 0;
    } else if (data.status === 'recording' || data.status === 'sending' || data.status === 'done') {
      stxt.textContent = data.status==='recording' ? '● RECORDING' :
                         data.status==='sending'   ? 'Sending to Mac...' : '✓ Lap complete';
      wMsg.style.display = 'none';
      rUI.style.display  = 'block';
      wHis.style.display = 'none';

      document.getElementById('heroTime').textContent    = data.cur_time   || '0:00.000';
      document.getElementById('heroSpeed').textContent   = Math.round(data.speed || 0);
      document.getElementById('heroSamples').textContent = (data.samples||0).toLocaleString();
      document.getElementById('heroGear').textContent    = gearLabel(data.gear || 0);
      const thr = Math.round((data.throttle||0)*100);
      const brk = Math.round((data.brake||0)*100);
      document.getElementById('gThrottle').textContent    = thr + '%';
      document.getElementById('gBrake').textContent       = brk + '%';
      document.getElementById('gThrottleFill').style.width = thr + '%';
      document.getElementById('gBrakeFill').style.width   = brk + '%';

      // Coaching
      if (data.coaching) updateCoachingUI(data.coaching);

      // Map — use reference GPS coordinates (projected via nearest-point matching)
      // This correctly places the car on the track regardless of AC coordinate system
      const c = data.coaching;
      if (c && c.ref_gps_x !== null && c.ref_gps_y !== null) {
        const gx = c.ref_gps_x, gy = c.ref_gps_y;
        if (_lastCarX === null || Math.abs(gx-_lastCarX)>0.5 || Math.abs(gy-_lastCarZ)>0.5) {
          pathHistory.push([gx, gy]);
          if (pathHistory.length > 1500) pathHistory.shift();
          _lastCarX = gx; _lastCarZ = gy;
        }
        drawMap([gx, gy]);
      }
    }

    // History (both panels)
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

# Store CSVs per lap for individual download
lap_csvs = {}

# Reference lap data for real-time coaching
ref_data = {
    "loaded": False,
    "x": [], "y": [], "dist_m": [],
    "speed_kmh": [], "throttle": [], "brake": [],
    "corners": [],
    "total_dist": 0,
    "left_bnd": [],
    "right_bnd": [],
}

# Live coaching state
coaching_state = {
    "message":    "Waiting for lap...",
    "sub":        "",
    "severity":   "info",   # info | warn | danger | good
    "corner_ahead": None,
    "ref_speed":  0,
    "cur_speed":  0,
    "speed_delta": 0,
    "dist_m":     0,
    "lap_pct":    0,
}


def load_reference(mac_ip, port):
    """Fetch reference lap data from Mac server.
    Boundaries are fetched separately so map shows even before lap processing."""
    # Step 1: always try to load boundaries (available immediately on server start)
    try:
        r_bnd = requests.get(f"http://{mac_ip}:{port}/api/boundaries", timeout=5)
        bnd   = r_bnd.json()
        if bnd.get("ok"):
            ref_data["left_bnd"]  = bnd.get("left_bnd",  [])
            ref_data["right_bnd"] = bnd.get("right_bnd", [])
            print(f"  Boundaries loaded: {len(ref_data['left_bnd'])} left, "
                  f"{len(ref_data['right_bnd'])} right points")
    except Exception as e:
        print(f"  Boundaries not available: {e}")

    # Step 2: try to load full reference lap
    try:
        r = requests.get(f"http://{mac_ip}:{port}/api/reference", timeout=10)
        data = r.json()
        if "error" in data:
            print(f"  Reference lap not ready yet: {data['error']}")
            # Return True if we at least have boundaries for the map
            return len(ref_data["left_bnd"]) > 0
        ref_data["x"]         = data["x"]
        ref_data["y"]         = data["y"]
        ref_data["dist_m"]    = data["dist_m"]
        ref_data["speed_kmh"] = data["speed_kmh"]
        ref_data["throttle"]  = data["throttle"]
        ref_data["brake"]     = data["brake"]
        ref_data["corners"]   = data.get("corners", [])
        ref_data["total_dist"]= data.get("total_dist", 3425)
        if data.get("left_bnd"):  ref_data["left_bnd"]  = data["left_bnd"]
        if data.get("right_bnd"): ref_data["right_bnd"] = data["right_bnd"]
        ref_data["loaded"]    = True
        print(f"  Reference loaded: {len(ref_data['x'])} points, "
              f"{len(ref_data['corners'])} corners, "
              f"{len(ref_data['left_bnd'])} boundary points")
        return True
    except Exception as e:
        print(f"  Could not load reference lap: {e}")
        return len(ref_data["left_bnd"]) > 0


_last_ref_i = 0  # cache last position for fast search

def find_nearest_ref(car_x, car_z):
    """Find index of closest reference point. Uses windowed search from last position."""
    global _last_ref_i
    if not ref_data["loaded"] or not ref_data["x"]:
        return -1
    xs, ys = ref_data["x"], ref_data["y"]
    n = len(xs)
    # Search a window of ±100 points around last known position (car moves ~5pts per sample)
    window = 100
    start = max(0, _last_ref_i - 10)
    end   = min(n, _last_ref_i + window)
    best_i, best_d = start, float("inf")
    for i in range(start, end):
        d = (xs[i] - car_x) ** 2 + (ys[i] - car_z) ** 2
        if d < best_d:
            best_d, best_i = d, i
    # If near end of lap, also check beginning (for lap rollover)
    if _last_ref_i > n - window:
        for i in range(0, min(window, n)):
            d = (xs[i] - car_x) ** 2 + (ys[i] - car_z) ** 2
            if d < best_d:
                best_d, best_i = d, i
    _last_ref_i = best_i
    return best_i


def update_coaching(car_x, car_z, speed_kmh, throttle, brake):
    """Real-time coaching comparing current telemetry to reference."""
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
    # Store GPS position on reference line for accurate map dot
    coaching_state["ref_gps_x"]   = round(ref_data["x"][ref_i], 2)
    coaching_state["ref_gps_y"]   = round(ref_data["y"][ref_i], 2)

    # Find nearest corner ahead (within 400m)
    next_corner = None
    for c in ref_data["corners"]:
        dtc = c["dist_m"] - cur_dist
        if 10 < dtc < 400:
            next_corner = c
            break
    coaching_state["corner_ahead"] = next_corner

    msg, sub, sev = "On pace", f"{speed_kmh:.0f} vs {ref_speed:.0f} km/h", "info"

    # Priority 1: active braking zone - ref is braking hard but we're not
    if ref_brake > 0.5 and brake < 0.1:
        msg = f"BRAKE NOW — {ref_speed:.0f}→{ref_speed*0.6:.0f} km/h"
        sub = f"Reference braking hard here — {speed_kmh:.0f} km/h entry"
        sev = "danger"

    # Priority 2: corner approach with brake point info
    elif next_corner:
        name  = next_corner["corner_name"]
        dist_to_brake = next_corner.get("ref_brake_point_m", next_corner["dist_m"] - 80) - cur_dist
        ref_apex = next_corner.get("ref_apex_speed_kmh", 0)
        ref_entry= next_corner.get("ref_entry_speed_kmh", 0)

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

    # Priority 3: missed throttle pickup
    elif ref_throttle > 0.85 and throttle < 0.5 and speed_kmh > 60:
        gap = (ref_throttle - throttle) * 100
        msg = f"MORE THROTTLE — {throttle*100:.0f}% vs {ref_throttle*100:.0f}%"
        sub = f"A2RL is at full throttle here — you are {gap:.0f}% behind"
        sev = "warn"

    # Priority 4: big speed deficit
    elif speed_delta < -25:
        msg = f"−{abs(speed_delta):.0f} km/h DEFICIT"
        sub = f"You: {speed_kmh:.0f}  A2RL: {ref_speed:.0f} — carry more speed"
        sev = "danger"
    elif speed_delta < -12:
        msg = f"−{abs(speed_delta):.0f} km/h — carry more speed"
        sub = f"You: {speed_kmh:.0f}  A2RL: {ref_speed:.0f} km/h"
        sev = "warn"

    # Priority 5: faster than reference
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


def send_to_mac(csv_text, mac_ip, port, lap_num, ac_lap_time_ms=None):
    url   = f"http://{mac_ip}:{port}/upload"
    fname = f"ac_lap{lap_num}.csv"
    files = {"file": (fname, csv_text.encode("utf-8"), "text/csv")}
    # Send the actual AC lap time so server uses it directly (bypasses computed time)
    form  = {"ac_lap_time_ms": str(ac_lap_time_ms)} if ac_lap_time_ms else {}
    try:
        resp = requests.post(url, files=files, data=form, timeout=60)
        data = resp.json()
        return data.get("ok", False), data.get("lap_time"), data.get("samples")
    except Exception as e:
        print(f"  Send failed: {e}")
        return False, None, None


# ── Mini HTTP server for live UI ──────────────────────────────────────────────

class UIHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Suppress access logs

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self._send(200, "text/html", LIVE_HTML.encode())

        elif self.path == "/state":
            self._send(200, "application/json",
                       json.dumps(state).encode())

        elif self.path == "/ref_map":
            has_bnd = len(ref_data.get("left_bnd", [])) > 0
            has_ref = len(ref_data.get("x", [])) > 0
            payload = json.dumps({
                "ok":        has_bnd or has_ref,
                "x":         ref_data.get("x", []),
                "y":         ref_data.get("y", []),
                "left_bnd":  ref_data.get("left_bnd", []),
                "right_bnd": ref_data.get("right_bnd", []),
            }).encode()
            self._send(200, "application/json", payload)

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

    def _send(self, code, ctype, body, disposition=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if disposition:
            self.send_header("Content-Disposition", disposition)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def start_ui_server(port=9000):
    import socket
    # Try binding to check port is free
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
    print("=" * 55)
    print("  AI Race Engineer — AC Lap Recorder")
    print("=" * 55)

    if sys.platform != "win32":
        print("ERROR: Must run on Windows where Assetto Corsa is installed.")
        sys.exit(1)

    print()
    mac_ip = input("Mac IP address (e.g. 192.168.1.10): ").strip()
    port   = 8080

    state["mac_ip"]   = mac_ip
    state["mac_port"] = port

    # Start live UI server
    UI_PORT = 9000
    try:
        start_ui_server(UI_PORT)
        print(f"\nLive UI started on port {UI_PORT}")
    except Exception as e:
        print(f"\nWARNING: Could not start UI server: {e}")
        print("This may be a firewall issue. Try allowing Python in Windows Firewall.")
        UI_PORT = None

    if UI_PORT:
        import webbrowser
        webbrowser.open(f"http://localhost:{UI_PORT}")
        print(f"Opening browser at http://localhost:{UI_PORT}")
        print(f"If browser doesn't open, go there manually\n")

    # Test Mac connection and load reference
    print(f"Testing Mac connection...", end="", flush=True)
    try:
        requests.get(f"http://{mac_ip}:{port}/", timeout=5)
        print(" Connected ✓")
        print(f"Loading reference lap from Mac...", end="", flush=True)
        if load_reference(mac_ip, port):
            print(" Loaded ✓")
        else:
            print(" Not available yet — reference loads after first lap processed")
            print("  Tip: run test.py on Mac first to pre-generate fast_laps.json")
    except Exception:
        print(f" Cannot reach {mac_ip}:{port} — make sure server.py is running on Mac")

    # Connect to AC shared memory
    print("\nConnecting to Assetto Corsa...", end="", flush=True)
    try:
        phys_mm  = mmap.mmap(-1, ctypes.sizeof(SPageFilePhysics),  "Local\\acpmf_physics")
        graph_mm = mmap.mmap(-1, ctypes.sizeof(SPageFileGraphic), "Local\\acpmf_graphics")
        print(" Connected ✓")
    except Exception as e:
        print(f"\nERROR: {e}")
        print("Make sure Assetto Corsa is running and you are in a session.")
        input("Press ENTER to exit...")
        sys.exit(1)

    def read_p():
        phys_mm.seek(0)
        return SPageFilePhysics.from_buffer_copy(
            phys_mm.read(ctypes.sizeof(SPageFilePhysics)))

    def read_g():
        graph_mm.seek(0)
        return SPageFileGraphic.from_buffer_copy(
            graph_mm.read(ctypes.sizeof(SPageFileGraphic)))

    print("Waiting for active session", end="", flush=True)
    while read_g().status != 2:
        print(".", end="", flush=True)
        time.sleep(0.5)
    print(" Ready!")
    if UI_PORT:
        print(f"\nUI: http://localhost:{UI_PORT}")
    print("Drive a lap — recording starts automatically at S/F line\n")

    lap_num = 0

    while True:
        lap_num += 1
        state["status"]   = "waiting"
        state["lap_num"]  = lap_num
        state["samples"]  = 0
        state["lap_time"] = None

        print(f"{'─'*55}")
        print(f"  LAP {lap_num} — Waiting for S/F line...")

        # Retry loading reference if not loaded yet
        if not ref_data["loaded"]:
            print(f"  Retrying reference load...", end="", flush=True)
            if load_reference(mac_ip, port):
                print(" Loaded ✓")
            else:
                print(" Still not available")

        records   = []
        recording = False
        prev_laps = read_g().completedLaps
        prev_t    = 0
        last_tick = 0.0
        INTERVAL  = 0.02

        # Pit start detection: check completedLaps OR if car is physically in pit area
        g_init    = read_g()
        p_init    = read_p()
        in_pit    = (g_init.isInPit == 1 or g_init.isInPitLane == 1)
        first_lap = (g_init.completedLaps == 0)
        pit_start = first_lap or in_pit

        # If previous lap just ended by crossing S/F, start next lap immediately
        if 'auto_start_next' in dir() and auto_start_next:
            recording = True
            records   = []
            state["status"] = "recording"
            auto_start_next = False
            print(f"  ● Recording started (auto — continued from lap {lap_num - 1})")
        elif pit_start:
            reason = "first lap of session" if first_lap else "car detected in pit lane"
            print(f"  Pit start detected ({reason}) — recording when car moves")
            state["status"] = "waiting"
            auto_start_next = False
        else:
            print(f"  Track start — waiting for S/F line crossing")
            auto_start_next = False

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

                # Detect lap start
                if not recording:
                    if pit_start:
                        # Pit start: begin as soon as car starts moving out of pit
                        if p.speedKmh > 5 and cur_t > 0:
                            recording = True
                            records   = []
                            state["status"] = "recording"
                            print(f"  ● Recording started (pit start)")
                    else:
                        # Normal start: timer resets crossing S/F line
                        if cur_t > 0 and cur_t < 3000 and prev_t > 5000:
                            recording = True
                            records   = []
                            state["status"] = "recording"
                            print(f"  ● Recording started")
                    if cur_t > 0:
                        prev_t = cur_t

                if recording:
                    records.append(take_sample(p, g))

                    # Update shared state for UI (every sample)
                    state["samples"]  = len(records)
                    state["speed"]    = round(p.speedKmh, 1)
                    state["gear"]     = p.gear
                    # Show time from recording start, not session start
                    rec_t = cur_t - records[0]["LapTimeCurrent"] if records else 0
                    state["cur_time"] = fmt(max(0, rec_t))
                    state["throttle"] = round(p.gas, 3)
                    state["brake"]    = round(p.brake, 3)

                    # Always update car position (every sample)
                    state["car_x"] = round(g.carCoordinates[0], 2)
                    state["car_z"] = round(g.carCoordinates[2], 2)

                    # Real-time coaching (every 5 samples = 10Hz)
                    if len(records) % 5 == 0 and ref_data["loaded"]:
                        cx = g.carCoordinates[0]
                        cz = g.carCoordinates[2]
                        update_coaching(cx, cz, p.speedKmh, p.gas, p.brake)
                        state["coaching"] = coaching_state.copy()

                    if len(records) % 100 == 0:
                        print(f"\r  ● {fmt(cur_t)}  "
                              f"{p.speedKmh:.0f} km/h  "
                              f"G{p.gear}  "
                              f"{len(records)} samples   ",
                              end="", flush=True)

                    # Lap complete
                    if g.completedLaps > prev_laps and len(records) > 100:
                        lap_ms = g.iLastTime if g.iLastTime > 0 else cur_t
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
                        break

                    # Fallback timer reset
                    if len(records) > 300 and cur_t < 1000:
                        if records[-1]["LapTimeCurrent"] > 20000:
                            lap_ms = records[-1]["LapTimeCurrent"]
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
                            break

                prev_laps = g.completedLaps

        except KeyboardInterrupt:
            print("\nStopped.")
            break

        if records:
            csv_text = to_csv(records)

            # Store for UI download
            lap_csvs[lap_num] = csv_text

            # Save to Desktop
            local = save_to_desktop(csv_text, lap_num)
            print(f"  Saved: {Path(local).name}")

            # Update history immediately so UI shows it
            lap_entry = {
                "lap":     lap_num,
                "time":    state["lap_time"] or "?",
                "samples": len(records),
            }
            state["history"].append(lap_entry)
            state["status"] = "sending"

            # Send to Mac in background so recording can restart immediately
            def send_async(csv_text=csv_text, lap_num=lap_num, lap_ms=lap_ms):
                ok, lt, samps = send_to_mac(csv_text, mac_ip, port, lap_num,
                                            ac_lap_time_ms=lap_ms)
                if ok:
                    print(f"\n  Lap {lap_num} sent to Mac OK")
                    print(f"  Dashboard: http://{mac_ip}:{port}/dashboard")
                else:
                    print(f"\n  Lap {lap_num} send failed — file saved to Desktop")
                state["status"] = "waiting"

            threading.Thread(target=send_async, daemon=True).start()

        else:
            state["status"] = "waiting"

        # Immediately start recording next lap — no need to wait for S/F
        # because we just crossed it to end the previous lap
        print(f"  Starting lap {lap_num + 1} recording immediately...")
        auto_start_next = True  # flag to skip S/F detection for next lap

    phys_mm.close()
    graph_mm.close()
    print("\nDone.")
    input("Press ENTER to exit...")


if __name__ == "__main__":
    main()
