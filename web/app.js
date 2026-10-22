'use strict';

const $ = id => document.getElementById(id);

// Connection + telemetry state.
let socket, role = 'observer', replay = false, linked = false, reconnectTimer;
let pose = { x: 1.5, y: 4, theta: 0, v: 0, w: 0 };
let ranges = [], trail = [], keys = new Set();
let world = { width: 12, height: 8, rectangles: [], circles: [] };
let lastFrame = 0, lastHeld = false, networkTimer;
const pendingPings = new Map();

const canvas = $('arena'), ctx = canvas.getContext('2d');

async function api(path, body) {
  const response = await fetch('/api/' + path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

function report(error) {
  $('record-status').textContent = error.message;
}

function send(topic, data) {
  if (socket?.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ topic, stamp: Date.now() / 1000, data }));
  }
}

function badge(id, text, state) {
  $(id).textContent = '● ' + text;
  $(id).className = 'status ' + state;
}

// ---------- WebSocket connection ----------

function connect() {
  socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);

  socket.onopen = () => {
    linked = true;
    badge('link-status', 'LINK ONLINE', 'good');
  };

  socket.onclose = () => {
    linked = false;
    keys.clear();
    pendingPings.clear();
    lastHeld = false;
    badge('link-status', 'LINK OFFLINE', 'bad');
    badge('deadman-status', 'DEADMAN RELEASED', '');
    $('role').textContent = 'DISCONNECTED';
    clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(connect, 1500);
  };

  socket.onerror = () => socket.close();

  socket.onmessage = event => {
    const { topic, data: d } = JSON.parse(event.data);

    if (topic === '/odom') {
      pose = d;
      if (!trail.length || Math.hypot(d.x - trail.at(-1).x, d.y - trail.at(-1).y) > .025) {
        trail.push({ x: d.x, y: d.y });
        if (trail.length > 1200) trail.shift();
      }
      $('velocity').innerHTML = `${d.v.toFixed(2)}<small> m/s</small>`;
      $('angular').innerHTML = `${d.w.toFixed(2)}<small> rad/s</small>`;
      $('position').innerHTML = `${d.x.toFixed(2)} / ${d.y.toFixed(2)}<small> m</small>`;
      $('heading').textContent = (d.theta * 180 / Math.PI).toFixed(1) + '°';
      $('speed-meter').style.width = Math.abs(d.v) * 100 + '%';
    } else if (topic === '/scan') {
      ranges = d.ranges;
    } else if (topic === '/battery_state') {
      $('battery').innerHTML = `${d.percentage.toFixed(1)}<small> %</small>`;
      $('voltage').textContent = d.voltage.toFixed(2) + ' V';
    } else if (topic === '/diagnostics') {
      if (d.role) {
        role = d.role;
        $('role').textContent = role.toUpperCase();
      }
      if (d.estop !== undefined) {
        badge('estop-status', d.estop ? 'E-STOP LATCHED' : 'E-STOP CLEAR', d.estop ? 'bad' : 'good');
        badge('deadman-status', d.deadman ? 'DEADMAN HELD' : 'DEADMAN RELEASED', d.deadman ? 'good' : '');
        badge('watchdog-status', d.watchdog_tripped ? 'WATCHDOG STALE' : 'WATCHDOG ARMED', d.watchdog_tripped ? 'warn' : 'good');
        $('dropped').textContent = d.dropped_cmds;
        if (replay !== d.replay) trail = [];
        replay = d.replay;
        $('replay-banner').hidden = !replay;
      }
      if (d.reason && d.reason !== 'Connected') $('record-status').textContent = d.reason;
    } else if (topic === '/pong' && pendingPings.has(d.id)) {
      const elapsed = performance.now() - pendingPings.get(d.id);
      pendingPings.delete(d.id);
      $('rtt').innerHTML = `${Math.round(elapsed)}<small> ms</small>`;
    }
  };
}

// ---------- Operator input (keyboard + gamepad) ----------

function input() {
  const pad = [...navigator.getGamepads?.() || []].find(Boolean);
  $('gamepad').textContent = pad ? 'Gamepad: connected' : 'Gamepad: waiting';

  const typing = ['INPUT', 'SELECT', 'BUTTON', 'TEXTAREA'].includes(document.activeElement?.tagName);
  const held = document.hasFocus() && !document.hidden &&
    ((keys.has('ShiftLeft') || keys.has('ShiftRight')) || !!pad?.buttons[0]?.pressed);

  let linear = typing ? 0 : ((keys.has('KeyW') || keys.has('ArrowUp') ? 1 : 0) - (keys.has('KeyS') || keys.has('ArrowDown') ? 1 : 0)) * .8;
  let angular = typing ? 0 : ((keys.has('KeyA') || keys.has('ArrowLeft') ? 1 : 0) - (keys.has('KeyD') || keys.has('ArrowRight') ? 1 : 0)) * 1.5;

  if (pad) {
    if (Math.abs(pad.axes[1] || 0) > .12) linear = -pad.axes[1] * .8;
    if (Math.abs(pad.axes[0] || 0) > .12) angular = -pad.axes[0] * 1.5;
  }

  return { linear: held ? linear : 0, angular: held ? angular : 0, deadman: held };
}

function release() {
  keys.clear();
  lastHeld = false;
  if (role === 'operator') send('/cmd_vel', { linear: 0, angular: 0, deadman: false });
}

window.addEventListener('keydown', e => {
  if (['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Space'].includes(e.code) && !['INPUT', 'SELECT'].includes(e.target.tagName)) {
    e.preventDefault();
  }
  keys.add(e.code);
});

window.addEventListener('keyup', e => {
  keys.delete(e.code);
  if (e.code.startsWith('Shift') && role === 'operator') send('/cmd_vel', input());
});

window.addEventListener('blur', release);
document.addEventListener('visibilitychange', () => { if (document.hidden) release(); });
window.addEventListener('pagehide', release);

// Command loop: send held input at 20 Hz while this client is the operator.
setInterval(() => {
  const command = input();
  if (linked && role === 'operator' && !replay) send('/cmd_vel', command);
  lastHeld = command.deadman;
}, 50);

// Ping loop: measure round-trip time once a second, expiring stale pings.
setInterval(() => {
  if (!linked) return;
  const id = crypto.randomUUID();
  pendingPings.set(id, performance.now());
  for (const [key, t] of pendingPings) {
    if (performance.now() - t > 10000) pendingPings.delete(key);
  }
  send('/ping', { id, t: Date.now() });
}, 1000);

// ---------- UI controls ----------

$('estop').onclick = () => { release(); api('estop', {}).catch(report); };
$('reset').onclick = () => { release(); trail = []; api('reset', {}).catch(report); };

for (const name of ['latency_ms', 'jitter_ms', 'loss_pct']) {
  $(name).oninput = () => {
    $(name + '-value').value = $(name).value + (name === 'loss_pct' ? ' %' : ' ms');
    clearTimeout(networkTimer);
    networkTimer = setTimeout(() => {
      api('network', Object.fromEntries(['latency_ms', 'jitter_ms', 'loss_pct'].map(key => [key, +$(key).value]))).catch(report);
    }, 80);
  };
}

async function refreshRecordings() {
  const items = await api('recordings');
  $('recordings').replaceChildren(
    new Option('Select a recording', ''),
    ...items.map(item => new Option(`${item.id.slice(0, 8)} / ${item.message_count} msgs`, item.id)),
  );
}

$('record').onclick = () => api('recordings/start', {})
  .then(d => { $('record-status').textContent = '● Recording ' + d.id.slice(0, 8); })
  .catch(report);

$('stop-record').onclick = () => api('recordings/stop', {})
  .then(() => { $('record-status').textContent = 'Recording saved'; return refreshRecordings(); })
  .catch(report);

$('replay').onclick = () => {
  const id = $('recordings').value;
  if (!id) { $('record-status').textContent = 'Select a recording first'; return; }
  release();
  api('replay/' + id, {})
    .then(() => { $('record-status').textContent = 'Playing recorded telemetry'; trail = []; })
    .catch(report);
};

$('stop-replay').onclick = () => api('replay/stop', {})
  .then(() => { $('record-status').textContent = 'Live session'; trail = []; })
  .catch(report);

// ---------- Arena rendering ----------

function render(now) {
  requestAnimationFrame(render);
  if (now - lastFrame < 30) return;
  lastFrame = now;

  const rect = canvas.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
  if (canvas.width !== Math.round(rect.width * dpr) || canvas.height !== Math.round(rect.height * dpr)) {
    canvas.width = Math.round(rect.width * dpr);
    canvas.height = Math.round(rect.height * dpr);
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const W = rect.width, H = rect.height;
  ctx.clearRect(0, 0, W, H);

  // World-to-canvas projection: fit the arena inside the canvas with a margin.
  const s = Math.min((W - 60) / world.width, (H - 75) / world.height);
  const ox = (W - world.width * s) / 2, oy = (H - world.height * s) / 2;
  const point = (x, y) => [ox + x * s, H - oy - y * s];

  // Grid lines.
  ctx.strokeStyle = '#1c2a33';
  ctx.lineWidth = 1;
  for (let x = 0; x <= world.width; x++) {
    ctx.beginPath();
    ctx.moveTo(...point(x, 0));
    ctx.lineTo(...point(x, world.height));
    ctx.stroke();
  }
  for (let y = 0; y <= world.height; y++) {
    ctx.beginPath();
    ctx.moveTo(...point(0, y));
    ctx.lineTo(...point(world.width, y));
    ctx.stroke();
  }

  // Arena border.
  ctx.strokeStyle = '#405462';
  ctx.strokeRect(ox, oy, world.width * s, world.height * s);

  // Obstacles.
  ctx.fillStyle = '#243640';
  ctx.strokeStyle = '#49616f';
  for (const [x, y, w, h] of world.rectangles) {
    const [px, py] = point(x, y + h);
    ctx.fillRect(px, py, w * s, h * s);
    ctx.strokeRect(px, py, w * s, h * s);
  }
  for (const [x, y, r] of world.circles) {
    ctx.beginPath();
    ctx.arc(...point(x, y), r * s, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
  }

  // Lidar overlay.
  if ($('lidar').checked && ranges.length) {
    const origin = point(pose.x, pose.y);
    for (let i = 0; i < ranges.length; i++) {
      const angle = pose.theta - Math.PI + i * 2 * Math.PI / ranges.length;
      const end = point(pose.x + Math.cos(angle) * ranges[i], pose.y + Math.sin(angle) * ranges[i]);
      ctx.strokeStyle = '#64d9d216';
      ctx.beginPath();
      ctx.moveTo(...origin);
      ctx.lineTo(...end);
      ctx.stroke();
      ctx.fillStyle = '#64d9d280';
      ctx.fillRect(end[0] - 1, end[1] - 1, 2, 2);
    }
  }

  // Trail.
  ctx.beginPath();
  ctx.strokeStyle = '#b6f36d80';
  ctx.lineWidth = 2;
  trail.forEach((p, i) => {
    const xy = point(p.x, p.y);
    if (i) ctx.lineTo(...xy); else ctx.moveTo(...xy);
  });
  ctx.stroke();

  // Robot body + heading marker.
  const [rx, ry] = point(pose.x, pose.y);
  ctx.save();
  ctx.translate(rx, ry);
  ctx.rotate(-pose.theta);
  ctx.shadowColor = '#b6f36d';
  ctx.shadowBlur = 15;
  ctx.fillStyle = '#b6f36d';
  ctx.beginPath();
  ctx.arc(0, 0, .22 * s, 0, Math.PI * 2);
  ctx.fill();
  ctx.shadowBlur = 0;
  ctx.fillStyle = '#162518';
  ctx.beginPath();
  ctx.moveTo(.3 * s, 0);
  ctx.lineTo(-.1 * s, -.12 * s);
  ctx.lineTo(-.1 * s, .12 * s);
  ctx.closePath();
  ctx.fill();
  ctx.restore();

  ctx.fillStyle = '#b6f36d';
  ctx.font = '10px monospace';
  ctx.fillText('R01', rx + 15, ry - 15);

  $('clock').textContent = new Date().toLocaleTimeString('en-GB');
}

// ---------- Boot ----------

function applyNetworkSettings(network) {
  for (const name of ['latency_ms', 'jitter_ms', 'loss_pct']) {
    if (network[name] === undefined) continue;
    $(name).value = network[name];
    $(name + '-value').value = network[name] + (name === 'loss_pct' ? ' %' : ' ms');
  }
}

api('state').then(d => {
  world = d.world;
  if (d.network) applyNetworkSettings(d.network);
}).catch(report);
refreshRecordings().catch(report);
connect();
requestAnimationFrame(render);
